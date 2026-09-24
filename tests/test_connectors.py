"""Connectors let the Course Agent use Course Author-configured MCP tools for research."""

import json
from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, patch

import httpx2
import pytest
from fastmcp import FastMCP
from pydantic_ai.mcp import MCPToolset
from pydantic_ai.messages import ModelMessage, ModelRequest, ToolReturnPart
from pydantic_ai.models.function import AgentInfo, DeltaToolCall, FunctionModel

from course_harness.app import create_app
from course_harness.connector_tools import extract_links
from course_harness.connectors import ResolvedConnector
from course_harness.providers import ProviderCapabilities

ARTICLE_URL = "https://blog.example.org/back-door"
EXA_RESULTS = f"""Title: The back-door criterion, explained
URL: {ARTICLE_URL}
Published: 2021-05-04T00:00:00.000Z
Author: A. Writer
Highlights:
A set of variables satisfies the back-door criterion when it blocks every back-door path.

Title: Second result
URL: https://example.com/second
Highlights:
More text."""
ARTICLE_MARKDOWN = (
    "# The back-door criterion, explained\n\n"
    + "Adjusting for an admissible set removes confounding bias. " * 20
)


def exa_stand_in(calls: list[str] | None = None) -> FastMCP:
    server = FastMCP("exa-stand-in")

    @server.tool
    def web_search_exa(query: str, numResults: int = 5) -> str:  # noqa: N803
        """Search the web."""
        if calls is not None:
            calls.append(f"search:{query}")
        return EXA_RESULTS

    @server.tool
    def web_fetch_exa(urls: list[str], maxCharacters: int = 3000) -> str:  # noqa: N803
        """Read pages."""
        if calls is not None:
            calls.append(f"fetch:{','.join(urls)}:{maxCharacters}")
        return ARTICLE_MARKDOWN

    @server.tool
    def agent_run(task: str) -> str:
        """A slow paid tool that is hidden by default."""
        return "ran"

    return server


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


def _app(tmp_path: Path, *, model: Any = None, server: FastMCP | None = None) -> Any:
    workspace = tmp_path / "course"
    workspace.mkdir(parents=True, exist_ok=True)
    connect = None
    if server is not None:

        def connect(connector: ResolvedConnector) -> MCPToolset[Any]:
            return MCPToolset(server, id=connector.id)

    return create_app(
        workspace,
        provider_store_path=tmp_path / "provider",
        chat_store_path=tmp_path / "chat",
        library_data_path=tmp_path / "library",
        library_cache_path=tmp_path / "cache",
        agent_model=FunctionModel(stream_function=model) if model else None,
        provider_validator=_verified_capabilities,
        remote_host_resolver=_public_host_resolver,
        connector_connect=connect,
    )


def _client(app: Any) -> httpx2.AsyncClient:
    return httpx2.AsyncClient(transport=httpx2.ASGITransport(app=app), base_url="http://test")


def _exa_input(connector: dict[str, Any], **changes: Any) -> dict[str, Any]:
    request = {
        "name": connector["name"],
        "url": connector["url"],
        "url_secret": connector["url_secret"],
        "headers": [
            {"name": header["name"], "value": header["value"], "secret": header["secret"]}
            for header in connector["headers"]
        ],
        "enabled": connector["enabled"],
        "hidden_tools": connector["hidden_tools"],
        "fetch_tool": connector["fetch_tool"],
    }
    request.update(changes)
    return request


@pytest.mark.anyio
async def test_exa_is_a_default_connector_that_works_without_a_key(tmp_path: Path) -> None:
    async with _client(_app(tmp_path)) as client:
        connectors = (await client.get("/api/connectors")).json()

    assert [connector["name"] for connector in connectors] == ["Exa"]
    exa = connectors[0]
    assert exa["url"] == "https://mcp.exa.ai/mcp"
    assert exa["preset"] == "exa"
    assert exa["enabled"] is True
    assert exa["fetch_tool"] == "web_fetch_exa"
    assert exa["hidden_tools"] == ["agent_run"]
    assert exa["headers"] == [
        {"name": "x-api-key", "value": None, "secret": True, "configured": False}
    ]


@pytest.mark.anyio
async def test_connector_credentials_are_write_only_and_outside_the_workspace(
    tmp_path: Path,
) -> None:
    secret = "exa-secret-key-1234"
    async with _client(_app(tmp_path)) as client:
        exa = (await client.get("/api/connectors")).json()[0]
        saved = await client.put(
            f"/api/connectors/{exa['id']}",
            json=_exa_input(exa, headers=[{"name": "x-api-key", "value": secret, "secret": True}]),
        )
        kept = await client.put(
            f"/api/connectors/{exa['id']}",
            json=_exa_input(
                exa,
                name="Exa search",
                headers=[{"name": "x-api-key", "value": "", "secret": True}],
            ),
        )
        listed = await client.get("/api/connectors")

    credentials = tmp_path / "provider" / "connector-credentials" / "credentials.json"
    assert saved.status_code == 200
    assert saved.json()["headers"][0] == {
        "name": "x-api-key",
        "value": None,
        "secret": True,
        "configured": True,
    }
    assert kept.json()["headers"][0]["configured"] is True
    assert kept.json()["name"] == "Exa search"
    assert kept.json()["credential_storage"] == "private-json-file"
    for body in (saved.text, kept.text, listed.text):
        assert secret not in body
    assert secret not in (tmp_path / "provider" / "connectors.json").read_text()
    assert secret in credentials.read_text()
    assert credentials.stat().st_mode & 0o077 == 0
    for path in (tmp_path / "course").rglob("*"):
        assert not path.is_file() or secret not in path.read_text(errors="ignore")


@pytest.mark.anyio
async def test_a_connector_can_be_replaced_and_the_default_restored(tmp_path: Path) -> None:
    async with _client(_app(tmp_path)) as client:
        exa = (await client.get("/api/connectors")).json()[0]
        insecure = await client.post(
            "/api/connectors", json={"name": "Plain", "url": "http://search.example.com/mcp"}
        )
        tavily = await client.post(
            "/api/connectors",
            json={
                "name": "Tavily",
                "url": "https://mcp.tavily.com/mcp/?tavilyApiKey=tvly-secret",
                "url_secret": True,
            },
        )
        deleted = await client.delete(f"/api/connectors/{exa['id']}")
        after_delete = (await client.get("/api/connectors")).json()
        restored = (await client.post("/api/connectors/restore-defaults")).json()
        missing = await client.delete(f"/api/connectors/{exa['id']}")

    assert insecure.status_code == 422
    assert tavily.status_code == 201
    assert tavily.json()["url"] == "https://mcp.tavily.com"
    assert "tvly-secret" not in tavily.text
    assert deleted.status_code == 204
    assert [connector["name"] for connector in after_delete] == ["Tavily"]
    assert [connector["name"] for connector in restored] == ["Exa", "Tavily"]
    assert missing.status_code == 404


@pytest.mark.anyio
async def test_testing_a_connector_lists_its_tools(tmp_path: Path) -> None:
    async with _client(_app(tmp_path, server=exa_stand_in())) as client:
        exa = (await client.get("/api/connectors")).json()[0]
        tested = await client.post(f"/api/connectors/{exa['id']}/test")
    async with _client(_app(tmp_path / "offline")) as client:
        exa = (await client.get("/api/connectors")).json()[0]
        unreachable = await client.post(f"/api/connectors/{exa['id']}/test")

    assert tested.status_code == 200
    assert {tool["name"] for tool in tested.json()["tools"]} == {
        "web_search_exa",
        "web_fetch_exa",
        "agent_run",
    }
    assert unreachable.status_code == 502
    assert unreachable.json()["detail"].startswith("Exa could not be reached")


def test_search_results_become_candidate_links() -> None:
    links = extract_links(EXA_RESULTS)

    assert links == [
        {
            "url": ARTICLE_URL,
            "title": "The back-door criterion, explained",
            "published": "2021-05-04",
            "authors": ["A. Writer"],
            "summary": "A set of variables satisfies the back-door criterion when "
            "it blocks every back-door path.",
        },
        {
            "url": "https://example.com/second",
            "title": "Second result",
            "summary": "More text.",
        },
    ]
    assert extract_links(json.dumps({"results": [{"url": "https://a.org", "title": "A"}]})) == [
        {"url": "https://a.org", "title": "A"}
    ]
    assert extract_links("See https://b.org/page. And more.") == [{"url": "https://b.org/page"}]


async def _run_agent(client: httpx2.AsyncClient, prompt: str) -> str:
    response = await client.post(
        "/api/agent",
        headers={"accept": "text/event-stream"},
        json={
            "threadId": "course-agent",
            "runId": "connector-run",
            "state": {},
            "messages": [{"id": "user-1", "role": "user", "content": prompt}],
            "tools": [],
            "context": [],
            "forwardedProps": {"mode": "autonomous"},
        },
    )
    assert response.status_code == 200
    return response.text


def _tool_returns(messages: list[ModelMessage]) -> list[ToolReturnPart]:
    return [
        part
        for message in messages
        if isinstance(message, ModelRequest)
        for part in message.parts
        if isinstance(part, ToolReturnPart)
    ]


@pytest.mark.anyio
async def test_web_research_through_a_connector_reaches_the_course(tmp_path: Path) -> None:
    calls: list[str] = []
    offered: list[set[str]] = []
    returned: list[str] = []
    steps = [
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
        ("exa_web_search_exa", {"query": "back-door criterion"}),
        ("add_candidate", {"url": ARTICLE_URL, "label": "Back-door explainer"}),
    ]

    async def model(
        messages: list[ModelMessage], info: AgentInfo
    ) -> AsyncIterator[str | dict[int, DeltaToolCall]]:
        offered.append({tool.name for tool in info.function_tools})
        returns = _tool_returns(messages)
        returned[:] = [str(part.content) for part in returns]
        if len(returns) < len(steps):
            name, args = steps[len(returns)]
            yield {0: DeltaToolCall(name=name, json_args=json.dumps(args), tool_call_id=name)}
        else:
            yield "I added a web explainer."

    app = _app(tmp_path, model=model, server=exa_stand_in(calls))
    blocked = AsyncMock(side_effect=ValueError("Client error '403 Forbidden'"))
    async with _client(app) as client:
        await client.put(
            "/api/provider", json={"kind": "openrouter", "model": "m", "api_key": "secret"}
        )
        with patch("course_harness.library.fetch_remote_resource", blocked):
            await _run_agent(client, "Find a readable explainer of the back-door criterion.")
        sources = (await client.get("/api/sources")).json()
        resources = (await client.get("/api/resources")).json()
        transcript = (await client.get("/api/chat")).json()
        content = (await client.get(f"/api/sources/{sources[0]['id']}/content")).text

    assert "exa_web_search_exa" in offered[0]
    assert "exa_web_fetch_exa" in offered[0]
    assert "exa_agent_run" not in offered[0]
    search_result = json.loads(returned[1])
    assert search_result["kind"] == "connector_result"
    assert "UNTRUSTED" in search_result["security_notice"]
    assert calls == ["search:back-door criterion", f"fetch:{ARTICLE_URL}:200000"]
    assert [source["label"] for source in sources] == ["Back-door explainer"]
    assert resources[0]["location"] == ARTICLE_URL
    assert resources[0]["captured_via"] == "Exa"
    assert "admissible set removes confounding" in content
    card = transcript["messages"][-1]["research"][0]
    assert card["title"] == "Exa: back-door criterion"
    assert card["candidates"][0]["url"] == ARTICLE_URL


@pytest.mark.anyio
async def test_an_unreachable_or_disabled_connector_does_not_stop_a_run(tmp_path: Path) -> None:
    offered: list[set[str]] = []
    instructions: list[str] = []

    async def model(
        messages: list[ModelMessage], info: AgentInfo
    ) -> AsyncIterator[str | dict[int, DeltaToolCall]]:
        offered.append({tool.name for tool in info.function_tools})
        instructions.append(info.instructions or "")
        yield "Web search is unavailable right now."

    async with _client(_app(tmp_path, model=model)) as client:
        await client.put(
            "/api/provider", json={"kind": "openrouter", "model": "m", "api_key": "secret"}
        )
        stream = await _run_agent(client, "Search the web.")
        exa = (await client.get("/api/connectors")).json()[0]
        await client.put(f"/api/connectors/{exa['id']}", json=_exa_input(exa, enabled=False))
        await _run_agent(client, "Search the web again.")

    assert "RUN_FINISHED" in stream
    assert not any(name.startswith("exa_") for name in offered[0])
    assert "The Exa Connector is unavailable in this run" in instructions[0]
    assert "Exa Connector" not in instructions[1]


@pytest.mark.anyio
async def test_a_run_connects_only_when_a_connector_tool_is_called(tmp_path: Path) -> None:
    server = exa_stand_in()
    connections: list[str] = []
    replies = iter(["No research needed.", "call"])

    def counting_connect(connector: ResolvedConnector) -> MCPToolset[Any]:
        connections.append(connector.name)
        return MCPToolset(server, id=connector.id)

    async def model(
        messages: list[ModelMessage], _info: AgentInfo
    ) -> AsyncIterator[str | dict[int, DeltaToolCall]]:
        if _tool_returns(messages):
            yield "Done."
        elif next(replies) == "call":
            yield {
                0: DeltaToolCall(
                    name="exa_web_search_exa",
                    json_args=json.dumps({"query": "q"}),
                    tool_call_id="search",
                )
            }
        else:
            yield "No research needed."

    workspace = tmp_path / "course"
    workspace.mkdir()
    app = create_app(
        workspace,
        provider_store_path=tmp_path / "provider",
        chat_store_path=tmp_path / "chat",
        agent_model=FunctionModel(stream_function=model),
        provider_validator=_verified_capabilities,
        connector_connect=counting_connect,
    )
    async with _client(app) as client:
        await client.put(
            "/api/provider", json={"kind": "openrouter", "model": "m", "api_key": "secret"}
        )
        exa = (await client.get("/api/connectors")).json()[0]
        await client.post(f"/api/connectors/{exa['id']}/test")
        after_test = len(connections)
        await _run_agent(client, "Just think.")
        after_quiet_run = len(connections)
        await _run_agent(client, "Search.")

    assert after_test == 1
    assert after_quiet_run == 1
    assert len(connections) == 2
