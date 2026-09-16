import asyncio
import xml.etree.ElementTree as ET
from collections.abc import Callable
from typing import Any

import httpx2

from course_harness.product_connectors import product_connector_client
from course_harness.resources import (
    MAX_REMOTE_REDIRECTS,
    Candidate,
    DiscoveryRequest,
    DiscoveryResult,
    RemoteHostResolver,
    pin_remote_url,
    redirect_target,
    resolve_remote_host,
)

_ARXIV_NAMESPACES = {
    "atom": "http://www.w3.org/2005/Atom",
    "arxiv": "http://arxiv.org/schemas/atom",
}


async def search_arxiv(
    query: str,
    limit: int = 15,
    client: httpx2.AsyncClient | None = None,
) -> list[Candidate]:
    url = (
        "https://export.arxiv.org/api/query"
        f"?search_query=all:{httpx2.URL(query).raw_path.decode()}"
        f"&start=0&max_results={limit}"
        "&sortBy=relevance&sortOrder=descending"
    )
    return await _fetch_arxiv(client, url)


def _parse_arxiv(xml_text: str) -> list[Candidate]:
    root = ET.fromstring(xml_text)
    results: list[Candidate] = []
    for entry in root.findall("atom:entry", _ARXIV_NAMESPACES):
        title_el = entry.find("atom:title", _ARXIV_NAMESPACES)
        summary_el = entry.find("atom:summary", _ARXIV_NAMESPACES)
        id_el = entry.find("atom:id", _ARXIV_NAMESPACES)
        published_el = entry.find("atom:published", _ARXIV_NAMESPACES)

        authors: list[str] = []
        for author in entry.findall("atom:author", _ARXIV_NAMESPACES):
            name_el = author.find("atom:name", _ARXIV_NAMESPACES)
            if name_el is not None and name_el.text:
                authors.append(name_el.text.strip())

        arxiv_id = ""
        if id_el is not None and id_el.text:
            arxiv_id = id_el.text.rsplit("/", 1)[-1]

        pdf_url = ""
        for link in entry.findall("atom:link", _ARXIV_NAMESPACES):
            href = link.get("href", "")
            if link.get("title") == "pdf" and href:
                pdf_url = str(href)

        article_id = ""
        if id_el is not None and id_el.text:
            article_id = id_el.text

        results.append(
            Candidate(
                provider="arxiv",
                provider_id=arxiv_id,
                title=_elem_text(title_el),
                authors=authors if authors else None,
                summary=_elem_text(summary_el),
                url=pdf_url if pdf_url else article_id,
                media_type="application/pdf",
                published_at=_elem_text(published_el),
            )
        )
    return results


async def search_crossref(
    query: str,
    limit: int = 15,
    client: httpx2.AsyncClient | None = None,
) -> list[Candidate]:
    url = f"https://api.crossref.org/works?query={httpx2.URL(query).raw_path.decode()}&rows={limit}"
    return await _fetch_crossref(client, url)


def _parse_crossref(data: dict[str, Any]) -> list[Candidate]:
    items = data.get("message", {}).get("items", [])
    results: list[Candidate] = []
    for item in items:
        doi = item.get("DOI", "")
        title = ""
        title_list = item.get("title")
        if isinstance(title_list, list) and title_list:
            title = title_list[0] or ""
        authors: list[str] = []
        for author in item.get("author", []) or []:
            family = author.get("family", "")
            given = author.get("given", "")
            name = f"{given} {family}".strip()
            if name:
                authors.append(name)
        summary = item.get("abstract") or ""
        if isinstance(summary, str):
            summary = summary[:1000]
        url = f"https://doi.org/{doi}" if doi else ""
        published = ""
        published_date = item.get("published-print", {}).get("date-parts") or item.get(
            "created", {}
        ).get("date-parts")
        if published_date and isinstance(published_date, list) and published_date[0]:
            parts = published_date[0]
            published = "-".join(str(p) for p in parts)

        results.append(
            Candidate(
                provider="crossref",
                provider_id=doi,
                title=title or None,
                authors=authors if authors else None,
                summary=summary if isinstance(summary, str) else None,
                url=url,
                published_at=published or None,
            )
        )
    return results


async def search_github(
    query: str,
    limit: int = 15,
    client: httpx2.AsyncClient | None = None,
) -> list[Candidate]:
    url = (
        "https://api.github.com/search/repositories"
        f"?q={httpx2.URL(query).raw_path.decode()}"
        f"&per_page={limit}"
    )
    return await _fetch_github(client, url)


def _parse_github(data: dict[str, Any]) -> list[Candidate]:
    items = data.get("items", [])
    results: list[Candidate] = []
    for item in items:
        results.append(
            Candidate(
                provider="github",
                provider_id=item.get("full_name", ""),
                title=item.get("full_name"),
                authors=[item["owner"]["login"]] if item.get("owner", {}).get("login") else None,
                summary=item.get("description"),
                url=item.get("html_url", ""),
                published_at=item.get("created_at"),
            )
        )
    return results


async def search_huggingface(
    query: str,
    limit: int = 15,
    client: httpx2.AsyncClient | None = None,
) -> list[Candidate]:
    url = (
        "https://huggingface.co/api/models"
        f"?search={httpx2.URL(query).raw_path.decode()}"
        f"&limit={limit}"
    )
    return await _fetch_huggingface(client, url)


def _parse_huggingface(data: list[dict[str, Any]]) -> list[Candidate]:
    results: list[Candidate] = []
    for item in data:
        model_id = item.get("modelId", item.get("id", ""))
        authors: list[str] | None = None
        author_val = item.get("author")
        if isinstance(author_val, str) and author_val:
            authors = [author_val]
        summary = item.get("pipeline_tag") or ""
        if item.get("description"):
            summary = item["description"][:500] if isinstance(item["description"], str) else summary
        results.append(
            Candidate(
                provider="huggingface",
                provider_id=model_id,
                title=model_id,
                authors=authors,
                summary=summary if summary else None,
                url=f"https://huggingface.co/{model_id}" if model_id else "",
                published_at=item.get("lastModified"),
            )
        )
    return results


async def inspect_web_url(
    url: str,
    client: httpx2.AsyncClient | None = None,
    *,
    host_resolver: RemoteHostResolver | None = None,
) -> Candidate:
    resolver = host_resolver or resolve_remote_host
    if client is None:
        async with product_connector_client(timeout=15) as owned_client:
            return await _inspect_web(client=owned_client, url=url, host_resolver=resolver)
    return await _inspect_web(client=client, url=url, host_resolver=resolver)


async def _inspect_web(
    client: httpx2.AsyncClient,
    url: str,
    host_resolver: RemoteHostResolver,
) -> Candidate:
    current_url = url
    for redirect_count in range(MAX_REMOTE_REDIRECTS + 1):
        target = await pin_remote_url(current_url, host_resolver=host_resolver)
        response = await client.head(
            target.url,
            headers={"Host": target.host_header},
            extensions={"sni_hostname": target.sni_hostname},
            follow_redirects=False,
        )
        if response.is_redirect:
            if redirect_count == MAX_REMOTE_REDIRECTS:
                raise ValueError("Remote URL exceeded the redirect limit")
            current_url = redirect_target(current_url, response.headers.get("location"))
            continue
        response.raise_for_status()
        content_type = response.headers.get("content-type", "").split(";")[0].strip()
        content_length = response.headers.get("content-length")
        size: int | None = None
        if content_length is not None and content_length.isdigit():
            size = int(content_length)
        return Candidate(
            provider="web",
            provider_id=url,
            url=url,
            media_type=content_type or None,
            size_bytes=size,
        )
    raise AssertionError("Remote redirect loop did not return or raise")


PROVIDERS: dict[str, Callable[..., Any]] = {
    "arxiv": search_arxiv,
    "crossref": search_crossref,
    "github": search_github,
    "huggingface": search_huggingface,
}


async def discover(request: DiscoveryRequest) -> list[DiscoveryResult]:
    providers = request.providers or list(PROVIDERS)
    async with product_connector_client(timeout=15, follow_redirects=True) as client:
        tasks: list[asyncio.Task[DiscoveryResult]] = []
        for name in providers:
            fn = PROVIDERS.get(name)
            if fn is not None:
                tasks.append(
                    asyncio.create_task(
                        _run_provider(name, fn, request.query, request.limit, client)
                    )
                )
            else:
                tasks.append(asyncio.create_task(_noop_result(name)))
        return await asyncio.gather(*tasks)


async def _run_provider(
    name: str,
    fn: Callable[..., Any],
    query: str,
    limit: int,
    client: httpx2.AsyncClient,
) -> DiscoveryResult:
    try:
        candidates = await fn(query, limit, client=client)
        return DiscoveryResult(provider=name, candidates=candidates)
    except Exception as exc:
        return DiscoveryResult(provider=name, error=str(exc))


async def _noop_result(provider: str) -> DiscoveryResult:
    return DiscoveryResult(provider=provider)


# --- Internal HTTP helpers ---


async def _fetch_arxiv(
    client: httpx2.AsyncClient | None,
    url: str,
) -> list[Candidate]:
    if client is None:
        async with product_connector_client(timeout=15) as owned_client:
            return await _fetch_arxiv(client=owned_client, url=url)
    response = await client.get(url)
    response.raise_for_status()
    return _parse_arxiv(response.text)


async def _fetch_crossref(
    client: httpx2.AsyncClient | None,
    url: str,
) -> list[Candidate]:
    if client is None:
        async with product_connector_client(timeout=15) as owned_client:
            return await _fetch_crossref(client=owned_client, url=url)
    response = await client.get(url)
    response.raise_for_status()
    return _parse_crossref(response.json())


async def _fetch_github(
    client: httpx2.AsyncClient | None,
    url: str,
) -> list[Candidate]:
    if client is None:
        async with product_connector_client(timeout=15) as owned_client:
            return await _fetch_github(client=owned_client, url=url)
    response = await client.get(
        url,
        headers={"Accept": "application/vnd.github+json"},
    )
    response.raise_for_status()
    return _parse_github(response.json())


async def _fetch_huggingface(
    client: httpx2.AsyncClient | None,
    url: str,
) -> list[Candidate]:
    if client is None:
        async with product_connector_client(timeout=15) as owned_client:
            return await _fetch_huggingface(client=owned_client, url=url)
    response = await client.get(url)
    response.raise_for_status()
    return _parse_huggingface(response.json())


def _elem_text(element: ET.Element | None) -> str | None:
    if element is not None and element.text:
        return element.text.strip()
    return None
