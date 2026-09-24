from typing import Any

import pytest
from pydantic_ai.mcp import MCPToolset

from course_harness import connector_tools
from course_harness.connectors import ResolvedConnector


@pytest.fixture(autouse=True)
def offline_connectors(monkeypatch: pytest.MonkeyPatch) -> None:
    """Keep the default Exa Connector from reaching the network in tests.

    A test that exercises Connectors passes its own connector_connect.
    """

    def unreachable(connector: ResolvedConnector) -> MCPToolset[Any]:
        return MCPToolset("http://127.0.0.1:9/mcp", id=connector.id, init_timeout=2)

    monkeypatch.setattr(connector_tools, "connect", unreachable)
