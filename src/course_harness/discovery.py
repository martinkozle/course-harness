import asyncio
import re
import xml.etree.ElementTree as ET
from collections.abc import Callable, Mapping
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

        doi_el = entry.find("arxiv:doi", _ARXIV_NAMESPACES)
        results.append(
            Candidate(
                provider="arxiv",
                provider_id=arxiv_id,
                arxiv_id=_versionless_arxiv_id(arxiv_id) or None,
                doi=_elem_text(doi_el),
                open_access_url=pdf_url or None,
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

        container = item.get("container-title")
        venue = container[0] if isinstance(container, list) and container else None
        results.append(
            Candidate(
                provider="crossref",
                provider_id=doi,
                doi=doi or None,
                venue=venue or None,
                citation_count=item.get("is-referenced-by-count"),
                title=title or None,
                authors=authors if authors else None,
                summary=summary if isinstance(summary, str) else None,
                url=url,
                published_at=published or None,
            )
        )
    return results


_SEMANTIC_SCHOLAR_FIELDS = (
    "title,authors,abstract,year,venue,externalIds,openAccessPdf,citationCount,url,publicationDate"
)


async def search_semantic_scholar(
    query: str,
    limit: int = 15,
    client: httpx2.AsyncClient | None = None,
    api_key: str | None = None,
) -> list[Candidate]:
    url = (
        "https://api.semanticscholar.org/graph/v1/paper/search"
        f"?query={httpx2.URL(query).raw_path.decode()}"
        f"&limit={limit}&fields={_SEMANTIC_SCHOLAR_FIELDS}"
    )
    # Without a key, requests share Semantic Scholar's public rate limit.
    headers = {"x-api-key": api_key} if api_key else None
    return await _fetch_json(client, url, _parse_semantic_scholar, headers)


def _parse_semantic_scholar(data: dict[str, Any]) -> list[Candidate]:
    results: list[Candidate] = []
    for item in data.get("data") or []:
        external = item.get("externalIds") or {}
        doi = external.get("DOI")
        arxiv_id = external.get("ArXiv")
        pdf = (item.get("openAccessPdf") or {}).get("url") or None
        landing = (
            f"https://doi.org/{doi}"
            if doi
            else f"https://arxiv.org/abs/{arxiv_id}"
            if arxiv_id
            else item.get("url") or ""
        )
        if not landing:
            continue
        authors = [a["name"] for a in item.get("authors") or [] if a.get("name")]
        abstract = item.get("abstract")
        results.append(
            Candidate(
                provider="semantic_scholar",
                provider_id=item.get("paperId", ""),
                title=item.get("title"),
                authors=authors or None,
                summary=abstract[:1000] if isinstance(abstract, str) else None,
                url=landing,
                published_at=item.get("publicationDate")
                or (str(item["year"]) if item.get("year") else None),
                doi=doi,
                arxiv_id=arxiv_id,
                venue=item.get("venue") or None,
                citation_count=item.get("citationCount"),
                open_access_url=pdf,
            )
        )
    return results


async def search_openalex(
    query: str,
    limit: int = 15,
    client: httpx2.AsyncClient | None = None,
) -> list[Candidate]:
    url = (
        "https://api.openalex.org/works"
        f"?search={httpx2.URL(query).raw_path.decode()}"
        f"&per_page={limit}"
    )
    return await _fetch_json(client, url, _parse_openalex)


def _parse_openalex(data: dict[str, Any]) -> list[Candidate]:
    results: list[Candidate] = []
    for item in data.get("results") or []:
        doi_url = item.get("doi") or ""
        doi = doi_url.removeprefix("https://doi.org/") or None
        ids = item.get("ids") or {}
        oa_location = item.get("best_oa_location") or {}
        primary = item.get("primary_location") or {}
        source = primary.get("source") or {}
        url = doi_url or primary.get("landing_page_url") or ids.get("openalex") or ""
        if not url:
            continue
        authors = [
            (authorship.get("author") or {}).get("display_name")
            for authorship in item.get("authorships") or []
        ]
        results.append(
            Candidate(
                provider="openalex",
                provider_id=ids.get("openalex") or item.get("id", ""),
                title=item.get("display_name") or item.get("title"),
                authors=[name for name in authors if name] or None,
                summary=_openalex_abstract(item.get("abstract_inverted_index")),
                url=url,
                published_at=item.get("publication_date"),
                doi=doi,
                arxiv_id=_arxiv_id_from_url(oa_location.get("landing_page_url")),
                venue=source.get("display_name") or None,
                citation_count=item.get("cited_by_count"),
                open_access_url=oa_location.get("pdf_url") or None,
            )
        )
    return results


def _openalex_abstract(inverted: object) -> str | None:
    """Rebuild an OpenAlex abstract from its word → positions index."""
    if not isinstance(inverted, dict) or not inverted:
        return None
    positions: dict[int, str] = {}
    for word, places in inverted.items():
        for place in places if isinstance(places, list) else []:
            if isinstance(place, int):
                positions[place] = str(word)
    text = " ".join(positions[index] for index in sorted(positions))
    return text[:1000] or None


def _versionless_arxiv_id(value: str) -> str:
    return re.sub(r"v\d+$", "", value)


def _arxiv_id_from_url(url: object) -> str | None:
    if not isinstance(url, str):
        return None
    match = re.search(r"arxiv\.org/(?:abs|pdf)/([^/?#]+?)(?:\.pdf)?(?:[?#]|$)", url)
    return _versionless_arxiv_id(match.group(1)) if match else None


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
    "semantic_scholar": search_semantic_scholar,
    "openalex": search_openalex,
    "github": search_github,
    "huggingface": search_huggingface,
}
PAPER_PROVIDERS = ("arxiv", "crossref", "semantic_scholar", "openalex")


async def discover(
    request: DiscoveryRequest, api_keys: Mapping[str, str] | None = None
) -> list[DiscoveryResult]:
    """Search each requested provider, sending its Paper Search Key when one is saved."""
    api_keys = api_keys or {}
    providers = request.providers or list(PROVIDERS)
    # A connector redirect may target a destination outside the connector's
    # disclosed boundary. Surface it as a provider error instead of following it.
    async with product_connector_client(timeout=15, follow_redirects=False) as client:
        tasks: list[asyncio.Task[DiscoveryResult]] = []
        for name in providers:
            fn = PROVIDERS.get(name)
            if fn is not None:
                tasks.append(
                    asyncio.create_task(
                        _run_provider(
                            name, fn, request.query, request.limit, client, api_keys.get(name)
                        )
                    )
                )
            else:
                tasks.append(asyncio.create_task(_noop_result(name)))
        return await asyncio.gather(*tasks)


async def search_papers(
    query: str,
    providers: list[str] | None = None,
    limit: int = 10,
    api_keys: Mapping[str, str] | None = None,
) -> tuple[list[Candidate], dict[str, str]]:
    """Search paper indexes and merge results that describe the same paper.

    Returns the merged Candidates, best-ranked first, and per-provider errors.
    """
    selected = [name for name in providers or PAPER_PROVIDERS if name in PAPER_PROVIDERS]
    results = await discover(
        DiscoveryRequest(query=query, providers=selected, limit=limit), api_keys=api_keys
    )
    errors = {result.provider: result.error for result in results if result.error}
    return merge_candidates([result.candidates for result in results])[:limit], errors


def merge_candidates(groups: list[list[Candidate]]) -> list[Candidate]:
    """Interleave provider rankings and merge Candidates with the same DOI or arXiv ID.

    A merged Candidate keeps the first provider's identity and fills gaps from
    the others, so a Crossref DOI match can gain an open-access PDF from OpenAlex.
    """
    merged: list[Candidate] = []
    by_key: dict[str, int] = {}
    longest = max((len(group) for group in groups), default=0)
    for rank in range(longest):
        for group in groups:
            if rank >= len(group):
                continue
            candidate = group[rank]
            keys = _identity_keys(candidate)
            position = next((by_key[key] for key in keys if key in by_key), None)
            if position is None:
                position = len(merged)
                merged.append(candidate)
            else:
                existing = merged[position]
                gaps = {
                    name: value
                    for name, value in candidate.model_dump().items()
                    if value is not None and getattr(existing, name) is None
                }
                merged[position] = existing.model_copy(update=gaps)
            for key in _identity_keys(merged[position]):
                by_key.setdefault(key, position)
    return merged


def _identity_keys(candidate: Candidate) -> list[str]:
    keys: list[str] = []
    if candidate.doi:
        keys.append(f"doi:{candidate.doi.lower()}")
    if candidate.arxiv_id:
        keys.append(f"arxiv:{_versionless_arxiv_id(candidate.arxiv_id).lower()}")
    if candidate.title:
        keys.append("title:" + re.sub(r"\W+", " ", candidate.title).strip().lower())
    return keys


async def _run_provider(
    name: str,
    fn: Callable[..., Any],
    query: str,
    limit: int,
    client: httpx2.AsyncClient,
    api_key: str | None = None,
) -> DiscoveryResult:
    try:
        keyed = {"api_key": api_key} if api_key else {}
        candidates = await fn(query, limit, client=client, **keyed)
        return DiscoveryResult(provider=name, candidates=candidates)
    except httpx2.HTTPStatusError as exc:
        status = exc.response.status_code
        if status == 429:
            detail = "is rate limiting requests; try again shortly"
        elif api_key and status in (401, 403):
            detail = "rejected the saved API key; check it in Settings"
        else:
            detail = "failed"
        return DiscoveryResult(provider=name, error=f"{name} {detail} (HTTP {status}).")
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


async def _fetch_json(
    client: httpx2.AsyncClient | None,
    url: str,
    parse: Callable[[Any], list[Candidate]],
    headers: dict[str, str] | None = None,
) -> list[Candidate]:
    if client is None:
        async with product_connector_client(timeout=15) as owned_client:
            return await _fetch_json(owned_client, url, parse, headers)
    response = await client.get(url, headers=headers)
    response.raise_for_status()
    return parse(response.json())


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
