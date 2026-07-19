import sqlite3
from pathlib import Path

from pydantic import BaseModel, ConfigDict, Field


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


def open_search_db(cache_dir: Path) -> sqlite3.Connection:
    path = search_db_path(cache_dir)
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(path))
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
    conn = open_search_db(cache_dir)

    conn.execute("DELETE FROM source_content WHERE content_hash = ?", (content_hash,))
    conn.execute("DELETE FROM content_lines WHERE content_hash = ?", (content_hash,))

    offsets = _line_offsets(text)
    lines = text.split("\n")
    for line_number, line_text in enumerate(lines):
        conn.execute(
            "INSERT INTO source_content (content_hash, line_number, content) VALUES (?, ?, ?)",
            (content_hash, line_number, line_text),
        )
        conn.execute(
            "INSERT INTO content_lines (content_hash, line_number, char_offset) VALUES (?, ?, ?)",
            (content_hash, line_number, offsets[line_number]),
        )

    conn.commit()
    conn.close()


def deindex_resource(cache_dir: Path, content_hash: str) -> None:
    path = search_db_path(cache_dir)
    if not path.is_file():
        return
    conn = sqlite3.connect(str(path))
    conn.execute("DELETE FROM source_content WHERE content_hash = ?", (content_hash,))
    conn.execute("DELETE FROM content_lines WHERE content_hash = ?", (content_hash,))
    conn.commit()
    conn.close()


def search_raw(
    cache_dir: Path,
    query: str,
    *,
    limit: int = 10,
) -> list[_RawHit]:
    path = search_db_path(cache_dir)
    if not path.is_file():
        return []

    conn = sqlite3.connect(str(path))
    conn.row_factory = sqlite3.Row

    try:
        rows = conn.execute(
            """
            SELECT content_hash, line_number,
                   snippet(source_content, 2, '<mark>', '</mark>', '…', 32) AS snippet,
                   bm25(source_content, 0) AS rank
            FROM source_content
            WHERE source_content MATCH ?
            ORDER BY rank
            LIMIT ?
            """,
            (query, limit),
        ).fetchall()
    except sqlite3.OperationalError:
        conn.close()
        return []

    hits: list[_RawHit] = []
    seen_hashes: set[str] = set()
    for row in rows:
        content_hash = row["content_hash"]
        line_number = int(row["line_number"])
        snippet = row["snippet"] or ""
        rank = float(row["rank"])

        if content_hash in seen_hashes:
            continue
        seen_hashes.add(content_hash)

        hits.append(
            _RawHit(
                content_hash=content_hash,
                line_number=line_number,
                snippet=snippet,
                rank=rank,
            )
        )

    conn.close()
    return hits


def enrich_search_results(
    hits: list[_RawHit],
    sources_by_version: dict[str, tuple[str, str, str]],
) -> list[SearchResult]:
    results: list[SearchResult] = []
    for hit in hits:
        match = sources_by_version.get(hit.content_hash)
        if match is None:
            continue
        source_id, resource_id, label = match
        results.append(
            SearchResult(
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
        )
    return results


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
        extracted = derived / resource.snapshot_hash / "extracted.md"
        if not extracted.is_file():
            continue
        try:
            text = extracted.read_text(encoding="utf-8")
        except OSError, UnicodeError:
            continue
        index_resource(cache_dir, resource.snapshot_hash, text)


def _line_offsets(text: str) -> list[int]:
    offsets = [0]
    for i, char in enumerate(text):
        if char == "\n":
            offsets.append(i + 1)
    return offsets
