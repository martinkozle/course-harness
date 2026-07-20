import contextlib
import json
import os
from collections.abc import AsyncIterator
from pathlib import Path

import httpx2
import pytest
from fastapi import FastAPI
from pydantic_ai.messages import ModelMessage
from pydantic_ai.models.function import AgentInfo, DeltaToolCall, FunctionModel

from course_harness import resources as res
from course_harness.app import create_app
from course_harness.library import register_and_snapshot
from course_harness.providers import ProviderCapabilities
from course_harness.search import rebuild_index, search_db_path, search_raw

SEARCH_FIXTURES = Path(__file__).with_name("fixtures") / "search"


async def _verified_capabilities(_request: object) -> ProviderCapabilities:
    return ProviderCapabilities(
        tool_calling=True,
        structured_output=True,
        streaming=True,
        context_window=131_072,
        vision=False,
    )


async def _verified_account(_request: object) -> None:
    return None


def _app(
    workspace: Path,
    *,
    data_dir: Path | None = None,
    cache_dir: Path | None = None,
    library_data: Path | None = None,
    library_cache: Path | None = None,
):
    return create_app(
        workspace,
        library_data_path=library_data or data_dir,
        library_cache_path=library_cache or cache_dir,
    )


# ---------------------------------------------------------------------------
# Source CRUD
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_admit_source_requires_processed_resource(tmp_path: Path) -> None:
    workspace = tmp_path / "course"
    workspace.mkdir()
    data_dir = tmp_path / "data"
    cache_dir = tmp_path / "cache"
    transport = httpx2.ASGITransport(app=_app(workspace, data_dir=data_dir, cache_dir=cache_dir))
    async with httpx2.AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.post(
            "/api/sources",
            json={"resource_id": "resource-000000000000"},
        )
    assert response.status_code == 422
    assert "not found in the Library" in response.json()["detail"]


@pytest.mark.anyio
async def test_admit_source_pins_snapshot_version(tmp_path: Path) -> None:
    workspace = tmp_path / "course"
    workspace.mkdir()
    data_dir = tmp_path / "data"
    cache_dir = tmp_path / "cache"
    fixture = SEARCH_FIXTURES / "chapter_causal.md"
    app = create_app(workspace, library_data_path=data_dir, library_cache_path=cache_dir)
    transport = httpx2.ASGITransport(app=app)
    async with httpx2.AsyncClient(transport=transport, base_url="http://test") as client:
        upload_resp = await client.post(
            "/api/resources/upload",
            files={"file": ("chapter_causal.md", fixture.read_bytes(), "text/markdown")},
        )
        resource = upload_resp.json()
        admit_resp = await client.post(
            "/api/sources",
            json={"resource_id": resource["resource_id"], "label": "Causal Foundations"},
        )
    assert admit_resp.status_code == 201
    source = admit_resp.json()
    assert source["id"].startswith("source-")
    assert source["source_version_id"] == resource["snapshot"]["content_hash"]


@pytest.mark.anyio
async def test_duplicate_resource_admission_rejected(tmp_path: Path) -> None:
    workspace = tmp_path / "course"
    workspace.mkdir()
    data_dir = tmp_path / "data"
    cache_dir = tmp_path / "cache"
    fixture = SEARCH_FIXTURES / "chapter_causal.md"
    transport = httpx2.ASGITransport(
        app=_app(workspace, library_data=data_dir, library_cache=cache_dir)
    )
    async with httpx2.AsyncClient(transport=transport, base_url="http://test") as client:
        upload_resp = await client.post(
            "/api/resources/upload",
            files={"file": ("chapter_causal.md", fixture.read_bytes(), "text/markdown")},
        )
        resource = upload_resp.json()
        first = await client.post(
            "/api/sources",
            json={"resource_id": resource["resource_id"], "label": "First"},
        )
        assert first.status_code == 201
        second = await client.post(
            "/api/sources",
            json={"resource_id": resource["resource_id"], "label": "Duplicate"},
        )
    assert second.status_code == 422
    assert "already admitted" in second.json()["detail"]


@pytest.mark.anyio
async def test_list_sources_empty(tmp_path: Path) -> None:
    workspace = tmp_path / "course"
    workspace.mkdir()
    transport = httpx2.ASGITransport(app=_app(workspace))
    async with httpx2.AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.get("/api/sources")
    assert response.status_code == 200
    assert response.json() == []


@pytest.mark.anyio
async def test_list_sources_after_admission(tmp_path: Path) -> None:
    workspace = tmp_path / "course"
    workspace.mkdir()
    data_dir = tmp_path / "data"
    cache_dir = tmp_path / "cache"
    fixture = SEARCH_FIXTURES / "chapter_causal.md"
    transport = httpx2.ASGITransport(
        app=_app(workspace, library_data=data_dir, library_cache=cache_dir)
    )
    async with httpx2.AsyncClient(transport=transport, base_url="http://test") as client:
        upload_resp = await client.post(
            "/api/resources/upload",
            files={"file": ("chapter_causal.md", fixture.read_bytes(), "text/markdown")},
        )
        resource = upload_resp.json()
        admit_resp = await client.post(
            "/api/sources",
            json={"resource_id": resource["resource_id"], "label": "Causal Ch1"},
        )
        assert admit_resp.status_code == 201
        sources_resp = await client.get("/api/sources")
    assert sources_resp.status_code == 200
    sources = sources_resp.json()
    assert len(sources) == 1
    assert sources[0]["label"] == "Causal Ch1"


@pytest.mark.anyio
async def test_remove_source(tmp_path: Path) -> None:
    workspace = tmp_path / "course"
    workspace.mkdir()
    data_dir = tmp_path / "data"
    cache_dir = tmp_path / "cache"
    fixture = SEARCH_FIXTURES / "chapter_causal.md"
    transport = httpx2.ASGITransport(
        app=_app(workspace, library_data=data_dir, library_cache=cache_dir)
    )
    async with httpx2.AsyncClient(transport=transport, base_url="http://test") as client:
        upload_resp = await client.post(
            "/api/resources/upload",
            files={"file": ("chapter_causal.md", fixture.read_bytes(), "text/markdown")},
        )
        resource = upload_resp.json()
        admit_resp = await client.post(
            "/api/sources",
            json={"resource_id": resource["resource_id"], "label": "Causal"},
        )
        source = admit_resp.json()
        delete_resp = await client.delete(f"/api/sources/{source['id']}")
        assert delete_resp.status_code == 204
        list_resp = await client.get("/api/sources")
        assert list_resp.json() == []


@pytest.mark.anyio
async def test_read_source_content_bounded(tmp_path: Path) -> None:
    workspace = tmp_path / "course"
    workspace.mkdir()
    data_dir = tmp_path / "data"
    cache_dir = tmp_path / "cache"
    fixture = SEARCH_FIXTURES / "chapter_causal.md"
    transport = httpx2.ASGITransport(
        app=_app(workspace, library_data=data_dir, library_cache=cache_dir)
    )
    async with httpx2.AsyncClient(transport=transport, base_url="http://test") as client:
        upload_resp = await client.post(
            "/api/resources/upload",
            files={"file": ("chapter_causal.md", fixture.read_bytes(), "text/markdown")},
        )
        resource = upload_resp.json()
        admit_resp = await client.post(
            "/api/sources",
            json={"resource_id": resource["resource_id"], "label": "Causal"},
        )
        source = admit_resp.json()
        content_resp = await client.get(
            f"/api/sources/{source['id']}/content", params={"max_chars": 50}
        )
    assert content_resp.status_code == 200
    assert len(content_resp.text) <= 50
    assert "Chapter" in content_resp.text


@pytest.mark.anyio
async def test_read_source_content_coordinates(tmp_path: Path) -> None:
    workspace = tmp_path / "course"
    workspace.mkdir()
    data_dir = tmp_path / "data"
    cache_dir = tmp_path / "cache"
    fixture = SEARCH_FIXTURES / "chapter_causal.md"
    transport = httpx2.ASGITransport(
        app=_app(workspace, library_data=data_dir, library_cache=cache_dir)
    )
    async with httpx2.AsyncClient(transport=transport, base_url="http://test") as client:
        upload_resp = await client.post(
            "/api/resources/upload",
            files={"file": ("chapter_causal.md", fixture.read_bytes(), "text/markdown")},
        )
        resource = upload_resp.json()
        admit_resp = await client.post(
            "/api/sources",
            json={"resource_id": resource["resource_id"], "label": "Causal"},
        )
        source = admit_resp.json()
        content_resp = await client.get(
            f"/api/sources/{source['id']}/content",
            params={"line_start": 0, "line_end": 3},
        )
    assert content_resp.status_code == 200
    lines = content_resp.text.strip().split("\n")
    assert len(lines) <= 3


# ---------------------------------------------------------------------------
# FTS5 Search
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_search_fts5_finds_known_content(tmp_path: Path) -> None:
    workspace = tmp_path / "course"
    workspace.mkdir()
    data_dir = tmp_path / "data"
    cache_dir = tmp_path / "cache"
    fixture = SEARCH_FIXTURES / "chapter_causal.md"
    transport = httpx2.ASGITransport(
        app=_app(workspace, library_data=data_dir, library_cache=cache_dir)
    )
    async with httpx2.AsyncClient(transport=transport, base_url="http://test") as client:
        upload_resp = await client.post(
            "/api/resources/upload",
            files={"file": ("chapter_causal.md", fixture.read_bytes(), "text/markdown")},
        )
        resource = upload_resp.json()
        await client.post(
            "/api/sources",
            json={"resource_id": resource["resource_id"], "label": "Causal Ch1"},
        )
        search_resp = await client.post(
            "/api/sources/search", json={"query": "counterfactual", "limit": 5}
        )
    assert search_resp.status_code == 200
    results = search_resp.json()
    assert len(results) >= 1
    assert results[0]["label"] == "Causal Ch1"
    chunks = results[0]["chunks"]
    assert len(chunks) >= 1
    assert "counterfactual" in chunks[0]["snippet"].lower()


@pytest.mark.anyio
async def test_search_fts5_returns_snippets_and_coordinates(tmp_path: Path) -> None:
    workspace = tmp_path / "course"
    workspace.mkdir()
    data_dir = tmp_path / "data"
    cache_dir = tmp_path / "cache"
    fixture = SEARCH_FIXTURES / "chapter_causal.md"
    transport = httpx2.ASGITransport(
        app=_app(workspace, library_data=data_dir, library_cache=cache_dir)
    )
    async with httpx2.AsyncClient(transport=transport, base_url="http://test") as client:
        upload_resp = await client.post(
            "/api/resources/upload",
            files={"file": ("chapter_causal.md", fixture.read_bytes(), "text/markdown")},
        )
        resource = upload_resp.json()
        await client.post(
            "/api/sources",
            json={"resource_id": resource["resource_id"], "label": "Causal"},
        )
        search_resp = await client.post("/api/sources/search", json={"query": "DAG", "limit": 5})
    assert search_resp.status_code == 200
    results = search_resp.json()
    assert len(results) >= 1
    group = results[0]
    assert "chunks" in group
    assert len(group["chunks"]) >= 1
    result = group["chunks"][0]
    assert "snippet" in result
    assert "coordinates" in result
    assert result["coordinates"]["line_start"] is not None


@pytest.mark.anyio
async def test_search_respects_source_id_filter(tmp_path: Path) -> None:
    workspace = tmp_path / "course"
    workspace.mkdir()
    data_dir = tmp_path / "data"
    cache_dir = tmp_path / "cache"
    transport = httpx2.ASGITransport(
        app=_app(workspace, library_data=data_dir, library_cache=cache_dir)
    )
    async with httpx2.AsyncClient(transport=transport, base_url="http://test") as client:
        f1 = SEARCH_FIXTURES / "chapter_causal.md"
        upload1 = await client.post(
            "/api/resources/upload",
            files={"file": ("chapter_causal.md", f1.read_bytes(), "text/markdown")},
        )
        r1 = upload1.json()
        f2 = SEARCH_FIXTURES / "chapter_methods.md"
        upload2 = await client.post(
            "/api/resources/upload",
            files={"file": ("chapter_methods.md", f2.read_bytes(), "text/markdown")},
        )
        r2 = upload2.json()
        admit1 = await client.post(
            "/api/sources", json={"resource_id": r1["resource_id"], "label": "Causal"}
        )
        s1 = admit1.json()
        admit2 = await client.post(
            "/api/sources", json={"resource_id": r2["resource_id"], "label": "Methods"}
        )
        s2 = admit2.json()
        search_resp = await client.post(
            "/api/sources/search", json={"query": "treatment effect", "limit": 10}
        )
        results = search_resp.json()
        source_ids = {r["source_id"] for r in results}
        assert s1["id"] in source_ids or s2["id"] in source_ids


@pytest.mark.anyio
async def test_clear_cache_rebuilds_search_index(tmp_path: Path) -> None:
    workspace = tmp_path / "course"
    workspace.mkdir()
    data_dir = tmp_path / "data"
    cache_dir = tmp_path / "cache"
    fixture = SEARCH_FIXTURES / "chapter_causal.md"
    transport = httpx2.ASGITransport(
        app=_app(workspace, library_data=data_dir, library_cache=cache_dir)
    )
    async with httpx2.AsyncClient(transport=transport, base_url="http://test") as client:
        upload_resp = await client.post(
            "/api/resources/upload",
            files={"file": ("chapter_causal.md", fixture.read_bytes(), "text/markdown")},
        )
        resource = upload_resp.json()
        await client.post(
            "/api/sources",
            json={"resource_id": resource["resource_id"], "label": "Causal"},
        )
        assert search_db_path(cache_dir).is_file()
        await client.delete("/api/resources/cache")
        assert search_db_path(cache_dir).is_file()
        hits = search_raw(cache_dir, "counterfactual")
        assert len(hits) >= 1


@pytest.mark.anyio
async def test_rebuild_index_restores_search(tmp_path: Path) -> None:
    data_dir = tmp_path / "data"
    cache_dir = tmp_path / "cache"
    fixture = SEARCH_FIXTURES / "chapter_causal.md"
    content = fixture.read_bytes()
    request = res.ResourceRegistrationRequest(
        kind="upload", location="chapter_causal.md", media_type="text/markdown"
    )
    resource, snapshot, state = register_and_snapshot(data_dir, cache_dir, request, content)
    assert state.status == "ready"
    hits = search_raw(cache_dir, "counterfactual")
    assert len(hits) >= 1
    db_path = search_db_path(cache_dir)
    db_path.unlink()
    for wal in sorted(cache_dir.glob("fts/search.db-*")):
        wal.unlink()
    hits = search_raw(cache_dir, "counterfactual")
    assert len(hits) == 0
    rebuild_index(cache_dir, data_dir)
    hits = search_raw(cache_dir, "counterfactual")
    assert len(hits) >= 1


# ---------------------------------------------------------------------------
# Agent tools for sources
# ---------------------------------------------------------------------------


def _source_tool_model(actions: list[dict[str, object]]) -> FunctionModel:
    async def stream_function(
        _messages: list[ModelMessage], _info: AgentInfo
    ) -> AsyncIterator[str | dict[int, DeltaToolCall]]:
        for i, a in enumerate(actions):
            yield {
                i: DeltaToolCall(
                    name=str(a.get("name", "")),
                    tool_call_id=str(a.get("tool_call_id", f"call-{i}")),
                    json_args=json.dumps(a.get("args", {})),
                )
            }
        yield "Source operation completed."

    return FunctionModel(stream_function=stream_function)


async def _agent_stream(client: httpx2.AsyncClient) -> httpx2.Response:
    return await client.post(
        "/api/agent",
        headers={"Accept": "text/event-stream"},
        json={
            "threadId": "t1",
            "runId": "r1",
            "state": {},
            "messages": [{"id": "user-001", "role": "user", "content": "Hello"}],
            "tools": [],
            "context": [],
            "forwardedProps": {"mode": "autonomous"},
        },
    )


def _parse_sse_events(body: bytes) -> list[dict[str, object]]:
    events: list[dict[str, object]] = []
    for frame in body.decode("utf-8", errors="replace").split("\n\n"):
        if not frame.strip():
            continue
        data_line = frame.split("data: ", 1)[-1] if "data: " in frame else frame
        with contextlib.suppress(json.JSONDecodeError):
            events.append(json.loads(data_line))
    return events


_AGENT_PROVIDER_KEY = os.environ.get("OPENROUTER_API_KEY", "test-key-0123456789")
_PROVIDER_PAYLOAD = {
    "kind": "openrouter",
    "model": "openai/gpt-oss-20b:free",
    "api_key": _AGENT_PROVIDER_KEY,
}


def _make_agent_app(
    workspace: Path,
    data_dir: Path,
    cache_dir: Path,
    provider_store: Path,
    chat_store: Path,
    model: FunctionModel | None = None,
) -> FastAPI:
    return create_app(
        workspace,
        library_data_path=data_dir,
        library_cache_path=cache_dir,
        provider_store_path=provider_store,
        chat_store_path=chat_store,
        agent_model=model,
        provider_validator=_verified_capabilities,
        provider_account_validator=_verified_account,
    )


@pytest.mark.anyio
async def test_agent_list_sources_tool(tmp_path: Path) -> None:
    workspace = tmp_path / "course"
    workspace.mkdir()
    data_dir = tmp_path / "data"
    cache_dir = tmp_path / "cache"
    provider_store = tmp_path / "provider"
    chat_store = tmp_path / "chat"
    fixture = SEARCH_FIXTURES / "chapter_causal.md"
    app = _make_agent_app(workspace, data_dir, cache_dir, provider_store, chat_store)
    async with httpx2.AsyncClient(
        transport=httpx2.ASGITransport(app=app), base_url="http://test"
    ) as client:
        upload_resp = await client.post(
            "/api/resources/upload",
            files={"file": ("chapter_causal.md", fixture.read_bytes(), "text/markdown")},
        )
        resource = upload_resp.json()
        await client.post(
            "/api/sources",
            json={"resource_id": resource["resource_id"], "label": "Causal"},
        )
        provider_resp = await client.put("/api/provider", json=_PROVIDER_PAYLOAD)
        assert provider_resp.status_code == 200
    model = _source_tool_model([{"name": "list_sources", "tool_call_id": "lc-1", "args": {}}])
    app_inner = _make_agent_app(
        workspace, data_dir, cache_dir, provider_store, chat_store, model=model
    )
    async with httpx2.AsyncClient(
        transport=httpx2.ASGITransport(app=app_inner), base_url="http://test"
    ) as client:
        response = await _agent_stream(client)
    assert response.status_code == 200
    events = _parse_sse_events(response.content)
    activity_events = [e for e in events if e.get("type") == "ACTIVITY_SNAPSHOT"]
    assert any("Sources listed" in str(e) for e in activity_events)


@pytest.mark.anyio
async def test_agent_search_sources_tool(tmp_path: Path) -> None:
    workspace = tmp_path / "course"
    workspace.mkdir()
    data_dir = tmp_path / "data"
    cache_dir = tmp_path / "cache"
    provider_store = tmp_path / "provider"
    chat_store = tmp_path / "chat"
    fixture = SEARCH_FIXTURES / "chapter_causal.md"
    app = _make_agent_app(workspace, data_dir, cache_dir, provider_store, chat_store)
    async with httpx2.AsyncClient(
        transport=httpx2.ASGITransport(app=app), base_url="http://test"
    ) as client:
        upload_resp = await client.post(
            "/api/resources/upload",
            files={"file": ("chapter_causal.md", fixture.read_bytes(), "text/markdown")},
        )
        resource = upload_resp.json()
        await client.post(
            "/api/sources",
            json={"resource_id": resource["resource_id"], "label": "Causal"},
        )
        provider_resp = await client.put("/api/provider", json=_PROVIDER_PAYLOAD)
        assert provider_resp.status_code == 200
    tool_args = {"query": "DAG", "limit": 3}
    model = _source_tool_model(
        [{"name": "search_sources", "tool_call_id": "sc-1", "args": tool_args}]
    )
    app_inner = _make_agent_app(
        workspace, data_dir, cache_dir, provider_store, chat_store, model=model
    )
    async with httpx2.AsyncClient(
        transport=httpx2.ASGITransport(app=app_inner), base_url="http://test"
    ) as client:
        response = await _agent_stream(client)
    assert response.status_code == 200
    events = _parse_sse_events(response.content)
    activity_events = [e for e in events if e.get("type") == "ACTIVITY_SNAPSHOT"]
    assert any("Search" in str(e) for e in activity_events)


@pytest.mark.anyio
async def test_agent_read_source_content_tool(tmp_path: Path) -> None:
    workspace = tmp_path / "course"
    workspace.mkdir()
    data_dir = tmp_path / "data"
    cache_dir = tmp_path / "cache"
    provider_store = tmp_path / "provider"
    chat_store = tmp_path / "chat"
    fixture = SEARCH_FIXTURES / "chapter_causal.md"
    app = _make_agent_app(workspace, data_dir, cache_dir, provider_store, chat_store)
    source_id = ""
    async with httpx2.AsyncClient(
        transport=httpx2.ASGITransport(app=app), base_url="http://test"
    ) as client:
        upload_resp = await client.post(
            "/api/resources/upload",
            files={"file": ("chapter_causal.md", fixture.read_bytes(), "text/markdown")},
        )
        resource = upload_resp.json()
        admit_resp = await client.post(
            "/api/sources",
            json={"resource_id": resource["resource_id"], "label": "Causal"},
        )
        source_id = admit_resp.json()["id"]
        provider_resp = await client.put("/api/provider", json=_PROVIDER_PAYLOAD)
        assert provider_resp.status_code == 200
    tool_args = {"source_id": source_id, "max_chars": 200}
    model = _source_tool_model(
        [{"name": "read_source_content", "tool_call_id": "rc-1", "args": tool_args}]
    )
    app_inner = _make_agent_app(
        workspace, data_dir, cache_dir, provider_store, chat_store, model=model
    )
    async with httpx2.AsyncClient(
        transport=httpx2.ASGITransport(app=app_inner), base_url="http://test"
    ) as client:
        response = await _agent_stream(client)
    events = _parse_sse_events(response.content)
    activity_events = [e for e in events if e.get("type") == "ACTIVITY_SNAPSHOT"]
    assert any("Read" in str(e) for e in activity_events)


@pytest.mark.anyio
async def test_agent_admit_source_requires_approval(tmp_path: Path) -> None:
    workspace = tmp_path / "course"
    workspace.mkdir()
    data_dir = tmp_path / "data"
    cache_dir = tmp_path / "cache"
    provider_store = tmp_path / "provider"
    chat_store = tmp_path / "chat"
    fixture = SEARCH_FIXTURES / "chapter_causal.md"
    app = _make_agent_app(workspace, data_dir, cache_dir, provider_store, chat_store)
    async with httpx2.AsyncClient(
        transport=httpx2.ASGITransport(app=app), base_url="http://test"
    ) as client:
        upload_resp = await client.post(
            "/api/resources/upload",
            files={"file": ("chapter_causal.md", fixture.read_bytes(), "text/markdown")},
        )
        resource = upload_resp.json()
        provider_resp = await client.put("/api/provider", json=_PROVIDER_PAYLOAD)
        assert provider_resp.status_code == 200
    tool_args = {"resource_id": resource["resource_id"], "label": "Causal"}
    model = _source_tool_model(
        [{"name": "admit_source", "tool_call_id": "ac-1", "args": tool_args}]
    )
    app_inner = _make_agent_app(
        workspace, data_dir, cache_dir, provider_store, chat_store, model=model
    )
    async with httpx2.AsyncClient(
        transport=httpx2.ASGITransport(app=app_inner), base_url="http://test"
    ) as client:
        response = await client.post(
            "/api/agent",
            headers={"Accept": "text/event-stream"},
            json={
                "threadId": "t1",
                "runId": "r2",
                "state": {},
                "messages": [
                    {"id": "user-001", "role": "user", "content": "Admit this as a source"}
                ],
                "tools": [],
                "context": [],
                "forwardedProps": {"mode": "guided"},
            },
        )
    events = _parse_sse_events(response.content)
    finished = [e for e in events if e.get("type") == "RUN_FINISHED"]
    assert len(finished) >= 1
    outcome = finished[0].get("outcome", {})
    if not isinstance(outcome, dict):
        pytest.fail("Expected outcome to be a dict")
    interrupts = outcome.get("interrupts", [])
    assert interrupts, "Guided mode should produce an interrupt for admit_source"


# ---------------------------------------------------------------------------
# Labeled retrieval fixture
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_labeled_retrieval_fixture_expected_discovery(tmp_path: Path) -> None:
    data_dir = tmp_path / "data"
    cache_dir = tmp_path / "cache"
    test_cases: list[tuple[Path, list[str]]] = [
        (SEARCH_FIXTURES / "chapter_causal.md", ["counterfactual", "DAG"]),
        (SEARCH_FIXTURES / "chapter_methods.md", ["synthetic control"]),
        (SEARCH_FIXTURES / "appendix_proofs.md", ["asymptotic normality"]),
    ]
    for fixture_path, _queries in test_cases:
        content = fixture_path.read_bytes()
        request = res.ResourceRegistrationRequest(
            kind="upload", location=fixture_path.name, media_type="text/markdown"
        )
        register_and_snapshot(data_dir, cache_dir, request, content)
    for fixture_path, queries in test_cases:
        for query in queries:
            hits = search_raw(cache_dir, query)
            assert len(hits) >= 1, f"Query '{query}' should find {fixture_path.name}"


@pytest.mark.anyio
async def test_adopt_version_updates_source(tmp_path: Path) -> None:
    workspace = tmp_path / "adopt-course"
    workspace.mkdir()
    data_dir = tmp_path / "library-data"
    cache_dir = tmp_path / "library-cache"

    content_v1 = b"# First version content"
    content_v2 = b"# Second version, updated content"

    app = _app(workspace, data_dir=data_dir, cache_dir=cache_dir)
    transport = httpx2.ASGITransport(app=app)

    from unittest.mock import AsyncMock, patch

    async with httpx2.AsyncClient(transport=transport, base_url="http://test") as client:
        with patch(
            "course_harness.library.fetch_remote_resource",
            AsyncMock(return_value=(content_v1, "text/markdown")),
        ):
            create_r = await client.post(
                "/api/resources/remote",
                json={"url": "https://example.com/source.md"},
            )
            assert create_r.status_code == 201

        resource_body = (await client.get("/api/resources")).json()
        resource_id = resource_body[0]["resource_id"]

        admit_r = await client.post(
            "/api/sources",
            json={"resource_id": resource_id, "label": "Test Source"},
        )
        assert admit_r.status_code == 201
        source_id = admit_r.json()["id"]
        old_version = admit_r.json()["source_version_id"]

        with patch(
            "course_harness.library.fetch_remote_resource",
            AsyncMock(return_value=(content_v2, "text/markdown")),
        ):
            refresh_r = await client.post(f"/api/resources/{resource_id}/refresh")
            assert refresh_r.status_code == 200

        adopt_r = await client.post(f"/api/sources/{source_id}/adopt-version")
        assert adopt_r.status_code == 200
        adopted = adopt_r.json()
        assert adopted["source_version_id"] != old_version


@pytest.mark.anyio
async def test_adopt_version_already_latest(tmp_path: Path) -> None:
    workspace = tmp_path / "adopt-latest-course"
    workspace.mkdir()
    data_dir = tmp_path / "library-data"
    cache_dir = tmp_path / "library-cache"

    app = _app(workspace, data_dir=data_dir, cache_dir=cache_dir)
    transport = httpx2.ASGITransport(app=app)

    from unittest.mock import AsyncMock, patch

    async with httpx2.AsyncClient(transport=transport, base_url="http://test") as client:
        with patch(
            "course_harness.library.fetch_remote_resource",
            AsyncMock(return_value=(b"content", "text/markdown")),
        ):
            create_r = await client.post(
                "/api/resources/remote",
                json={"url": "https://example.com/stable.md"},
            )
            assert create_r.status_code == 201

        resource_body = (await client.get("/api/resources")).json()
        resource_id = resource_body[0]["resource_id"]

        admit_r = await client.post(
            "/api/sources",
            json={"resource_id": resource_id, "label": "Stable Source"},
        )
        assert admit_r.status_code == 201
        source_id = admit_r.json()["id"]

        adopt_r = await client.post(f"/api/sources/{source_id}/adopt-version")
        assert adopt_r.status_code == 422
        assert "latest" in (adopt_r.json().get("detail") or "").lower()
