from unittest.mock import patch

import httpx2
import pytest

from course_harness import resources as res
from course_harness.discovery import (
    _parse_arxiv,
    _parse_crossref,
    _parse_github,
    _parse_huggingface,
    discover,
    inspect_web_url,
)

ARXIV_RESPONSE = """<?xml version="1.0" encoding="UTF-8"?>
<feed xmlns="http://www.w3.org/2005/Atom"
      xmlns:arxiv="http://arxiv.org/schemas/atom">
  <entry>
    <id>http://arxiv.org/abs/1706.03762v7</id>
    <title>Attention Is All You Need</title>
    <summary>  The dominant sequence transduction models are based on complex recurrent
or convolutional neural networks. </summary>
    <published>2017-06-12T00:00:00Z</published>
    <author><name>Ashish Vaswani</name></author>
    <author><name>Noam Shazeer</name></author>
    <link href="http://arxiv.org/pdf/1706.03762v7" title="pdf" rel="related"/>
  </entry>
</feed>
"""

CROSSREF_RESPONSE = {
    "message": {
        "items": [
            {
                "DOI": "10.1234/example.1",
                "title": ["Attention Is All You Need"],
                "author": [
                    {"family": "Vaswani", "given": "Ashish"},
                    {"family": "Shazeer", "given": "Noam"},
                ],
                "abstract": "The dominant sequence transduction models.",
                "published-print": {"date-parts": [[2017, 6, 12]]},
            }
        ]
    }
}

GITHUB_RESPONSE = {
    "items": [
        {
            "full_name": "tensorflow/tensorflow",
            "owner": {"login": "tensorflow"},
            "description": "An Open Source Machine Learning Framework",
            "html_url": "https://github.com/tensorflow/tensorflow",
            "created_at": "2015-11-07T00:00:00Z",
        }
    ]
}

HUGGINGFACE_RESPONSE = [
    {
        "modelId": "meta-llama/Llama-3.1-8B",
        "author": "meta-llama",
        "pipeline_tag": "text-generation",
        "description": "The Meta Llama 3.1 collection",
        "lastModified": "2024-07-23T00:00:00Z",
    }
]


async def _public_host_resolver(_hostname: str, _port: int) -> set[str]:
    return {"8.8.8.8"}


def test_parse_arxiv() -> None:
    candidates = _parse_arxiv(ARXIV_RESPONSE)
    assert len(candidates) == 1
    c = candidates[0]
    assert c.provider == "arxiv"
    assert c.provider_id == "1706.03762v7"
    assert c.title == "Attention Is All You Need"
    assert c.authors == ["Ashish Vaswani", "Noam Shazeer"]
    assert c.media_type == "application/pdf"
    assert "recurrent" in (c.summary or "")
    assert "arxiv.org/pdf" in c.url


def test_parse_crossref() -> None:
    candidates = _parse_crossref(CROSSREF_RESPONSE)
    assert len(candidates) == 1
    c = candidates[0]
    assert c.provider == "crossref"
    assert c.provider_id == "10.1234/example.1"
    assert c.title == "Attention Is All You Need"
    assert c.authors == ["Ashish Vaswani", "Noam Shazeer"]
    assert "doi.org" in c.url
    assert c.published_at == "2017-6-12"


def test_parse_github() -> None:
    candidates = _parse_github(GITHUB_RESPONSE)
    assert len(candidates) == 1
    c = candidates[0]
    assert c.provider == "github"
    assert c.provider_id == "tensorflow/tensorflow"
    assert c.title == "tensorflow/tensorflow"
    assert c.authors == ["tensorflow"]
    assert "github.com/tensorflow/tensorflow" in c.url


def test_parse_huggingface() -> None:
    candidates = _parse_huggingface(HUGGINGFACE_RESPONSE)
    assert len(candidates) == 1
    c = candidates[0]
    assert c.provider == "huggingface"
    assert c.provider_id == "meta-llama/Llama-3.1-8B"
    assert c.title == "meta-llama/Llama-3.1-8B"
    assert c.authors == ["meta-llama"]
    assert "huggingface.co" in c.url


# --- Integration tests with mocked HTTP ---


def _mock_arxiv(request: httpx2.Request) -> httpx2.Response:
    return httpx2.Response(200, text=ARXIV_RESPONSE)


def _mock_crossref(request: httpx2.Request) -> httpx2.Response:
    return httpx2.Response(200, json=CROSSREF_RESPONSE)


def _mock_github(request: httpx2.Request) -> httpx2.Response:
    return httpx2.Response(200, json=GITHUB_RESPONSE)


def _mock_huggingface(request: httpx2.Request) -> httpx2.Response:
    return httpx2.Response(200, json=HUGGINGFACE_RESPONSE)


@pytest.mark.anyio
async def test_discover_runs_providers_concurrently() -> None:
    request = res.DiscoveryRequest(query="transformers", providers=["arxiv", "crossref"])

    def mock_transport(http_request: httpx2.Request) -> httpx2.Response:
        if "arxiv" in str(http_request.url):
            return _mock_arxiv(http_request)
        if "crossref" in str(http_request.url):
            return _mock_crossref(http_request)
        return httpx2.Response(500)

    with patch(
        "course_harness.discovery.httpx2.AsyncClient",
        return_value=httpx2.AsyncClient(transport=httpx2.MockTransport(mock_transport)),
    ):
        results = await discover(request)

    assert len(results) == 2
    providers = {r.provider for r in results}
    assert providers == {"arxiv", "crossref"}
    for r in results:
        assert r.error is None
        assert len(r.candidates) == 1


@pytest.mark.anyio
async def test_discover_captures_provider_errors() -> None:
    request = res.DiscoveryRequest(query="fail", providers=["arxiv"])

    def mock_transport(request: httpx2.Request) -> httpx2.Response:  # noqa: F811
        return httpx2.Response(500, text="Internal Server Error")

    with patch(
        "course_harness.discovery.httpx2.AsyncClient",
        return_value=httpx2.AsyncClient(transport=httpx2.MockTransport(mock_transport)),
    ):
        results = await discover(request)

    assert len(results) == 1
    assert results[0].provider == "arxiv"
    assert results[0].error is not None
    assert len(results[0].candidates) == 0


@pytest.mark.anyio
async def test_discover_does_not_follow_unvalidated_redirects() -> None:
    requested: list[str] = []

    def mock_transport(request: httpx2.Request) -> httpx2.Response:
        requested.append(str(request.url))
        return httpx2.Response(
            302,
            headers={"location": "http://127.0.0.1:9/private"},
        )

    request = res.DiscoveryRequest(query="redirect", providers=["arxiv"])
    with patch(
        "course_harness.discovery.httpx2.AsyncClient",
        return_value=httpx2.AsyncClient(transport=httpx2.MockTransport(mock_transport)),
    ):
        results = await discover(request)

    assert requested == [
        "https://export.arxiv.org/api/query?search_query=all:redirect&start=0&max_results=15"
        "&sortBy=relevance&sortOrder=descending"
    ]
    assert results[0].candidates == []
    assert results[0].error is not None


@pytest.mark.anyio
async def test_inspect_web_url() -> None:
    def mock_transport(http_request: httpx2.Request) -> httpx2.Response:
        return httpx2.Response(
            200,
            headers={
                "content-type": "text/html; charset=utf-8",
                "content-length": "12345",
            },
        )

    with patch(
        "course_harness.discovery.httpx2.AsyncClient",
        return_value=httpx2.AsyncClient(transport=httpx2.MockTransport(mock_transport)),
    ):
        candidate = await inspect_web_url(
            "https://example.com",
            host_resolver=_public_host_resolver,
        )

    assert candidate.provider == "web"
    assert candidate.url == "https://example.com"
    assert candidate.media_type == "text/html"
    assert candidate.size_bytes == 12345


@pytest.mark.anyio
async def test_inspect_web_url_revalidates_redirect_destinations() -> None:
    requested: list[tuple[str, str, str]] = []

    async def resolver(hostname: str, _port: int) -> set[str]:
        return {"127.0.0.1"} if hostname == "private.example" else {"8.8.8.8"}

    def mock_transport(request: httpx2.Request) -> httpx2.Response:
        requested.append(
            (str(request.url), request.headers["host"], request.extensions["sni_hostname"])
        )
        return httpx2.Response(302, headers={"location": "https://private.example/metadata"})

    async with httpx2.AsyncClient(transport=httpx2.MockTransport(mock_transport)) as client:
        with pytest.raises(ValueError, match="disallowed network address"):
            await inspect_web_url(
                "https://public.example/resource",
                client=client,
                host_resolver=resolver,
            )

    assert requested == [("https://8.8.8.8/resource", "public.example", "public.example")]
