"""The Course Agent researches Candidates and brings chosen ones into the Course."""

import json
from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, patch

import httpx2
import pytest
from pydantic_ai.messages import (
    ModelMessage,
    ModelRequest,
    ModelResponse,
    TextPart,
    ToolCallPart,
    ToolReturnPart,
)
from pydantic_ai.models.function import AgentInfo, DeltaToolCall, FunctionModel

from course_harness.app import create_app
from course_harness.chat_history import read_chat_transcript, save_chat_history
from course_harness.course_agent import (
    CourseAgentDeps,
    CourseAgentState,
    create_autonomous_course_agent,
)
from course_harness.discovery import (
    _parse_openalex,
    _parse_semantic_scholar,
    merge_candidates,
)
from course_harness.providers import ProviderCapabilities
from course_harness.resources import Candidate, DiscoveryResult

SEMANTIC_SCHOLAR_RESPONSE = {
    "data": [
        {
            "paperId": "abc123",
            "title": "An Introduction to Causal Inference",
            "authors": [{"name": "Judea Pearl"}],
            "abstract": "This paper summarizes recent advances in causal inference.",
            "year": 2010,
            "venue": "The International Journal of Biostatistics",
            "externalIds": {"DOI": "10.2202/1557-4679.1203"},
            "openAccessPdf": {"url": "https://example.org/pearl.pdf"},
            "citationCount": 763,
            "publicationDate": "2010-02-26",
        },
        {"paperId": "no-link", "title": "Unreachable", "externalIds": {}},
    ]
}

OPENALEX_RESPONSE = {
    "results": [
        {
            "id": "https://openalex.org/W1",
            "ids": {"openalex": "https://openalex.org/W1"},
            "doi": "https://doi.org/10.2202/1557-4679.1203",
            "display_name": "An Introduction to Causal Inference",
            "publication_date": "2010-02-26",
            "cited_by_count": 770,
            "authorships": [{"author": {"display_name": "Judea Pearl"}}],
            "abstract_inverted_index": {"causal": [1], "Recent": [0], "inference.": [2]},
            "primary_location": {"source": {"display_name": "Int. J. Biostatistics"}},
            "best_oa_location": {
                "landing_page_url": "https://arxiv.org/abs/1001.0001v2",
                "pdf_url": "https://arxiv.org/pdf/1001.0001v2",
            },
        }
    ]
}


def test_semantic_scholar_results_keep_paper_identity() -> None:
    candidates = _parse_semantic_scholar(SEMANTIC_SCHOLAR_RESPONSE)

    assert len(candidates) == 1
    paper = candidates[0]
    assert paper.url == "https://doi.org/10.2202/1557-4679.1203"
    assert paper.doi == "10.2202/1557-4679.1203"
    assert paper.open_access_url == "https://example.org/pearl.pdf"
    assert paper.citation_count == 763
    assert paper.authors == ["Judea Pearl"]


def test_openalex_results_rebuild_abstracts_and_find_open_access_copies() -> None:
    paper = _parse_openalex(OPENALEX_RESPONSE)[0]

    assert paper.summary == "Recent causal inference."
    assert paper.doi == "10.2202/1557-4679.1203"
    assert paper.arxiv_id == "1001.0001"
    assert paper.open_access_url == "https://arxiv.org/pdf/1001.0001v2"
    assert paper.venue == "Int. J. Biostatistics"


def test_the_same_paper_from_several_indexes_is_one_candidate() -> None:
    crossref = Candidate(
        provider="crossref",
        provider_id="10.2202/1557-4679.1203",
        title="An Introduction to Causal Inference",
        url="https://doi.org/10.2202/1557-4679.1203",
        doi="10.2202/1557-4679.1203",
    )
    other = Candidate(provider="crossref", provider_id="x", title="Other", url="https://x.org")
    merged = merge_candidates([[crossref, other], _parse_openalex(OPENALEX_RESPONSE)])

    assert [candidate.title for candidate in merged] == [
        "An Introduction to Causal Inference",
        "Other",
    ]
    assert merged[0].provider == "crossref"
    assert merged[0].open_access_url == "https://arxiv.org/pdf/1001.0001v2"
    assert merged[0].arxiv_id == "1001.0001"


def _tool_returns(messages: list[ModelMessage]) -> list[ToolReturnPart]:
    return [
        part
        for message in messages
        if isinstance(message, ModelRequest)
        for part in message.parts
        if isinstance(part, ToolReturnPart)
    ]


def test_paper_search_shows_candidates_without_changing_the_library_or_course(
    tmp_path: Path,
) -> None:
    workspace = tmp_path / "course"
    workspace.mkdir()
    data_dir = tmp_path / "library"
    chat_store = tmp_path / "chat"

    def researcher(messages: list[ModelMessage], _info: AgentInfo) -> ModelResponse:
        if not _tool_returns(messages):
            return ModelResponse(
                parts=[
                    ToolCallPart(
                        tool_name="search_papers",
                        args={"query": "causal inference", "limit": 5},
                        tool_call_id="search-1",
                    )
                ]
            )
        return ModelResponse(parts=[TextPart(content="I found one strong overview.")])

    async def fake_discover(request):
        assert set(request.providers) == {"arxiv", "crossref", "semantic_scholar", "openalex"}
        return [
            DiscoveryResult(
                provider="semantic_scholar",
                candidates=_parse_semantic_scholar(SEMANTIC_SCHOLAR_RESPONSE),
            ),
            DiscoveryResult(provider="openalex", candidates=_parse_openalex(OPENALEX_RESPONSE)),
            DiscoveryResult(provider="arxiv", error="arxiv failed (HTTP 503)."),
        ]

    with patch("course_harness.discovery.discover", fake_discover):
        result = create_autonomous_course_agent().run_sync(
            "Find an overview of causal inference.",
            model=FunctionModel(function=researcher),
            deps=CourseAgentDeps(
                course_state=CourseAgentState(),
                workspace=workspace,
                data_dir=data_dir,
                cache_dir=tmp_path / "cache",
                chat_store_path=chat_store,
            ),
        )

    returned = json.loads(str(_tool_returns(result.all_messages())[0].content))
    assert returned["kind"] == "research_candidates"
    assert "UNTRUSTED" in returned["security_notice"]
    candidates = json.loads(returned["content"])
    assert len(candidates) == 1
    assert candidates[0]["doi"] == "10.2202/1557-4679.1203"
    assert returned["errors"] == {"arxiv": "arxiv failed (HTTP 503)."}
    assert not data_dir.exists()
    assert list(workspace.iterdir()) == []

    save_chat_history(chat_store, workspace, result.all_messages())
    reply = read_chat_transcript(chat_store, workspace).messages[-1]
    assert reply.content == "I found one strong overview."
    assert reply.research is not None
    card = reply.research[0]
    assert card.title == "Papers: causal inference"
    assert card.candidates[0].url == "https://doi.org/10.2202/1557-4679.1203"
    assert card.errors == {"arxiv": "arxiv failed (HTTP 503)."}


# ---------------------------------------------------------------------------
# Adding Candidates to the Course
# ---------------------------------------------------------------------------

PEARL_HTML = (
    b"<html><body><h1>An Introduction to Causal Inference</h1>"
    b"<p>The back-door criterion selects admissible sets for adjustment.</p></body></html>"
)
PEARL_URL = "https://doi.org/10.2202/1557-4679.1203"


async def _public_host_resolver(_hostname: str, _port: int) -> set[str]:
    return {"8.8.8.8"}


async def _verified_capabilities(_request: object) -> ProviderCapabilities:
    return ProviderCapabilities(
        tool_calling=True,
        structured_output=True,
        streaming=True,
        context_window=131_072,
        vision=False,
    )


async def _run_agent(app: Any, prompt: str) -> str:
    body = json.dumps(
        {
            "threadId": "course-agent",
            "runId": "research-run",
            "state": {},
            "messages": [{"id": "user-1", "role": "user", "content": prompt}],
            "tools": [],
            "context": [],
            "forwardedProps": {"mode": "autonomous"},
        }
    ).encode()
    sent = False
    parts: list[bytes] = []

    async def receive() -> dict[str, object]:
        nonlocal sent
        if sent:
            return {"type": "http.disconnect"}
        sent = True
        return {"type": "http.request", "body": body, "more_body": False}

    async def send(message: dict[str, Any]) -> None:
        if message["type"] == "http.response.start":
            assert message["status"] == 200
        elif message["type"] == "http.response.body" and message.get("body"):
            parts.append(message["body"])

    await app(
        {
            "type": "http",
            "asgi": {"version": "3.0", "spec_version": "2.4"},
            "http_version": "1.1",
            "method": "POST",
            "scheme": "http",
            "path": "/api/agent",
            "raw_path": b"/api/agent",
            "query_string": b"",
            "root_path": "",
            "headers": [
                (b"accept", b"text/event-stream"),
                (b"content-type", b"application/json"),
                (b"content-length", str(len(body)).encode()),
            ],
            "client": ("127.0.0.1", 50000),
            "server": ("test", 80),
        },
        receive,
        send,
    )
    return b"".join(parts).decode()


def _scripted(*calls: tuple[str, dict[str, object]], reply: str):
    """A streaming model that makes the given tool calls in turn, then replies."""

    async def model(
        messages: list[ModelMessage], _info: AgentInfo
    ) -> AsyncIterator[str | dict[int, DeltaToolCall]]:
        step = len(_tool_returns(messages))
        if step < len(calls):
            name, args = calls[step]
            yield {0: DeltaToolCall(name=name, json_args=json.dumps(args), tool_call_id=f"c{step}")}
        else:
            yield reply

    return model


def _research_app(tmp_path: Path, model: Any) -> tuple[Any, Path]:
    workspace = tmp_path / "course"
    workspace.mkdir()
    app = create_app(
        workspace,
        provider_store_path=tmp_path / "provider",
        chat_store_path=tmp_path / "chat",
        library_data_path=tmp_path / "library",
        library_cache_path=tmp_path / "cache",
        agent_model=FunctionModel(stream_function=model),
        provider_validator=_verified_capabilities,
        remote_host_resolver=_public_host_resolver,
    )
    return app, workspace


async def _fake_discover(_request: object) -> list[DiscoveryResult]:
    return [
        DiscoveryResult(
            provider="semantic_scholar",
            candidates=_parse_semantic_scholar(SEMANTIC_SCHOLAR_RESPONSE),
        )
    ]


@pytest.mark.anyio
async def test_the_agent_researches_adds_and_cites_a_candidate(tmp_path: Path) -> None:
    app, workspace = _research_app(
        tmp_path,
        _scripted(
            (
                "replace_course_plan",
                {
                    "command": {
                        "title": "Causal Inference",
                        "audience": "Graduate students",
                        "lectures": [{"title": "Confounding"}],
                    }
                },
            ),
            ("search_papers", {"query": "back-door criterion"}),
            ("add_candidate", {"url": PEARL_URL, "label": "Pearl 2010"}),
            ("add_candidate", {"url": PEARL_URL}),
            reply="I added Pearl 2010.",
        ),
    )
    fetch = AsyncMock(return_value=(PEARL_HTML, "text/html"))
    transport = httpx2.ASGITransport(app=app)
    async with httpx2.AsyncClient(transport=transport, base_url="http://test") as client:
        await client.put(
            "/api/provider",
            json={"kind": "openrouter", "model": "m", "api_key": "secret"},
        )
        with (
            patch("course_harness.discovery.discover", _fake_discover),
            patch("course_harness.library.fetch_remote_resource", fetch),
        ):
            stream = await _run_agent(app, "Research the back-door criterion for Lecture 1.")
        sources = (await client.get("/api/sources")).json()
        resources = (await client.get("/api/resources")).json()
        transcript = (await client.get("/api/chat")).json()
        content = (await client.get(f"/api/sources/{sources[0]['id']}/content")).text

    assert "sources-admit-" in stream
    assert fetch.await_count == 1
    assert [source["label"] for source in sources] == ["Pearl 2010"]
    assert [resource["location"] for resource in resources] == [PEARL_URL]
    assert "back-door criterion selects admissible sets" in content
    assert "sources.yaml" in {path.name for path in workspace.iterdir()}
    reply = transcript["messages"][-1]
    assert reply["content"] == "I added Pearl 2010."
    assert reply["research"][0]["candidates"][0]["url"] == PEARL_URL


@pytest.mark.anyio
async def test_the_agent_cannot_add_a_url_it_made_up(tmp_path: Path) -> None:
    returned: list[str] = []

    async def model(
        messages: list[ModelMessage], _info: AgentInfo
    ) -> AsyncIterator[str | dict[int, DeltaToolCall]]:
        returns = _tool_returns(messages)
        if not returns:
            yield {
                0: DeltaToolCall(
                    name="add_candidate",
                    json_args=json.dumps({"url": "https://invented.example/paper.pdf"}),
                    tool_call_id="c0",
                )
            }
        else:
            returned.append(str(returns[0].content))
            yield "I could not add it."

    app, _workspace = _research_app(tmp_path, model)
    fetch = AsyncMock(return_value=(PEARL_HTML, "text/html"))
    transport = httpx2.ASGITransport(app=app)
    async with httpx2.AsyncClient(transport=transport, base_url="http://test") as client:
        await client.put(
            "/api/provider",
            json={"kind": "openrouter", "model": "m", "api_key": "secret"},
        )
        with patch("course_harness.library.fetch_remote_resource", fetch):
            await _run_agent(app, "Add a paper about causal inference.")
        sources = (await client.get("/api/sources")).json()

    assert "did not appear in any research result" in returned[0]
    assert fetch.await_count == 0
    assert sources == []


@pytest.mark.anyio
async def test_a_course_author_adds_a_candidate_in_one_step(tmp_path: Path) -> None:
    app, _workspace = _research_app(tmp_path, _scripted(reply="unused"))
    transport = httpx2.ASGITransport(app=app)
    async with httpx2.AsyncClient(transport=transport, base_url="http://test") as client:
        with patch(
            "course_harness.library.fetch_remote_resource",
            AsyncMock(return_value=(PEARL_HTML, "text/html")),
        ):
            added = await client.post(
                "/api/sources/from-url", json={"url": PEARL_URL, "label": "Pearl 2010"}
            )
            again = await client.post("/api/sources/from-url", json={"url": PEARL_URL})
        with patch(
            "course_harness.library.fetch_remote_resource",
            AsyncMock(return_value=(b"\x00\x01", "application/octet-stream")),
        ):
            unreadable = await client.post(
                "/api/sources/from-url", json={"url": "https://example.org/blob"}
            )
        sources = (await client.get("/api/sources")).json()

    assert added.status_code == 201
    assert again.json()["id"] == added.json()["id"]
    assert unreadable.status_code == 422
    assert "No processor available" in unreadable.json()["detail"]
    assert [source["label"] for source in sources] == ["Pearl 2010"]


def test_candidates_wait_for_a_course_plan_before_anything_is_written(tmp_path: Path) -> None:
    workspace = tmp_path / "course"
    workspace.mkdir()
    captured: list[str] = []

    async def capture(url: str):
        captured.append(url)
        raise AssertionError("nothing should be captured without a Course Plan")

    def model(messages: list[ModelMessage], _info: AgentInfo) -> ModelResponse:
        if not _tool_returns(messages):
            return ModelResponse(
                parts=[
                    ToolCallPart(
                        tool_name="add_candidate", args={"url": PEARL_URL}, tool_call_id="a"
                    )
                ]
            )
        return ModelResponse(parts=[TextPart(content="I need a Course Plan first.")])

    result = create_autonomous_course_agent().run_sync(
        f"Please use {PEARL_URL} in the course.",
        model=FunctionModel(function=model),
        deps=CourseAgentDeps(
            course_state=CourseAgentState(),
            workspace=workspace,
            data_dir=tmp_path / "library",
            cache_dir=tmp_path / "cache",
            capture_remote=capture,
        ),
    )

    assert "replace_course_plan first" in str(_tool_returns(result.all_messages())[0].content)
    assert captured == []
    assert list(workspace.iterdir()) == []
