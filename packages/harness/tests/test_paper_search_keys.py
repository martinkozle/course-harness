"""Paper Search Keys raise the native paper indexes' rate limits without leaving this computer."""

from pathlib import Path
from typing import Any
from unittest.mock import patch

import httpx2
import pytest

from course_harness.app import create_app

SECRET = "s2-secret-key-1234"
SEMANTIC_SCHOLAR_RESPONSE = {
    "data": [
        {
            "paperId": "abc123",
            "title": "An Introduction to Causal Inference",
            "authors": [{"name": "Judea Pearl"}],
            "year": 2010,
            "externalIds": {"DOI": "10.2202/1557-4679.1203"},
        }
    ]
}


async def _public_host_resolver(_hostname: str, _port: int) -> set[str]:
    return {"8.8.8.8"}


def _app(tmp_path: Path) -> Any:
    workspace = tmp_path / "course"
    workspace.mkdir(parents=True, exist_ok=True)
    return create_app(
        workspace,
        provider_store_path=tmp_path / "provider",
        chat_store_path=tmp_path / "chat",
        library_data_path=tmp_path / "library",
        library_cache_path=tmp_path / "cache",
        remote_host_resolver=_public_host_resolver,
    )


def _client(app: Any) -> httpx2.AsyncClient:
    return httpx2.AsyncClient(transport=httpx2.ASGITransport(app=app), base_url="http://test")


async def _search(client: httpx2.AsyncClient, respond: Any) -> tuple[dict[str, Any], list[Any]]:
    sent: list[httpx2.Request] = []

    def transport(request: httpx2.Request) -> httpx2.Response:
        sent.append(request)
        return respond(request)

    with patch(
        "course_harness.discovery.httpx2.AsyncClient",
        return_value=httpx2.AsyncClient(transport=httpx2.MockTransport(transport)),
    ):
        response = await client.post(
            "/api/discovery/search",
            json={"query": "causal inference", "providers": ["semantic_scholar"]},
        )
    return response.json()[0], sent


def _found(_request: httpx2.Request) -> httpx2.Response:
    return httpx2.Response(200, json=SEMANTIC_SCHOLAR_RESPONSE)


@pytest.mark.anyio
async def test_semantic_scholar_key_is_write_only_and_outside_the_workspace(
    tmp_path: Path,
) -> None:
    async with _client(_app(tmp_path)) as client:
        initial = (await client.get("/api/paper-search-keys")).json()
        saved = await client.put(
            "/api/paper-search-keys/semantic_scholar", json={"api_key": f" {SECRET} "}
        )
        listed = await client.get("/api/paper-search-keys")
        unknown = await client.put("/api/paper-search-keys/openalex", json={"api_key": "x"})
        empty = await client.put("/api/paper-search-keys/semantic_scholar", json={"api_key": ""})

    assert initial == [
        {
            "provider": "semantic_scholar",
            "label": "Semantic Scholar",
            "configured": False,
            "credential_storage": None,
        }
    ]
    assert saved.status_code == 200
    assert saved.json()[0]["configured"] is True
    assert saved.json()[0]["credential_storage"] == "private-json-file"
    assert unknown.status_code == 422
    assert empty.status_code == 422
    for body in (saved.text, listed.text):
        assert SECRET not in body
    credentials = tmp_path / "provider" / "paper-search-credentials" / "credentials.json"
    assert SECRET in credentials.read_text()
    assert f" {SECRET}" not in credentials.read_text()
    assert credentials.stat().st_mode & 0o077 == 0
    assert SECRET not in (tmp_path / "provider" / "paper-search-keys.json").read_text()
    for path in (tmp_path / "course").rglob("*"):
        assert not path.is_file() or SECRET not in path.read_text(errors="ignore")


@pytest.mark.anyio
async def test_a_saved_key_is_sent_to_semantic_scholar_until_it_is_removed(
    tmp_path: Path,
) -> None:
    async with _client(_app(tmp_path)) as client:
        _, anonymous = await _search(client, _found)
        await client.put("/api/paper-search-keys/semantic_scholar", json={"api_key": SECRET})
        keyed_result, keyed = await _search(client, _found)
        removed = await client.delete("/api/paper-search-keys/semantic_scholar")
        _, after_removal = await _search(client, _found)

    assert "x-api-key" not in anonymous[0].headers
    assert keyed[0].headers["x-api-key"] == SECRET
    assert keyed_result["error"] is None
    assert keyed_result["candidates"]
    assert removed.json()[0]["configured"] is False
    assert "x-api-key" not in after_removal[0].headers
    credentials = tmp_path / "provider" / "paper-search-credentials" / "credentials.json"
    assert not credentials.exists() or SECRET not in credentials.read_text()


@pytest.mark.anyio
async def test_a_rejected_key_is_reported_as_the_key(tmp_path: Path) -> None:
    async with _client(_app(tmp_path)) as client:
        await client.put("/api/paper-search-keys/semantic_scholar", json={"api_key": SECRET})
        result, _ = await _search(client, lambda _request: httpx2.Response(403))

    assert result["candidates"] == []
    assert "rejected the saved API key" in result["error"]
