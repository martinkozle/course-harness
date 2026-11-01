import sqlite3
from concurrent.futures import ThreadPoolExecutor
from functools import partial
from pathlib import Path

import pytest

import course_harness.search as search


def test_parallel_source_indexing_takes_turns_instead_of_locking(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # A near-zero busy wait turns any contention between writers into "database is locked".
    connect = partial(sqlite3.connect, timeout=0.001)
    monkeypatch.setattr(search.sqlite3, "connect", lambda path, **_: connect(path))
    documents = {
        f"hash-{number}": "\n".join(f"line {line} of topic{number}" for line in range(20_000))
        for number in range(5)
    }

    with ThreadPoolExecutor(max_workers=5) as pool:
        for future in [
            pool.submit(search.index_resource, tmp_path, key, text)
            for key, text in documents.items()
        ]:
            future.result()

    for number, key in enumerate(documents):
        assert {hit.content_hash for hit in search.search_raw(tmp_path, f"topic{number}")} == {key}


MCP_NOTES = "\n".join(
    [
        "# Model Context Protocol",
        "Tools are model-controlled: the model decides when to call them.",
        "Resources are application-controlled context the client attaches.",
        "Prompts are user-controlled templates the user picks.",
        "Adoption grew 5.5% in the last quarter.",
        "Line 5 mentions nothing relevant.",
    ]
)


@pytest.mark.parametrize(
    ("query", "expected_lines"),
    [
        # The Course Agent's own queries from a real run; both used to fail as FTS5 syntax.
        (
            "model-controlled application-controlled user-controlled tools resources prompts",
            {0, 1, 2, 3},
        ),
        ("5.5%", {4}),
        ('tools "resources" NEAR(prompts) content:', {1, 2, 3}),
    ],
)
def test_search_treats_the_query_as_plain_words(
    tmp_path: Path, query: str, expected_lines: set[int]
) -> None:
    search.index_resource(tmp_path, "mcp", MCP_NOTES)

    hits = search.search_raw(tmp_path, query)

    assert {hit.line_number for hit in hits} == expected_lines
    assert all("<mark>" in hit.snippet for hit in hits)


def test_lines_with_every_word_come_before_lines_with_some(tmp_path: Path) -> None:
    search.index_resource(tmp_path, "mcp", MCP_NOTES)

    hits = search.search_raw(tmp_path, "How do application-controlled resources differ from tools?")

    assert hits[0].line_number == 2
    assert {hit.line_number for hit in hits} == {1, 2, 3}
    assert len(search.search_raw(tmp_path, "tools resources prompts", limit=2)) == 2


def test_search_stays_within_the_given_versions(tmp_path: Path) -> None:
    search.index_resource(tmp_path, "mcp", MCP_NOTES)
    search.index_resource(tmp_path, "other-workspace", "Tools tools tools everywhere.")

    hits = search.search_raw(tmp_path, "tools", content_hashes={"mcp"})

    assert {hit.content_hash for hit in hits} == {"mcp"}
    assert search.search_raw(tmp_path, "tools", content_hashes=set()) == []


def test_search_ignores_queries_without_words(tmp_path: Path) -> None:
    search.index_resource(tmp_path, "mcp", MCP_NOTES)

    assert search.search_raw(tmp_path, '"*:()-') == []
    assert search.query_tokens("What is the MCP?") == ["mcp"]
    assert search.query_tokens("the") == ["the"]
