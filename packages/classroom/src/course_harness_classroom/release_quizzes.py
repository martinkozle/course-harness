"""The Quizzes of a course's Releases, for Instructor Insights to grade against or compare with.

The served Release's Quizzes come from its snapshot. Any other Release is read from the bare
mirror with ``course_harness.core``'s verified readers, but only when its tag is one the mirror
lists under ``refs/tags/course-release/``; anything else is refused before Git reads a byte.

:class:`ReleaseQuizCache` keeps what it read keyed by the tag's object ID, so a tag that a fetch
leaves alone stays cached, and a tag that moved or vanished is dropped when the tag list is next
read. The tag list is read once per fetch (a per-course generation counts fetches). A read that
finishes after a fetch started is returned but not cached, a failed read is never cached, and the
cache keeps at most a few dozen Releases, least recently used first out.
"""

from __future__ import annotations

import logging
import re
import threading
from collections import OrderedDict, defaultdict
from pathlib import Path

from course_harness.core.releases import (
    MAX_RELEASE_READ_BYTES,
    RELEASE_READ_SECONDS,
    SLUG_PATTERN,
    read_budget,
    read_verified_release,
    run_git,
    tagged_plan,
    tagged_quiz,
)
from course_harness_classroom.insights import ReleaseQuestion, ReleaseQuizzes
from course_harness_classroom.served import ServedSnapshot

logger = logging.getLogger(__name__)

MAX_CACHED_RELEASES = 32
TAG_PREFIX = "course-release/"
OBJECT_ID = re.compile(r"(?:[0-9a-f]{40}|[0-9a-f]{64})\Z")


def snapshot_quizzes(snapshot: ServedSnapshot) -> ReleaseQuizzes:
    return ReleaseQuizzes(
        tag=snapshot.manifest.tag,
        name=snapshot.manifest.name,
        published_at=snapshot.manifest.published_at,
        notes=snapshot.manifest.notes,
        questions={
            (lecture.id, question.id): ReleaseQuestion(
                lecture_id=lecture.id, lecture_title=lecture.title, question=question
            )
            for lecture in snapshot.lectures
            if lecture.quiz is not None
            for question in lecture.quiz.questions
        },
    )


def release_tags(mirror: Path) -> dict[str, str]:
    """Every well-formed Release tag the mirror has, with its tag object ID."""
    if not (mirror / "HEAD").is_file():
        return {}
    with read_budget(seconds=RELEASE_READ_SECONDS, max_bytes=MAX_RELEASE_READ_BYTES):
        output = run_git(
            mirror,
            "for-each-ref",
            "--format=%(objectname) %(refname:strip=2)",
            "refs/tags/course-release",
        )
    tags: dict[str, str] = {}
    for line in output.decode("ascii", errors="replace").splitlines():
        object_id, _, tag = line.partition(" ")
        slug = tag.removeprefix(TAG_PREFIX)
        if (
            OBJECT_ID.fullmatch(object_id)
            and tag.startswith(TAG_PREFIX)
            and SLUG_PATTERN.fullmatch(slug)
        ):
            tags[tag] = object_id
    return tags


def read_release_quizzes(mirror: Path, tag: str) -> ReleaseQuizzes | None:
    """Read and verify one Release's Quizzes from the mirror; ``None`` if there is no such tag.

    Raises ``ReleaseError`` (or ``OSError``) when the Release cannot be read or verified.
    """
    slug = tag.removeprefix(TAG_PREFIX)
    if not tag.startswith(TAG_PREFIX) or not SLUG_PATTERN.fullmatch(slug):
        return None
    with read_budget(seconds=RELEASE_READ_SECONDS, max_bytes=MAX_RELEASE_READ_BYTES):
        release = read_verified_release(mirror, slug)
        if release is None:
            return None
        plan = tagged_plan(mirror, release.commit_oid)
        titles = {lecture.id: lecture.title for lecture in plan.lectures}
        quizzes = {
            item.lecture_id: tagged_quiz(mirror, release.commit_oid, item.lecture_id)
            for item in release.quizzes
        }
    questions: dict[tuple[str, str], ReleaseQuestion] = {}
    for lecture_id in release.included_lecture_ids:
        quiz = quizzes.get(lecture_id)
        if quiz is None:
            continue
        for question in quiz.questions:
            questions.setdefault(
                (lecture_id, question.id),
                ReleaseQuestion(
                    lecture_id=lecture_id,
                    lecture_title=titles.get(lecture_id, lecture_id),
                    question=question,
                ),
            )
    return ReleaseQuizzes(
        tag=release.tag,
        name=release.name,
        published_at=release.published_at,
        questions=questions,
        notes=release.notes,
    )


class ReleaseQuizCache:
    def __init__(self, *, max_entries: int = MAX_CACHED_RELEASES) -> None:
        self.max_entries = max_entries
        self._guard = threading.Lock()
        self._generations: defaultdict[int, int] = defaultdict(int)
        self._tags: dict[int, tuple[int, dict[str, str]]] = {}
        self._entries: OrderedDict[tuple[int, str, str], ReleaseQuizzes] = OrderedDict()

    def fetched(self, course_id: int) -> None:
        """A fetch of the course's mirror started or ended: its tags may have changed."""
        with self._guard:
            self._generations[course_id] += 1
            self._tags.pop(course_id, None)

    def forget(self, course_id: int) -> None:
        with self._guard:
            self._generations[course_id] += 1
            self._tags.pop(course_id, None)
            for key in [key for key in self._entries if key[0] == course_id]:
                del self._entries[key]

    def tags(self, course_id: int, mirror: Path) -> dict[str, str]:
        """The mirror's Release tags, listed once per fetch; drops entries for tags that moved."""
        with self._guard:
            generation = self._generations[course_id]
            listed = self._tags.get(course_id)
            if listed is not None and listed[0] == generation:
                return listed[1]
        tags = release_tags(mirror)
        with self._guard:
            if self._generations[course_id] == generation:
                self._tags[course_id] = (generation, tags)
                for key in [key for key in self._entries if key[0] == course_id]:
                    if tags.get(key[1]) != key[2]:
                        del self._entries[key]
        return tags

    def get(self, course_id: int, mirror: Path, tag: str) -> ReleaseQuizzes | None:
        """A listed Release tag's Quizzes; ``None`` for a tag the mirror does not have.

        Raises ``ReleaseError`` or ``OSError`` when the Release cannot be read.
        """
        object_id = self.tags(course_id, mirror).get(tag)
        if object_id is None:
            return None
        key = (course_id, tag, object_id)
        with self._guard:
            generation = self._generations[course_id]
            cached = self._entries.get(key)
            if cached is not None:
                self._entries.move_to_end(key)
                return cached
        quizzes = read_release_quizzes(mirror, tag)
        if quizzes is None:
            return None
        with self._guard:
            if self._generations[course_id] == generation:
                self._entries[key] = quizzes
                self._entries.move_to_end(key)
                while len(self._entries) > self.max_entries:
                    self._entries.popitem(last=False)
        return quizzes

    def __len__(self) -> int:
        with self._guard:
            return len(self._entries)
