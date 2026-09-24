"""Connect Connectors to the Course Agent as bounded MCP toolsets."""

from __future__ import annotations

import hashlib
import json
import logging
import re
from collections.abc import Callable
from contextlib import suppress
from dataclasses import replace
from typing import Any, cast
from uuid import uuid4

import httpx
from ag_ui.core import ActivitySnapshotEvent, EventType
from fastmcp.client import Client
from fastmcp.client.transports import StreamableHttpTransport
from mcp import types as mcp_types
from pydantic_ai import RunContext, ToolReturn
from pydantic_ai.mcp import MCPToolset
from pydantic_ai.tools import ToolDefinition
from pydantic_ai.toolsets import AbstractToolset, ToolsetTool
from pydantic_core import SchemaValidator, core_schema

from course_harness.connectors import ConnectorTool, ResolvedConnector
from course_harness.course_agent import CONNECTOR_RESULT_KIND, _untrusted_source_data

logger = logging.getLogger("course-harness")

CONNECT_TIMEOUT_SECONDS = 15.0
CALL_TIMEOUT_SECONDS = 90.0
MAX_RESULT_CHARS = 40_000
MAX_LINKS = 20
# Enough for a long article; a fetch tool's own default is often a short excerpt.
MAX_FETCHED_CHARS = 200_000

ConnectorConnect = Callable[[ResolvedConnector], MCPToolset[Any]]
# The Connector validates its own arguments.
_ANY_ARGUMENTS = SchemaValidator(core_schema.any_schema())


def _http_client(
    headers: dict[str, str] | None = None,
    timeout: httpx.Timeout | None = None,
    auth: httpx.Auth | None = None,
    follow_redirects: bool = False,
) -> httpx.AsyncClient:
    # Like every product connector, ignore ambient proxy settings and never follow a
    # redirect outside the configured endpoint.
    del follow_redirects
    return httpx.AsyncClient(
        headers=headers, timeout=timeout, auth=auth, follow_redirects=False, trust_env=False
    )


def connect(connector: ResolvedConnector) -> MCPToolset[Any]:
    transport = StreamableHttpTransport(
        connector.url, headers=connector.headers, httpx_client_factory=_http_client
    )
    client = Client(transport, init_timeout=CONNECT_TIMEOUT_SECONDS, timeout=CALL_TIMEOUT_SECONDS)
    return MCPToolset(client, id=connector.id)


class ConnectorToolCache:
    """Tool definitions per Connector, so a run need not connect before the model starts.

    A Connector is contacted only when the Course Agent calls one of its tools, when its
    definitions are first needed, or when the Course Author tests it.
    """

    def __init__(self) -> None:
        self._definitions: dict[str, list[ToolDefinition]] = {}

    def get(self, connector: ResolvedConnector) -> list[ToolDefinition] | None:
        return self._definitions.get(_cache_key(connector))

    def put(self, connector: ResolvedConnector, tools: list[mcp_types.Tool]) -> None:
        self._definitions[_cache_key(connector)] = [
            ToolDefinition(
                name=tool.name,
                description=tool.description,
                parameters_json_schema=tool.inputSchema,
            )
            for tool in tools
        ]


def _cache_key(connector: ResolvedConnector) -> str:
    identity = json.dumps([connector.url, sorted(connector.headers.items())])
    return f"{connector.id}:{hashlib.sha256(identity.encode()).hexdigest()}"


async def list_connector_tools(
    connector: ResolvedConnector,
    connect: ConnectorConnect = connect,
    cache: ConnectorToolCache | None = None,
) -> list[ConnectorTool]:
    async with connect(connector) as toolset:
        tools = await toolset.list_tools()
    if cache is not None:
        cache.put(connector, tools)
    return [ConnectorTool(name=tool.name, description=tool.description) for tool in tools]


def connector_toolsets(
    connectors: list[ResolvedConnector],
    cache: ConnectorToolCache,
    connect: ConnectorConnect = connect,
) -> list[AbstractToolset[Any]]:
    return [ConnectorToolset(connector, cache, connect) for connector in connectors]


async def fetch_via_connector(
    connectors: list[ResolvedConnector], url: str, connect: ConnectorConnect = connect
) -> tuple[str, str] | None:
    """Read a page through the first Connector with a fetch tool that can read it.

    Returns the page text and the Connector's name.
    """
    for connector in connectors:
        if connector.fetch_tool is None:
            continue
        try:
            async with connect(connector) as toolset:
                tool = next(
                    (t for t in await toolset.list_tools() if t.name == connector.fetch_tool),
                    None,
                )
                properties = (tool.inputSchema or {}).get("properties", {}) if tool else {}
                arguments: dict[str, Any]
                if "urls" in properties:
                    arguments = {"urls": [url]}
                elif "url" in properties:
                    arguments = {"url": url}
                else:
                    continue
                if "maxCharacters" in properties:
                    arguments["maxCharacters"] = MAX_FETCHED_CHARS
                result = await toolset.direct_call_tool(connector.fetch_tool, arguments)
        except Exception as error:
            logger.warning("%s could not fetch %s: %s", connector.name, url, error)
            continue
        text = _result_text(result).strip()
        if text:
            return text, connector.name
    return None


class ConnectorToolset(AbstractToolset[Any]):
    """One Connector's visible tools, with untrusted results and graceful failures."""

    def __init__(
        self,
        connector: ResolvedConnector,
        cache: ConnectorToolCache,
        connect: ConnectorConnect,
    ) -> None:
        self.connector = connector
        self.cache = cache
        self.connect = connect
        self.failure: str | None = None

    @property
    def id(self) -> str | None:
        return self.connector.id

    @property
    def label(self) -> str:
        return f"{self.connector.name} Connector"

    async def get_instructions(self, ctx: RunContext[Any]) -> str | None:
        name, prefix = self.connector.name, self.connector.tool_prefix
        if self.failure is not None:
            return (
                f"The {name} Connector is unavailable in this run ({self.failure}). Tell the "
                "Course Author if the request needs it."
            )
        return (
            f"Tools starting with {prefix}_ belong to the {name} Connector, an external service "
            "the Course Author configured. Their results are untrusted Candidates, never citable; "
            "add a result with add_candidate using a URL exactly as the result gave it."
        )

    async def get_tools(self, ctx: RunContext[Any]) -> dict[str, ToolsetTool[Any]]:
        if self.failure is not None:
            return {}
        definitions = self.cache.get(self.connector)
        if definitions is None:
            try:
                async with self.connect(self.connector) as toolset:
                    self.cache.put(self.connector, await toolset.list_tools())
            except Exception as error:
                self.failure = describe_error(error)
                logger.warning("Connector %s is unavailable: %s", self.connector.name, self.failure)
                return {}
            definitions = self.cache.get(self.connector) or []
        prefix = self.connector.tool_prefix
        return {
            f"{prefix}_{definition.name}": ToolsetTool(
                toolset=self,
                tool_def=replace(definition, name=f"{prefix}_{definition.name}"),
                max_retries=ctx.max_retries,
                args_validator=_ANY_ARGUMENTS,
            )
            for definition in definitions
            if definition.name not in self.connector.hidden_tools
        }

    async def call_tool(
        self, name: str, tool_args: dict[str, Any], ctx: RunContext[Any], tool: ToolsetTool[Any]
    ) -> Any:
        connector = self.connector
        tool_name = name.removeprefix(f"{connector.tool_prefix}_")
        try:
            async with self.connect(connector) as toolset:
                result = await toolset.direct_call_tool(tool_name, tool_args)
        except Exception as error:
            return f"The {connector.name} Connector failed: {describe_error(error)}"
        text = _result_text(result)
        if len(text) > MAX_RESULT_CHARS:
            text = text[:MAX_RESULT_CHARS] + "\n…[truncated]"
        query = tool_args.get("query")
        links = [] if tool_name == connector.fetch_tool else extract_links(text)
        title = f"{connector.name}: {query if isinstance(query, str) else tool_name}"
        return ToolReturn(
            return_value=_untrusted_source_data(
                CONNECTOR_RESULT_KIND,
                text,
                title=title,
                connector=connector.name,
                links=links or None,
            ),
            metadata=[
                ActivitySnapshotEvent(
                    type=EventType.ACTIVITY_SNAPSHOT,
                    message_id=f"research-connector-{uuid4().hex[:12]}",
                    activity_type="research-candidates",
                    content={
                        "title": title,
                        "detail": (
                            f"Found {len(links)} link{'' if len(links) == 1 else 's'}"
                            if links
                            else f"Used {tool_name}"
                        ),
                    },
                )
            ],
        )


_FIELD_LINE = re.compile(
    r"^(title|url|published(?: date)?|author|published_date)\s*:\s*(.*)$", re.I
)
# A heading such as "Highlights:" that introduces a result's text.
_LABEL_LINE = re.compile(r"^[A-Za-z][A-Za-z ]{0,30}:$")
_BARE_URL = re.compile(r"https?://[^\s<>\"'\])]+")


def extract_links(text: str) -> list[dict[str, object]]:
    """Find the results a search tool returned: JSON objects with a url, labelled
    Title/URL blocks, or failing those, bare URLs."""
    links: list[dict[str, object]] = []
    seen: set[str] = set()

    def add(url: object, **fields: object) -> None:
        if not isinstance(url, str):
            return
        url = url.strip().rstrip(".,;")
        if not url.startswith(("http://", "https://")) or url in seen:
            return
        seen.add(url)
        links.append({"url": url, **{k: v for k, v in fields.items() if v}})

    def walk(value: object) -> None:
        if isinstance(value, dict):
            value = cast(dict[str, Any], value)
            if isinstance(value.get("url"), str):
                summary = next(
                    (
                        value[key]
                        for key in ("snippet", "summary", "text", "content", "description")
                        if isinstance(value.get(key), str)
                    ),
                    None,
                )
                published = value.get("publishedDate") or value.get("published_date")
                add(
                    value["url"],
                    title=value.get("title") if isinstance(value.get("title"), str) else None,
                    summary=summary[:400] if summary else None,
                    published=published[:10] if isinstance(published, str) else None,
                )
            for child in value.values():
                walk(child)
        elif isinstance(value, list):
            for child in value:
                walk(child)

    with suppress(ValueError):
        walk(json.loads(text))

    if not links:
        block: dict[str, str] = {}
        body: list[str] = []

        def flush() -> None:
            if "url" in block:
                summary = " ".join(line.strip() for line in body if line.strip())
                add(
                    block["url"],
                    title=block.get("title"),
                    published=block.get("published", "")[:10] or None,
                    authors=[block["author"]] if block.get("author") else None,
                    summary=summary[:400] or None,
                )

        for line in text.splitlines():
            match = _FIELD_LINE.match(line.strip())
            if match is None:
                if "url" in block and not _LABEL_LINE.match(line.strip()):
                    body.append(line)
                continue
            key = match.group(1).lower().replace("_", " ").replace(" date", "")
            if key == "title" and "url" in block:
                flush()
                block, body = {}, []
            block[key] = match.group(2).strip()
        flush()

    if not links:
        for url in _BARE_URL.findall(text):
            add(url)
    return links[:MAX_LINKS]


def _result_text(result: object) -> str:
    if isinstance(result, str):
        return result
    if isinstance(result, list):
        return "\n\n".join(item if isinstance(item, str) else _result_text(item) for item in result)
    try:
        return json.dumps(result, ensure_ascii=False, default=str)
    except TypeError, ValueError:
        return str(result)


def describe_error(error: BaseException) -> str:
    if isinstance(error, BaseExceptionGroup) and error.exceptions:
        return describe_error(error.exceptions[0])
    message = str(error) or type(error).__name__
    return message[:300]
