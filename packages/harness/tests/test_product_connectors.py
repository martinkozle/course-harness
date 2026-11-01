from unittest.mock import patch

import httpx2

from course_harness.product_connectors import product_connector_client


def test_product_connector_client_ignores_development_environment_credentials(
    monkeypatch,
) -> None:
    """Product connectors must not inherit a development MCP or proxy environment."""
    monkeypatch.setenv("MCP_DEVELOPMENT_TOKEN", "development-only-credential")
    observed: dict[str, object] = {}

    class RecordingClient:
        def __init__(self, **kwargs: object) -> None:
            observed.update(kwargs)

    with patch("course_harness.product_connectors.httpx2.AsyncClient", RecordingClient):
        product_connector_client(timeout=15, follow_redirects=True)

    assert observed == {
        "timeout": 15,
        "follow_redirects": True,
        "trust_env": False,
        "limits": httpx2.Limits(max_keepalive_connections=0),
    }
