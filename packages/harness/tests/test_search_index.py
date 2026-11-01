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
