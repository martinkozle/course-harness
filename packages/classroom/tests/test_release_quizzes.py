"""The cache of Release Quizzes that Instructor Insights read from a course's mirror."""

from pathlib import Path
from typing import Any

import pytest

from course_harness.core.releases import ReleaseError
from course_harness_classroom import release_quizzes
from course_harness_classroom.insights import ReleaseQuizzes
from course_harness_classroom.release_quizzes import ReleaseQuizCache, release_tags

MIRROR = Path("/nonexistent/mirror.git")
COURSE = 7
FALL = "course-release/fall-2026"
WINTER = "course-release/winter-2026"
SPRING = "course-release/spring-2027"


class FakeMirror:
    """Stands in for Git: which tags the mirror lists, and every Release read made."""

    def __init__(self, monkeypatch: pytest.MonkeyPatch) -> None:
        self.tags = {FALL: "a" * 40, WINTER: "b" * 40}
        self.listings = 0
        self.reads: list[str] = []
        self.failures: set[str] = set()
        self.during_read = lambda: None
        monkeypatch.setattr(release_quizzes, "release_tags", self.list)
        monkeypatch.setattr(release_quizzes, "read_release_quizzes", self.read)

    def list(self, _mirror: Path) -> dict[str, str]:
        self.listings += 1
        return dict(self.tags)

    def read(self, _mirror: Path, tag: str) -> ReleaseQuizzes:
        self.reads.append(tag)
        self.during_read()
        if tag in self.failures:
            raise ReleaseError("Release read exceeded its time budget")
        return ReleaseQuizzes(tag=tag, name=tag, published_at="", questions={})


@pytest.fixture
def mirror(monkeypatch: pytest.MonkeyPatch) -> FakeMirror:
    return FakeMirror(monkeypatch)


def test_only_tags_the_mirror_lists_are_read(mirror: FakeMirror) -> None:
    cache = ReleaseQuizCache()

    assert cache.get(COURSE, MIRROR, "course-release/not-there") is None
    assert cache.get(COURSE, MIRROR, "refs/heads/main") is None
    assert mirror.reads == []
    assert cache.get(COURSE, MIRROR, FALL) is not None
    assert mirror.reads == [FALL]
    # The tag list is read once until the next fetch.
    assert mirror.listings == 1


def test_unchanged_tags_stay_cached_across_fetches_and_moved_ones_are_read_again(
    mirror: FakeMirror,
) -> None:
    cache = ReleaseQuizCache()
    cache.get(COURSE, MIRROR, FALL)
    cache.get(COURSE, MIRROR, WINTER)

    cache.fetched(COURSE)
    mirror.tags[WINTER] = "c" * 40
    cache.get(COURSE, MIRROR, FALL)
    cache.get(COURSE, MIRROR, WINTER)

    assert mirror.reads == [FALL, WINTER, WINTER]
    assert mirror.listings == 2
    assert len(cache) == 2

    cache.fetched(COURSE)
    del mirror.tags[WINTER]
    assert cache.get(COURSE, MIRROR, WINTER) is None
    assert len(cache) == 1


def test_a_failed_read_is_never_cached(mirror: FakeMirror) -> None:
    cache = ReleaseQuizCache()
    mirror.failures.add(FALL)
    with pytest.raises(ReleaseError):
        cache.get(COURSE, MIRROR, FALL)

    mirror.failures.clear()
    assert cache.get(COURSE, MIRROR, FALL) is not None
    assert mirror.reads == [FALL, FALL]


def test_a_read_overtaken_by_a_fetch_is_returned_but_not_cached(mirror: FakeMirror) -> None:
    cache = ReleaseQuizCache()
    cache.tags(COURSE, MIRROR)
    mirror.during_read = lambda: cache.fetched(COURSE)

    assert cache.get(COURSE, MIRROR, FALL) is not None
    assert len(cache) == 0


def test_the_cache_keeps_the_most_recently_used_releases(mirror: FakeMirror) -> None:
    mirror.tags[SPRING] = "d" * 40
    cache = ReleaseQuizCache(max_entries=2)
    cache.get(COURSE, MIRROR, FALL)
    cache.get(COURSE, MIRROR, WINTER)
    cache.get(COURSE, MIRROR, FALL)
    cache.get(COURSE, MIRROR, SPRING)

    assert len(cache) == 2
    cache.get(COURSE, MIRROR, FALL)
    cache.get(COURSE, MIRROR, WINTER)
    assert mirror.reads == [FALL, WINTER, SPRING, WINTER]


def test_forgetting_a_course_drops_its_entries_only(mirror: FakeMirror) -> None:
    cache = ReleaseQuizCache()
    cache.get(COURSE, MIRROR, FALL)
    cache.get(COURSE + 1, MIRROR, FALL)

    cache.forget(COURSE)

    assert len(cache) == 1


def test_release_tags_lists_the_release_tags_of_a_bare_repository(
    course_repository: Any, tmp_path: Path
) -> None:
    release = course_repository.publish("fall-2026", "Fall 2026")

    tags = release_tags(course_repository.remote)

    assert list(tags) == [release.tag]
    assert len(tags[release.tag]) in (40, 64)
    assert release_tags(tmp_path / "missing.git") == {}
