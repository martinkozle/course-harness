"""Sync attached courses: fetch the mirror, pick the newest valid Release and serve it.

An invalid newer Release is skipped, logged and reported, and the previously served Release stays.
A failed fetch or an unreadable Release leaves the served Release untouched too. One course syncs
at a time per course; the periodic loop and "Sync now" share that lock. An Instructor's "Sync now"
runs in a background thread, so the request returns at once and the interface polls for the end.

An Instructor may start "Sync now" at most once per ``SYNC_NOW_COOLDOWN_SECONDS`` per course.

The Quizzes of Releases other than the served one, which Instructor Insights grade against or
compare with, come from :class:`ReleaseQuizCache`, which a fetch tells that tags may have moved.
"""

from __future__ import annotations

import logging
import re
import shutil
import threading
import time
from collections import defaultdict
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

from course_harness.core.releases import ReleaseError, newest_release
from course_harness_classroom.config import ClassroomSettings
from course_harness_classroom.insights import ReleaseQuizzes
from course_harness_classroom.mirror import MirrorError, fetch
from course_harness_classroom.release_quizzes import ReleaseQuizCache, snapshot_quizzes
from course_harness_classroom.repo_urls import InvalidRepoUrl
from course_harness_classroom.served import (
    ServedSnapshot,
    load_snapshot,
    prepare_release,
    remove_other_snapshots,
    snapshot_name,
)
from course_harness_classroom.store import Course, ServedRelease, Store, SyncStatus

logger = logging.getLogger(__name__)
SNAPSHOT_NAME = re.compile(r"[a-z0-9-]{1,64}-[0-9a-f]{12}\Z")
MAX_MESSAGE_LENGTH = 4_000
SYNC_NOW_COOLDOWN_SECONDS = 30.0


class CourseNotFound(LookupError):
    pass


@dataclass(frozen=True)
class SyncStart:
    """What asking for a background sync did: started one, found one running, or was refused
    because one started less than the cooldown ago."""

    outcome: Literal["started", "running", "cooling-down"]
    retry_after_seconds: int = 0


class SyncService:
    def __init__(self, store: Store, settings: ClassroomSettings) -> None:
        self.store = store
        self.settings = settings
        self._locks: defaultdict[int, threading.Lock] = defaultdict(threading.Lock)
        self._locks_guard = threading.Lock()
        self._snapshots: dict[Path, ServedSnapshot] = {}
        self._release_quizzes = ReleaseQuizCache()
        self._background: set[int] = set()
        self._background_started: dict[int, float] = {}
        self.sync_now_cooldown_seconds = SYNC_NOW_COOLDOWN_SECONDS

    def course_dir(self, course: Course) -> Path:
        return self.settings.courses_dir / str(course.id)

    def mirror_dir(self, course: Course) -> Path:
        return self.course_dir(course) / "mirror.git"

    def served_dir(self, course: Course) -> Path:
        return self.course_dir(course) / "served"

    def snapshot_dir(self, course: Course) -> Path | None:
        """The directory of the Release a course serves now, if any."""
        if course.release_slug is None or course.release_commit is None:
            return None
        name = f"{course.release_slug}-{course.release_commit[:12]}"
        directory = self.served_dir(course) / name
        return directory if (directory / "release.json").is_file() else None

    def snapshot(self, course: Course) -> ServedSnapshot | None:
        directory = self.snapshot_dir(course)
        return None if directory is None else self._load(directory)

    def snapshot_named(self, course: Course, name: str) -> tuple[Path, ServedSnapshot] | None:
        """A snapshot still on disk: the served one or, so open pages keep working after a
        switch, the one served before it."""
        if not SNAPSHOT_NAME.fullmatch(name):
            return None
        directory = self.served_dir(course) / name
        if not (directory / "release.json").is_file():
            return None
        return directory, self._load(directory)

    def _load(self, directory: Path) -> ServedSnapshot:
        cached = self._snapshots.get(directory)
        if cached is None:
            cached = load_snapshot(directory)
            self._snapshots[directory] = cached
        return cached

    def _lock(self, course_id: int) -> threading.Lock:
        with self._locks_guard:
            return self._locks[course_id]

    def sync(self, course_id: int) -> Course:
        """Sync one course now and return it with its recorded outcome."""
        with self._lock(course_id):
            course = self.store.course_by_id(course_id)
            if course is None:
                raise CourseNotFound(course_id)
            self._sync_locked(course)
        updated = self.store.course_by_id(course_id)
        if updated is None:
            raise CourseNotFound(course_id)
        return updated

    def start_background_sync(self, course_id: int) -> SyncStart:
        """Sync one course in a background thread, unless it already is or one started less than
        the cooldown ago."""
        moment = time.monotonic()
        with self._locks_guard:
            if course_id in self._background:
                return SyncStart("running")
            started = self._background_started.get(course_id)
            if started is not None and moment - started < self.sync_now_cooldown_seconds:
                wait = self.sync_now_cooldown_seconds - (moment - started)
                return SyncStart("cooling-down", retry_after_seconds=max(1, round(wait + 0.5)))
            self._background.add(course_id)
            self._background_started[course_id] = moment
        thread = threading.Thread(
            target=self._background_sync,
            args=(course_id,),
            name=f"classroom-sync-{course_id}",
            daemon=True,
        )
        thread.start()
        return SyncStart("started")

    def _background_sync(self, course_id: int) -> None:
        try:
            self.sync(course_id)
        except CourseNotFound:
            pass
        except Exception:
            logger.exception("Background sync of course %s failed unexpectedly", course_id)
        finally:
            with self._locks_guard:
                self._background.discard(course_id)

    def is_syncing(self, course_id: int) -> bool:
        with self._locks_guard:
            return course_id in self._background

    def release_quizzes(self, course: Course, tag: str) -> ReleaseQuizzes | None:
        """The Quizzes of a Release tag: from the served snapshot when it is the served Release,
        otherwise from the mirror, only for a tag it lists. ``None`` when the mirror has no such
        tag or the Release cannot be read; a failed read is retried next time."""
        snapshot = self.snapshot(course)
        if snapshot is not None and snapshot.manifest.tag == tag:
            return snapshot_quizzes(snapshot)
        try:
            return self._release_quizzes.get(course.id, self.mirror_dir(course), tag)
        except (ReleaseError, OSError, ValueError) as error:
            logger.warning("Release %s of course %s cannot be read: %s", tag, course.slug, error)
            return None

    def _sync_locked(self, course: Course) -> None:
        mirror = self.mirror_dir(course)
        # Release tags may move while the fetch runs; reads finishing meanwhile are not cached.
        self._release_quizzes.fetched(course.id)
        try:
            fetch(
                mirror,
                course.repo_url,
                timeout_seconds=self.settings.fetch_timeout_seconds,
                max_bytes=self.settings.max_mirror_bytes,
                allow_local=self.settings.allow_local_repos,
            )
        except (MirrorError, InvalidRepoUrl, OSError) as error:
            logger.warning("Sync of course %s failed: %s", course.slug, error)
            self._release_quizzes.fetched(course.id)
            self._record(course, "error", str(error))
            return
        self._release_quizzes.fetched(course.id)
        try:
            scan = newest_release(mirror)
        except (ReleaseError, OSError) as error:
            logger.warning("Sync of course %s failed: %s", course.slug, error)
            self._record(course, "error", str(error))
            return
        warnings = [f"Skipped Release {item.name}: {item.reason}" for item in scan.rejected]
        for warning in warnings:
            logger.warning("Course %s: %s", course.slug, warning)
        release = scan.newest
        if release is None:
            message = (
                "No valid Release was found."
                if warnings
                else "This repository has no published Release yet. In Course Harness, "
                "publish one with “Share to GitHub” ticked, then sync again."
            )
            logger.warning("Sync of course %s found no Release to serve", course.slug)
            self._record(course, "error", "\n".join([message, *warnings]))
            return
        if (
            course.release_slug == release.slug
            and course.release_commit == release.commit_oid
            and self.snapshot_dir(course) is not None
        ):
            self._record(course, "warning" if warnings else "ok", "\n".join(warnings) or None)
            return
        served_dir = self.served_dir(course)
        previous = self.snapshot_dir(course)
        try:
            prepared = prepare_release(
                mirror, release, served_dir=served_dir, course_slug=course.slug
            )
        except (ReleaseError, OSError, ValueError) as error:
            logger.warning("Course %s: Release %s was skipped: %s", course.slug, release.tag, error)
            warnings.append(f"Skipped Release {release.tag}: {error}")
            self._record(course, "error", "\n".join(warnings))
            return
        warnings += prepared.warnings
        for directory in [path for path in self._snapshots if path.parent == served_dir]:
            self._snapshots.pop(directory, None)
        self.store.record_sync(
            course.id,
            status="warning" if warnings else "ok",
            message=_message(warnings),
            served=ServedRelease(
                title=prepared.snapshot.title,
                slug=release.slug,
                name=release.name,
                commit=release.commit_oid,
                published_at=release.published_at,
            ),
        )
        keep = {snapshot_name(release)} | ({previous.name} if previous else set())
        remove_other_snapshots(served_dir, keep=keep)
        logger.info("Course %s now serves %s", course.slug, release.tag)

    def _record(self, course: Course, status: SyncStatus, message: str | None) -> None:
        self.store.record_sync(course.id, status=status, message=_message([message or ""]))

    def sync_all(self) -> None:
        for course in self.store.courses():
            try:
                self.sync(course.id)
            except CourseNotFound:
                continue
            except Exception:
                logger.exception("Sync of course %s failed unexpectedly", course.slug)

    def remove_course_data(self, course: Course) -> None:
        with self._lock(course.id):
            shutil.rmtree(self.course_dir(course), ignore_errors=True)
            course_dir = self.course_dir(course)
            for directory in [path for path in self._snapshots if course_dir in path.parents]:
                self._snapshots.pop(directory, None)
            self._release_quizzes.forget(course.id)


def _message(lines: list[str]) -> str | None:
    text = "\n".join(lines).strip()
    return text[:MAX_MESSAGE_LENGTH] or None


class PeriodicSync:
    """Sync every course on an interval in a daemon thread, until stopped."""

    def __init__(self, service: SyncService, interval_seconds: int) -> None:
        self.service = service
        self.interval_seconds = interval_seconds
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    def start(self) -> None:
        if self.interval_seconds <= 0 or self._thread is not None:
            return
        self._thread = threading.Thread(target=self._run, name="classroom-sync", daemon=True)
        self._thread.start()

    def _run(self) -> None:
        while not self._stop.is_set():
            self.service.sync_all()
            if self._stop.wait(self.interval_seconds):
                return

    def stop(self) -> None:
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=5)
            self._thread = None
