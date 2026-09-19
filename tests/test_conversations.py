"""Conversation behavior at the HTTP and persisted chat seams."""

import json
from pathlib import Path

import httpx2
import pytest
from pydantic_ai.messages import (
    ModelMessage,
    ModelRequest,
    ModelResponse,
    TextPart,
    ToolCallPart,
    UserPromptPart,
)

from course_harness.app import create_app
from course_harness.chat_history import (
    chat_session_path,
    read_chat_history,
    save_chat_history,
)


@pytest.mark.anyio
async def test_new_conversation_reuses_one_empty_draft(tmp_path: Path) -> None:
    workspace = tmp_path / "course"
    workspace.mkdir()
    store = tmp_path / "chat"
    app = create_app(workspace, chat_store_path=store)
    async with httpx2.AsyncClient(
        transport=httpx2.ASGITransport(app=app), base_url="http://test"
    ) as client:
        first = (await client.get("/api/conversations")).json()["active_id"]
        for _ in range(3):
            catalog = (await client.post("/api/conversations", json={})).json()
            assert catalog["active_id"] == first
            assert len(catalog["conversations"]) == 1
        named = (await client.post("/api/conversations", json={"title": "Named draft"})).json()
        assert named["active_id"] == first
        assert named["conversations"][0]["title"] == "Named draft"

        save_chat_history(
            store,
            workspace,
            [ModelRequest(parts=[UserPromptPart(content="Plan the first lecture")])],
            first,
        )
        second = (await client.post("/api/conversations", json={})).json()["active_id"]
        assert second != first
        assert (await client.post("/api/conversations", json={})).json()["active_id"] == second
        await client.post(f"/api/conversations/{first}/activate")
        reused = (await client.post("/api/conversations", json={})).json()
        assert reused["active_id"] == second
        assert len(reused["conversations"]) == 2


@pytest.mark.anyio
async def test_course_author_can_keep_and_reopen_conversations(tmp_path: Path) -> None:
    workspace = tmp_path / "course"
    workspace.mkdir()
    store = tmp_path / "chat"
    app = create_app(workspace, chat_store_path=store)
    async with httpx2.AsyncClient(
        transport=httpx2.ASGITransport(app=app), base_url="http://test"
    ) as client:
        initial = (await client.get("/api/conversations")).json()
        first = initial["active_id"]
        save_chat_history(
            store,
            workspace,
            [ModelRequest(parts=[UserPromptPart(content="First idea")])],
            first,
        )
        created = await client.post("/api/conversations", json={"title": "Second idea"})
        assert created.status_code == 201
        second = created.json()["active_id"]
        assert second != first
        assert (await client.get("/api/chat")).json()["messages"] == []
        assert (await client.get(f"/api/conversations/{first}")).json()["messages"][0][
            "content"
        ] == "First idea"

        reopened = await client.post(f"/api/conversations/{first}/activate")
        assert reopened.status_code == 200
        assert reopened.json()["active_id"] == first
        assert (await client.get("/api/chat")).json()["messages"][0]["content"] == "First idea"
        assert (
            await client.patch(f"/api/conversations/{second}", json={"archived": True})
        ).status_code == 200
        assert (await client.post(f"/api/conversations/{second}/activate")).status_code == 409
        assert (await client.get(f"/api/conversations/{second}")).status_code == 200
        assert (await client.delete(f"/api/conversations/{second}")).status_code == 200
        assert (await client.get(f"/api/conversations/{second}")).status_code == 404


@pytest.mark.anyio
async def test_compaction_keeps_transcript_and_shrinks_model_history(tmp_path: Path) -> None:
    workspace = tmp_path / "course"
    workspace.mkdir()
    store = tmp_path / "chat"
    app = create_app(workspace, chat_store_path=store)
    async with httpx2.AsyncClient(
        transport=httpx2.ASGITransport(app=app), base_url="http://test"
    ) as client:
        conversation_id = (await client.get("/api/conversations")).json()["active_id"]
        original: list[ModelMessage] = [
            ModelRequest(parts=[UserPromptPart(content=f"Question {i}: " + "x" * 400)])
            for i in range(30)
        ]
        save_chat_history(store, workspace, original, conversation_id)
        preview = await client.post(f"/api/conversations/{conversation_id}/compact/preview")
        assert preview.status_code == 200
        proposal = preview.json()
        assert proposal["source_message_count"] == 30
        assert len(proposal["summary"]) <= 4000
        assert "omitted" in proposal["summary"]
        confirmed = await client.post(
            f"/api/conversations/{conversation_id}/compact",
            json={
                "summary": "Course goal: explain causal graphs.",
                "revision": proposal["revision"],
            },
        )
        assert confirmed.status_code == 200
        assert len(confirmed.json()["messages"]) == 30
        thread_file = next(path for path in store.glob("*/*.json") if path.name != "index.json")
        provenance = json.loads(thread_file.read_text(encoding="utf-8"))["compactions"][0]
        assert provenance["conversation_id"] == conversation_id
        assert provenance["source_revision"] == proposal["revision"]
        assert provenance["source_message_count"] == 30
        assert provenance["summary"] == "Course goal: explain causal graphs."
        assert len(read_chat_history(store, workspace, conversation_id)) == 1
        assert "Course goal" in str(read_chat_history(store, workspace, conversation_id)[0])
        assert (
            await client.post(
                f"/api/conversations/{conversation_id}/compact",
                json={"summary": "Stale", "revision": proposal["revision"]},
            )
        ).status_code == 409

        continued = [
            *read_chat_history(store, workspace, conversation_id),
            ModelRequest(parts=[UserPromptPart(content="Continue")]),
            ModelResponse(parts=[TextPart(content="Done")]),
        ]
        save_chat_history(store, workspace, continued, conversation_id)
        transcript = (await client.get("/api/chat")).json()["messages"]
        assert len(transcript) == 32
        assert transcript[-2]["content"] == "Continue"
        second_preview = (
            await client.post(f"/api/conversations/{conversation_id}/compact/preview")
        ).json()
        second = await client.post(
            f"/api/conversations/{conversation_id}/compact",
            json={"summary": "Reviewed again", "revision": second_preview["revision"]},
        )
        assert len(second.json()["messages"]) == 32


@pytest.mark.anyio
async def test_pending_approval_cannot_be_cleared_archived_deleted_or_compacted(
    tmp_path: Path,
) -> None:
    workspace = tmp_path / "course"
    workspace.mkdir()
    store = tmp_path / "chat"
    app = create_app(workspace, chat_store_path=store)
    async with httpx2.AsyncClient(
        transport=httpx2.ASGITransport(app=app), base_url="http://test"
    ) as client:
        first = (await client.get("/api/conversations")).json()["active_id"]
        save_chat_history(
            store,
            workspace,
            [
                ModelResponse(
                    parts=[ToolCallPart(tool_name="change_course", args={}, tool_call_id="one")]
                )
            ],
            first,
        )
        index_path = next(store.glob("*/index.json"))
        stale_index = json.loads(index_path.read_text(encoding="utf-8"))
        stale_index["conversations"][0]["has_pending_approval"] = False
        index_path.write_text(json.dumps(stale_index), encoding="utf-8")
        catalog = (await client.get("/api/conversations")).json()
        assert (
            next(item for item in catalog["conversations"] if item["id"] == first)[
                "has_pending_approval"
            ]
            is True
        )
        second = (await client.post("/api/conversations", json={})).json()["active_id"]
        assert (await client.post(f"/api/conversations/{first}/activate")).status_code == 200
        assert (await client.delete("/api/chat")).status_code == 409
        assert (
            await client.patch(f"/api/conversations/{first}", json={"archived": True})
        ).status_code == 409
        assert (await client.delete(f"/api/conversations/{first}")).status_code == 409
        assert (await client.post(f"/api/conversations/{first}/compact/preview")).status_code == 409
        assert (await client.post(f"/api/conversations/{second}/activate")).status_code == 200
        assert (await client.get(f"/api/conversations/{first}")).json()["approval"] is not None


@pytest.mark.anyio
async def test_legacy_chat_file_migrates_once(tmp_path: Path) -> None:
    workspace = tmp_path / "course"
    workspace.mkdir()
    store = tmp_path / "chat"
    store.mkdir()
    legacy = chat_session_path(store, workspace)
    legacy.write_text(
        json.dumps(
            {
                "version": 1,
                "history": [
                    {
                        "kind": "request",
                        "parts": [{"part_kind": "user-prompt", "content": "Existing chat"}],
                    }
                ],
                "messages": [{"id": "user-1", "role": "user", "content": "Existing chat"}],
            }
        ),
        encoding="utf-8",
    )
    app = create_app(workspace, chat_store_path=store)
    async with httpx2.AsyncClient(
        transport=httpx2.ASGITransport(app=app), base_url="http://test"
    ) as client:
        catalog = (await client.get("/api/conversations")).json()
        transcript = (await client.get("/api/chat")).json()
    assert len(catalog["conversations"]) == 1
    assert transcript["messages"][0]["content"] == "Existing chat"
    assert not legacy.exists()
    reopened = create_app(workspace, chat_store_path=store)
    async with httpx2.AsyncClient(
        transport=httpx2.ASGITransport(app=reopened), base_url="http://test"
    ) as client:
        assert (await client.get("/api/conversations")).json()["active_id"] == catalog["active_id"]
