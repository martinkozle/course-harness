"""Conversation behavior at the HTTP and persisted chat seams."""

import asyncio
import json
from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any

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
from pydantic_ai.models.function import AgentInfo, FunctionModel

from course_harness.app import create_app
from course_harness.chat_history import (
    chat_session_path,
    clean_generated_title,
    fallback_title,
    list_conversations,
    propose_conversation_title,
    read_chat_history,
    save_chat_history,
    update_conversation,
)
from course_harness.providers import ProviderCapabilities


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


async def _verified_capabilities(_request: object) -> ProviderCapabilities:
    return ProviderCapabilities(
        tool_calling=True,
        structured_output=True,
        streaming=True,
        context_window=131_072,
        vision=False,
    )


async def _replying_model(_messages: list[ModelMessage], _info: AgentInfo) -> AsyncIterator[str]:
    yield "Noted."


def _titled_app(tmp_path: Path, title_model: FunctionModel | None = None) -> Any:
    workspace = tmp_path / "course"
    workspace.mkdir(exist_ok=True)
    return create_app(
        workspace,
        provider_store_path=tmp_path / "provider",
        chat_store_path=tmp_path / "chat",
        agent_model=FunctionModel(stream_function=_replying_model),
        title_model=title_model,
        provider_validator=_verified_capabilities,
    )


async def _send(client: httpx2.AsyncClient, content: str) -> None:
    await client.put(
        "/api/provider",
        json={"kind": "openrouter", "model": "openai/gpt-oss-20b:free", "api_key": "secret"},
    )
    response = await client.post(
        "/api/agent",
        headers={"accept": "text/event-stream"},
        json={
            "threadId": (await client.get("/api/conversations")).json()["active_id"],
            "runId": "title-run",
            "state": {},
            "messages": [{"id": "user-1", "role": "user", "content": content}],
            "tools": [],
            "context": [],
            "forwardedProps": {"mode": "autonomous"},
        },
    )
    assert response.status_code == 200


async def _active_summary(client: httpx2.AsyncClient) -> dict[str, Any]:
    catalog = (await client.get("/api/conversations")).json()
    return next(item for item in catalog["conversations"] if item["id"] == catalog["active_id"])


def test_fallback_title_cuts_the_first_message_at_a_word_boundary() -> None:
    assert fallback_title("  Plan a\n lecture  ") == "Plan a lecture"
    long = "Design an introductory course on causal inference for practising data scientists"
    title = fallback_title(long)
    assert title == "Design an introductory course on causal inference for…"
    assert len(title) <= 61
    assert fallback_title("x" * 100) == "x" * 60 + "…"


def test_generated_titles_are_cleaned_to_one_plain_line() -> None:
    assert clean_generated_title('Title: "Causal Inference Basics."\nextra') == (
        "Causal Inference Basics"
    )
    assert clean_generated_title("  \n ") == ""


@pytest.mark.anyio
async def test_first_message_names_the_conversation_without_a_title_model(
    tmp_path: Path,
) -> None:
    async with httpx2.AsyncClient(
        transport=httpx2.ASGITransport(app=_titled_app(tmp_path)), base_url="http://test"
    ) as client:
        await _send(
            client,
            '[Attachment: resource-0123456789ab image/png "a.png"]\n\n'
            "[Context: Focus on Lecture 2]\n\nOutline the confounding lecture",
        )
        summary = await _active_summary(client)
        assert summary["title"] == "Outline the confounding lecture"
        assert summary["title_source"] == "query"

        await _send(client, "Now add exercises")
        assert (await _active_summary(client))["title"] == "Outline the confounding lecture"


@pytest.mark.anyio
async def test_title_model_refines_the_fallback_title(tmp_path: Path) -> None:
    prompts: list[str] = []

    def titling(messages: list[ModelMessage], _info: AgentInfo) -> ModelResponse:
        prompts.append(str(_user_prompt(messages)))
        return ModelResponse(parts=[TextPart(content='"Confounding Lecture Outline."')])

    app = _titled_app(tmp_path, FunctionModel(function=titling))
    async with httpx2.AsyncClient(
        transport=httpx2.ASGITransport(app=app), base_url="http://test"
    ) as client:
        await _send(client, "Outline the confounding lecture")
        for _ in range(100):
            summary = await _active_summary(client)
            if summary["title_source"] == "generated":
                break
            await asyncio.sleep(0.01)
    assert summary["title"] == "Confounding Lecture Outline"
    assert prompts == ["Outline the confounding lecture"]


def _user_prompt(messages: list[ModelMessage]) -> object:
    request = messages[-1]
    assert isinstance(request, ModelRequest)
    return next(part.content for part in request.parts if isinstance(part, UserPromptPart))


def test_automatic_titles_never_replace_a_title_the_author_chose(tmp_path: Path) -> None:
    workspace = tmp_path / "course"
    workspace.mkdir()
    store = tmp_path / "chat"
    conversation_id = list_conversations(store, workspace).active_id
    assert propose_conversation_title(store, workspace, conversation_id, "Plan it", "query")
    update_conversation(store, workspace, conversation_id, title="My name")
    assert not propose_conversation_title(
        store, workspace, conversation_id, "Planning Session", "generated"
    )
    summary = list_conversations(store, workspace).conversations[0]
    assert (summary.title, summary.title_source) == ("My name", "author")


def test_stored_titles_without_a_source_keep_author_renames(tmp_path: Path) -> None:
    workspace = tmp_path / "course"
    workspace.mkdir()
    store = tmp_path / "chat"
    list_conversations(store, workspace)
    index_path = next(store.glob("*/index.json"))
    index = json.loads(index_path.read_text())
    first = index["conversations"][0]
    del first["title_source"]
    index["conversations"].append({**first, "id": "renamed", "title": "Week one"})
    index_path.write_text(json.dumps(index))
    (index_path.parent / "renamed.json").write_text(
        (index_path.parent / f"{first['id']}.json").read_text()
    )
    sources = {
        item.title: item.title_source for item in list_conversations(store, workspace).conversations
    }
    assert sources == {"Conversation 1": "default", "Week one": "author"}
