from typing import Any

import pytest
from pydantic_ai.mcp import MCPToolset

from course_harness import connector_tools, discovery, docling_parser
from course_harness.connectors import ResolvedConnector


@pytest.fixture(autouse=True)
def offline_connectors(monkeypatch: pytest.MonkeyPatch) -> None:
    """Keep the default Exa Connector from reaching the network in tests.

    A test that exercises Connectors passes its own connector_connect.
    """

    def unreachable(connector: ResolvedConnector) -> MCPToolset[Any]:
        return MCPToolset("http://127.0.0.1:9/mcp", id=connector.id, init_timeout=2)

    monkeypatch.setattr(connector_tools, "connect", unreachable)


@pytest.fixture(autouse=True)
def unpaced_semantic_scholar(monkeypatch: pytest.MonkeyPatch) -> None:
    """Keep Semantic Scholar's one-request-per-second pacing and backoff out of unrelated tests."""
    monkeypatch.setattr(discovery, "SEMANTIC_SCHOLAR_PACER", discovery.RequestPacer(0))
    monkeypatch.setattr(discovery, "_SEMANTIC_SCHOLAR_BACKOFF", 0.0)


@pytest.fixture(autouse=True)
def without_full_docling(monkeypatch: pytest.MonkeyPatch) -> None:
    """Behave like a plain install, whether or not the course-harness[docling] extra is present.

    A test that needs full Docling patches full_docling_installed to return True.
    """
    monkeypatch.setattr(docling_parser, "full_docling_installed", lambda: False)
