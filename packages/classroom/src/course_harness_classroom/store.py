"""The Classroom's SQLite store: attached courses, Share Links and sessions.

Migrations are numbered SQL scripts applied in order and recorded in ``PRAGMA user_version``; a
later ticket appends a script, never edits an applied one. Each operation opens its own
connection, so the store is safe to use from request threads and the sync thread alike.
"""

from __future__ import annotations

import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Literal

Role = Literal["learner", "instructor"]
SessionKind = Literal["admin", "course"]
SyncStatus = Literal["pending", "ok", "warning", "error"]
ROLES: tuple[Role, ...] = ("learner", "instructor")

MIGRATIONS: tuple[str, ...] = (
    """
    CREATE TABLE courses (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        slug TEXT NOT NULL UNIQUE,
        repo_url TEXT NOT NULL UNIQUE,
        title TEXT,
        release_slug TEXT,
        release_name TEXT,
        release_commit TEXT,
        release_published_at TEXT,
        served_at TEXT,
        last_sync_at TEXT,
        sync_status TEXT NOT NULL DEFAULT 'pending'
            CHECK (sync_status IN ('pending', 'ok', 'warning', 'error')),
        sync_message TEXT,
        created_at TEXT NOT NULL
    );
    CREATE TABLE share_links (
        course_id INTEGER NOT NULL REFERENCES courses (id) ON DELETE CASCADE,
        role TEXT NOT NULL CHECK (role IN ('learner', 'instructor')),
        token_hash TEXT NOT NULL UNIQUE,
        created_at TEXT NOT NULL,
        rotated_at TEXT,
        PRIMARY KEY (course_id, role)
    );
    CREATE TABLE sessions (
        id_hash TEXT PRIMARY KEY,
        kind TEXT NOT NULL CHECK (kind IN ('admin', 'course')),
        course_id INTEGER REFERENCES courses (id) ON DELETE CASCADE,
        role TEXT CHECK (role IN ('learner', 'instructor')),
        created_at TEXT NOT NULL,
        expires_at TEXT NOT NULL
    );
    CREATE INDEX sessions_by_course ON sessions (course_id, role);
    """,
)


def now() -> datetime:
    return datetime.now(UTC)


def timestamp(moment: datetime | None = None) -> str:
    return (moment or now()).isoformat(timespec="seconds")


@dataclass(frozen=True)
class Course:
    id: int
    slug: str
    repo_url: str
    title: str | None
    release_slug: str | None
    release_name: str | None
    release_commit: str | None
    release_published_at: str | None
    served_at: str | None
    last_sync_at: str | None
    sync_status: SyncStatus
    sync_message: str | None
    created_at: str


@dataclass(frozen=True)
class ShareLink:
    course_id: int
    role: Role
    created_at: str
    rotated_at: str | None


@dataclass(frozen=True)
class Session:
    kind: SessionKind
    course_id: int | None
    role: Role | None
    expires_at: str


@dataclass(frozen=True)
class ServedRelease:
    """What a successful sync serves: the Release and the Course title it carries."""

    title: str
    slug: str
    name: str
    commit: str
    published_at: str


class DuplicateCourse(ValueError):
    """A course with this slug or repository is already attached."""


class Store:
    def __init__(self, path: Path) -> None:
        self.path = path
        path.parent.mkdir(parents=True, exist_ok=True)
        with self.connect() as connection:
            connection.execute("PRAGMA journal_mode = WAL")
            version = connection.execute("PRAGMA user_version").fetchone()[0]
            for number, script in enumerate(MIGRATIONS[version:], start=version + 1):
                connection.executescript(f"BEGIN; {script} PRAGMA user_version = {number}; COMMIT;")

    @contextmanager
    def connect(self) -> Iterator[sqlite3.Connection]:
        connection = sqlite3.connect(self.path, timeout=10, isolation_level=None)
        connection.row_factory = sqlite3.Row
        try:
            connection.execute("PRAGMA foreign_keys = ON")
            yield connection
        finally:
            connection.close()

    # Courses

    def add_course(self, *, slug: str, repo_url: str) -> Course:
        try:
            with self.connect() as connection:
                cursor = connection.execute(
                    "INSERT INTO courses (slug, repo_url, created_at) VALUES (?, ?, ?)",
                    (slug, repo_url, timestamp()),
                )
        except sqlite3.IntegrityError as error:
            raise DuplicateCourse("A course with this address or repository is attached") from error
        course = self.course_by_id(int(cursor.lastrowid or 0))
        assert course is not None
        return course

    def courses(self) -> list[Course]:
        with self.connect() as connection:
            rows = connection.execute("SELECT * FROM courses ORDER BY created_at, id").fetchall()
        return [Course(**dict(row)) for row in rows]

    def course_by_slug(self, slug: str) -> Course | None:
        with self.connect() as connection:
            row = connection.execute("SELECT * FROM courses WHERE slug = ?", (slug,)).fetchone()
        return Course(**dict(row)) if row else None

    def course_by_id(self, course_id: int) -> Course | None:
        with self.connect() as connection:
            row = connection.execute("SELECT * FROM courses WHERE id = ?", (course_id,)).fetchone()
        return Course(**dict(row)) if row else None

    def delete_course(self, course_id: int) -> None:
        with self.connect() as connection:
            connection.execute("DELETE FROM courses WHERE id = ?", (course_id,))

    def record_sync(
        self,
        course_id: int,
        *,
        status: SyncStatus,
        message: str | None,
        served: ServedRelease | None = None,
    ) -> None:
        """Record a sync's outcome and, when it switched Releases, the newly served one."""
        with self.connect() as connection:
            connection.execute(
                "UPDATE courses SET last_sync_at = ?, sync_status = ?, sync_message = ? "
                "WHERE id = ?",
                (timestamp(), status, message, course_id),
            )
            if served is not None:
                connection.execute(
                    "UPDATE courses SET title = ?, release_slug = ?, release_name = ?, "
                    "release_commit = ?, release_published_at = ?, served_at = ? WHERE id = ?",
                    (
                        served.title,
                        served.slug,
                        served.name,
                        served.commit,
                        served.published_at,
                        timestamp(),
                        course_id,
                    ),
                )

    # Share Links

    def share_links(self, course_id: int) -> dict[Role, ShareLink]:
        with self.connect() as connection:
            rows = connection.execute(
                "SELECT course_id, role, created_at, rotated_at FROM share_links "
                "WHERE course_id = ?",
                (course_id,),
            ).fetchall()
        return {row["role"]: ShareLink(**dict(row)) for row in rows}

    def set_share_link(self, course_id: int, role: Role, token_hash: str) -> None:
        """Create a course's Share Link for a role, or replace it and end its sessions."""
        moment = timestamp()
        with self.connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            updated = connection.execute(
                "UPDATE share_links SET token_hash = ?, rotated_at = ? "
                "WHERE course_id = ? AND role = ?",
                (token_hash, moment, course_id, role),
            ).rowcount
            if not updated:
                connection.execute(
                    "INSERT INTO share_links (course_id, role, token_hash, created_at) "
                    "VALUES (?, ?, ?, ?)",
                    (course_id, role, token_hash, moment),
                )
            connection.execute(
                "DELETE FROM sessions WHERE kind = 'course' AND course_id = ? AND role = ?",
                (course_id, role),
            )
            connection.execute("COMMIT")

    def share_link_by_hash(self, token_hash: str) -> ShareLink | None:
        with self.connect() as connection:
            row = connection.execute(
                "SELECT course_id, role, created_at, rotated_at FROM share_links "
                "WHERE token_hash = ?",
                (token_hash,),
            ).fetchone()
        return ShareLink(**dict(row)) if row else None

    # Sessions

    def add_session(
        self,
        id_hash: str,
        *,
        kind: SessionKind,
        lifetime: timedelta,
        course_id: int | None = None,
        role: Role | None = None,
    ) -> None:
        moment = now()
        with self.connect() as connection:
            connection.execute("DELETE FROM sessions WHERE expires_at < ?", (timestamp(moment),))
            connection.execute(
                "INSERT INTO sessions (id_hash, kind, course_id, role, created_at, expires_at) "
                "VALUES (?, ?, ?, ?, ?, ?)",
                (id_hash, kind, course_id, role, timestamp(moment), timestamp(moment + lifetime)),
            )

    def session(self, id_hash: str) -> Session | None:
        with self.connect() as connection:
            row = connection.execute(
                "SELECT kind, course_id, role, expires_at FROM sessions "
                "WHERE id_hash = ? AND expires_at >= ?",
                (id_hash, timestamp()),
            ).fetchone()
        return Session(**dict(row)) if row else None

    def delete_session(self, id_hash: str) -> None:
        with self.connect() as connection:
            connection.execute("DELETE FROM sessions WHERE id_hash = ?", (id_hash,))
