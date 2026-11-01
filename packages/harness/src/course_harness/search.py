import logging
import re
import sqlite3
import threading
from collections.abc import Iterable
from pathlib import Path

from pydantic import BaseModel, ConfigDict, Field

logger = logging.getLogger("course-harness")


class ContentCoordinates(BaseModel):
    model_config = ConfigDict(extra="forbid")

    line_start: int | None = None
    line_end: int | None = None
    page: int | None = None
    block: int | None = None


class SearchResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    source_id: str
    resource_id: str
    label: str
    snippet: str
    coordinates: ContentCoordinates
    rank: float


class GroupedSearchResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    source_id: str
    resource_id: str
    label: str
    max_rank: float
    chunks: list[SearchResult]


class SearchRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    query: str = Field(min_length=1, max_length=500)
    limit: int | None = Field(default=None, ge=1, le=50)


class _RawHit:
    __slots__ = ("content_hash", "line_number", "snippet", "rank")

    def __init__(self, content_hash: str, line_number: int, snippet: str, rank: float) -> None:
        self.content_hash = content_hash
        self.line_number = line_number
        self.snippet = snippet
        self.rank = rank


def search_db_path(cache_dir: Path) -> Path:
    return cache_dir / "fts" / "search.db"


# SQLite allows one writer at a time; parallel Source admissions take turns here
# instead of failing with "database is locked" after the default five seconds.
_WRITE_LOCK = threading.Lock()
WRITE_TIMEOUT_SECONDS = 60


def open_search_db(cache_dir: Path) -> sqlite3.Connection:
    path = search_db_path(cache_dir)
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(path), timeout=WRITE_TIMEOUT_SECONDS)
    conn.execute("PRAGMA journal_mode=WAL")

    cursor = conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name='source_content'"
    )
    if cursor.fetchone() is None:
        conn.execute(
            """
            CREATE VIRTUAL TABLE source_content USING fts5(
                content_hash,
                line_number,
                content
            )
            """
        )

    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS content_lines (
            content_hash TEXT NOT NULL,
            line_number INTEGER NOT NULL,
            char_offset INTEGER NOT NULL,
            PRIMARY KEY (content_hash, line_number)
        )
        """
    )
    conn.commit()
    return conn


def index_resource(cache_dir: Path, content_hash: str, text: str) -> None:
    offsets = _line_offsets(text)
    lines = text.split("\n")
    with _WRITE_LOCK:
        conn = open_search_db(cache_dir)
        try:
            conn.execute("DELETE FROM source_content WHERE content_hash = ?", (content_hash,))
            conn.execute("DELETE FROM content_lines WHERE content_hash = ?", (content_hash,))
            conn.executemany(
                "INSERT INTO source_content (content_hash, line_number, content) VALUES (?, ?, ?)",
                [(content_hash, number, line) for number, line in enumerate(lines)],
            )
            conn.executemany(
                "INSERT INTO content_lines (content_hash, line_number, char_offset) "
                "VALUES (?, ?, ?)",
                [(content_hash, number, offsets[number]) for number in range(len(lines))],
            )
            conn.commit()
        finally:
            conn.close()


def deindex_resource(cache_dir: Path, content_hash: str) -> None:
    path = search_db_path(cache_dir)
    if not path.is_file():
        return
    with _WRITE_LOCK:
        conn = sqlite3.connect(str(path), timeout=WRITE_TIMEOUT_SECONDS)
        try:
            conn.execute("DELETE FROM source_content WHERE content_hash = ?", (content_hash,))
            conn.execute("DELETE FROM content_lines WHERE content_hash = ?", (content_hash,))
            conn.commit()
        finally:
            conn.close()


def search_raw(
    cache_dir: Path,
    query: str,
    *,
    limit: int = 10,
    content_hashes: Iterable[str] | None = None,
) -> list[_RawHit]:
    """Find indexed lines for a plain-text query.

    The text is never passed to FTS5 as query syntax: it is reduced to quoted word tokens.
    Lines containing every token come first; when there are fewer than ``limit`` of them, the
    best lines containing any token fill the rest. ``content_hashes`` limits the search to
    those indexed versions, since the index is shared by every Course Workspace.
    """
    path = search_db_path(cache_dir)
    if not path.is_file():
        return []
    tokens = query_tokens(query)
    if not tokens:
        return []
    hashes = None if content_hashes is None else sorted(set(content_hashes))
    if hashes is not None and not hashes:
        return []

    quoted = [f'"{token}"' for token in tokens]
    expressions = [" AND ".join(quoted)]
    if len(tokens) > 1:
        expressions.append(" OR ".join(quoted))

    conn = sqlite3.connect(str(path))
    conn.row_factory = sqlite3.Row
    hits: list[_RawHit] = []
    seen: set[tuple[str, int]] = set()
    try:
        for expression in expressions:
            remaining = limit - len(hits)
            if remaining <= 0:
                break
            for row in _match(conn, f"content : ({expression})", hashes, limit):
                key = (row["content_hash"], int(row["line_number"]))
                if key in seen:
                    continue
                seen.add(key)
                hits.append(
                    _RawHit(
                        content_hash=key[0],
                        line_number=key[1],
                        snippet=row["snippet"] or "",
                        rank=float(row["rank"]),
                    )
                )
                if len(hits) >= limit:
                    break
    except sqlite3.OperationalError:
        logger.exception("Source search failed for query %r", query)
    finally:
        conn.close()
    return hits


MAX_QUERY_TOKENS = 12
# Decimal numbers such as 5.5 stay one token; FTS5 reads a quoted "5.5" as the phrase "5 5".
_TOKEN_PATTERN = re.compile(r"\d+(?:[.,]\d+)+|[^\W_]+")
_STOPWORDS = frozenset({
    "a", "an", "and", "are", "as", "at", "be", "by", "for", "from", "how", "in", "into", "is",
    "it", "of", "on", "or", "that", "the", "this", "to", "was", "what", "when", "where", "which",
    "who", "why", "with",
})  # fmt: skip


def query_tokens(query: str) -> list[str]:
    """Word tokens of a plain-text query: runs of Unicode letters and digits, split on everything
    else (hyphens and punctuation included), with decimal numbers kept whole.

    Common English stopwords are dropped unless nothing else is left; duplicates are dropped and
    at most ``MAX_QUERY_TOKENS`` are kept.
    """
    words = _TOKEN_PATTERN.findall(query.casefold())
    meaningful = [word for word in words if word not in _STOPWORDS] or words
    return list(dict.fromkeys(meaningful))[:MAX_QUERY_TOKENS]


def _match(
    conn: sqlite3.Connection, expression: str, hashes: list[str] | None, limit: int
) -> list[sqlite3.Row]:
    scope = ""
    parameters: list[object] = [expression]
    if hashes is not None:
        scope = f"AND content_hash IN ({', '.join('?' for _ in hashes)})"
        parameters.extend(hashes)
    parameters.append(limit)
    return conn.execute(
        f"""
        SELECT content_hash, line_number,
               snippet(source_content, 2, '<mark>', '</mark>', '…', 64) AS snippet,
               bm25(source_content, 0) AS rank
        FROM source_content
        WHERE source_content MATCH ? {scope}
        ORDER BY rank
        LIMIT ?
        """,  # noqa: S608 - only placeholders are interpolated
        parameters,
    ).fetchall()


def enrich_search_results(
    hits: list[_RawHit],
    sources_by_version: dict[str, tuple[str, str, str]],
) -> list[GroupedSearchResult]:
    by_source: dict[str, list[SearchResult]] = {}
    source_info: dict[str, tuple[str, str, str]] = {}
    for hit in hits:
        match = sources_by_version.get(hit.content_hash)
        if match is None:
            continue
        source_id, resource_id, label = match
        source_info[source_id] = match
        result = SearchResult(
            source_id=source_id,
            resource_id=resource_id,
            label=label,
            snippet=hit.snippet,
            coordinates=ContentCoordinates(
                line_start=hit.line_number,
                line_end=hit.line_number,
            ),
            rank=hit.rank,
        )
        by_source.setdefault(source_id, []).append(result)

    grouped: list[GroupedSearchResult] = []
    for source_id, chunks in by_source.items():
        chunks.sort(key=lambda c: c.coordinates.line_start or 0)
        sid, rid, lbl = source_info[source_id]
        max_rank = max(c.rank for c in chunks)
        grouped.append(
            GroupedSearchResult(
                source_id=sid,
                resource_id=rid,
                label=lbl,
                max_rank=max_rank,
                chunks=chunks,
            )
        )

    grouped.sort(key=lambda g: g.max_rank)
    return grouped


def rebuild_index(cache_dir: Path, data_dir: Path) -> None:
    from course_harness.library import derived_dir  # noqa: PLC0415
    from course_harness.resources import read_library_index  # noqa: PLC0415

    db_path = search_db_path(cache_dir)
    db_path.unlink(missing_ok=True)
    for wal_path in cache_dir.glob("fts/search.db-*"):
        wal_path.unlink(missing_ok=True)

    index = read_library_index(data_dir / "registry.json")
    derived = derived_dir(cache_dir)
    for resource in index.resources:
        if resource.snapshot_hash is None:
            continue
        # Representations kept for a pinning Source are searched under their own key.
        kept = sorted(path.name for path in derived.glob(f"{resource.snapshot_hash}@*"))
        for key in [resource.snapshot_hash, *kept]:
            extracted = derived / key / "extracted.md"
            if not extracted.is_file():
                continue
            try:
                text = extracted.read_text(encoding="utf-8")
            except OSError, UnicodeError:
                continue
            index_resource(cache_dir, key, text)


def _line_offsets(text: str) -> list[int]:
    offsets = [0]
    for i, char in enumerate(text):
        if char == "\n":
            offsets.append(i + 1)
    return offsets
