"""Sync attached courses: fetch the mirror, pick the newest valid Release and serve it.

An invalid newer Release is skipped, logged and reported, and the previously served Release stays.
A failed fetch or an unreadable Release leaves the served Release untouched too. One course syncs
at a time per course; the periodic loop and "Sync now" share that lock.
"""

from __future__ import annotations

import logging
import re
import shutil
import threading
from collections import defaultdict
from pathlib import Path

from course_harness.core.releases import ReleaseError, newest_release
from course_harness_classroom.config import ClassroomSettings
from course_harness_classroom.mirror import MirrorError, fetch
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


class CourseNotFound(LookupError):
    pass


class SyncService:
    def __init__(self, store: Store, settings: ClassroomSettings) -> None:
        self.store = store
        self.settings = settings
        self._locks: defaultdict[int, threading.Lock] = defaultdict(threading.Lock)
        self._locks_guard = threading.Lock()
        self._snapshots: dict[Path, ServedSnapshot] = {}

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

    def _sync_locked(self, course: Course) -> None:
        mirror = self.mirror_dir(course)
        try:
            fetch(
                mirror,
                course.repo_url,
                timeout_seconds=self.settings.fetch_timeout_seconds,
                max_bytes=self.settings.max_mirror_bytes,
                allow_local=self.settings.allow_local_repos,
            )
            scan = newest_release(mirror)
        except (MirrorError, InvalidRepoUrl, ReleaseError, OSError) as error:
            logger.warning("Sync of course %s failed: %s", course.slug, error)
            self._record(course, "error", str(error))
            return
        warnings = [f"Skipped Release {item.name}: {item.reason}" for item in scan.rejected]
        for warning in warnings:
            logger.warning("Course %s: %s", course.slug, warning)
        release = scan.newest
        if release is None:
            self._record(course, "error", "\n".join(["No valid Release was found.", *warnings]))
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
