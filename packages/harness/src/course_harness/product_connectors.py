"""Outbound HTTP clients owned by Course Harness product connectors."""

import httpx2


def product_connector_client(
    *, timeout: float, follow_redirects: bool = False
) -> httpx2.AsyncClient:
    """Create an isolated client for a configured provider or public connector.

    Product connectors use only credentials supplied through their explicit
    configuration. In particular, they must not read development proxy or MCP
    environment configuration.
    """
    return httpx2.AsyncClient(
        timeout=timeout,
        follow_redirects=follow_redirects,
        trust_env=False,
        limits=httpx2.Limits(max_keepalive_connections=0),
    )
