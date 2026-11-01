"""The Classroom's SQLite store: attached courses, Share Links, sessions and Answers.

Migrations are numbered SQL scripts applied in order and recorded in ``PRAGMA user_version``; a
later ticket appends a script, never edits an applied one. Each operation opens its own
connection, so the store is safe to use from request threads and the sync thread alike.
"""

from __future__ import annotations

import sqlite3
from collections.abc import Callable, Iterator
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
        -- For an Administrator session, a fingerprint of the token it was opened with.
        admin_fingerprint TEXT,
        created_at TEXT NOT NULL,
        expires_at TEXT NOT NULL
    );
    CREATE INDEX sessions_by_course ON sessions (course_id, role);
    """,
    # Answers: one row per Option a Learner picked. Correctness is never stored; it is derived
    # against the served Release when read (ADR 0018). ``role`` tells Instructors' own tries
    # apart from Learners'.
    """
    CREATE TABLE answers (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        course_id INTEGER NOT NULL REFERENCES courses (id) ON DELETE CASCADE,
        release_tag TEXT NOT NULL,
        lecture_id TEXT NOT NULL,
        question_id TEXT NOT NULL,
        option_id TEXT NOT NULL,
        attempt INTEGER NOT NULL CHECK (attempt >= 1),
        learner_name TEXT NOT NULL,
        student_id TEXT NOT NULL,
        role TEXT NOT NULL CHECK (role IN ('learner', 'instructor')),
        answered_at TEXT NOT NULL
    );
    CREATE INDEX answers_by_student
        ON answers (course_id, student_id, lecture_id, question_id);
    CREATE INDEX answers_by_question ON answers (course_id, lecture_id, question_id);
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
    admin_fingerprint: str | None
    expires_at: str


@dataclass(frozen=True)
class ServedRelease:
    """What a successful sync serves: the Release and the Course title it carries."""

    title: str
    slug: str
    name: str
    commit: str
    published_at: str


@dataclass(frozen=True)
class NewAnswer:
    """An Option a Learner picked, before the store numbers the attempt."""

    course_id: int
    release_tag: str
    lecture_id: str
    question_id: str
    option_id: str
    learner_name: str
    student_id: str
    role: Role


@dataclass(frozen=True)
class Answer:
    id: int
    course_id: int
    release_tag: str
    lecture_id: str
    question_id: str
    option_id: str
    attempt: int
    learner_name: str
    student_id: str
    role: Role
    answered_at: str


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
        admin_fingerprint: str | None = None,
    ) -> None:
        with self.connect() as connection:
            _insert_session(
                connection,
                id_hash,
                kind=kind,
                lifetime=lifetime,
                course_id=course_id,
                role=role,
                admin_fingerprint=admin_fingerprint,
            )

    def exchange_share_link(
        self, token_hash: str, role: Role, *, session_hash: str, lifetime: timedelta
    ) -> Course | None:
        """Open a course session for a Share Link token, in one transaction with the check that
        the link still exists, so a concurrent rotation can never leave a session behind."""
        with self.connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute(
                "SELECT courses.* FROM share_links"
                " JOIN courses ON courses.id = share_links.course_id"
                " WHERE share_links.token_hash = ? AND share_links.role = ?",
                (token_hash, role),
            ).fetchone()
            if row is None:
                connection.execute("ROLLBACK")
                return None
            course = Course(**dict(row))
            _insert_session(
                connection,
                session_hash,
                kind="course",
                lifetime=lifetime,
                course_id=course.id,
                role=role,
            )
            connection.execute("COMMIT")
        return course

    def session(self, id_hash: str) -> Session | None:
        with self.connect() as connection:
            row = connection.execute(
                "SELECT kind, course_id, role, admin_fingerprint, expires_at FROM sessions "
                "WHERE id_hash = ? AND expires_at >= ?",
                (id_hash, timestamp()),
            ).fetchone()
        return Session(**dict(row)) if row else None

    def delete_session(self, id_hash: str) -> None:
        with self.connect() as connection:
            connection.execute("DELETE FROM sessions WHERE id_hash = ?", (id_hash,))

    # Answers

    def record_answer(
        self, answer: NewAnswer, *, settled: Callable[[list[str]], bool]
    ) -> Answer | None:
        """Record an Answer as the next attempt of its student on its Question.

        A Question is keyed by its Lecture and Question IDs. ``settled`` sees the Option IDs the
        student picked on it before, in order; when it says the Question is already answered
        correctly, or the student picked this Option before, nothing is recorded and ``None`` is
        returned, so an attempt number is the count of distinct Options tried. Reading and
        inserting happen in one write transaction, so concurrent Answers never share an attempt
        number.
        """
        with self.connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            try:
                earlier = [
                    row["option_id"]
                    for row in connection.execute(
                        "SELECT option_id FROM answers WHERE course_id = ? AND student_id = ? "
                        "AND lecture_id = ? AND question_id = ? ORDER BY id",
                        (
                            answer.course_id,
                            answer.student_id,
                            answer.lecture_id,
                            answer.question_id,
                        ),
                    )
                ]
                if answer.option_id in earlier or settled(earlier):
                    connection.execute("COMMIT")
                    return None
                moment = now().isoformat(timespec="milliseconds")
                cursor = connection.execute(
                    "INSERT INTO answers (course_id, release_tag, lecture_id, question_id, "
                    "option_id, attempt, learner_name, student_id, role, answered_at) "
                    "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                    (
                        answer.course_id,
                        answer.release_tag,
                        answer.lecture_id,
                        answer.question_id,
                        answer.option_id,
                        len(earlier) + 1,
                        answer.learner_name,
                        answer.student_id,
                        answer.role,
                        moment,
                    ),
                )
                connection.execute("COMMIT")
            except BaseException:
                connection.execute("ROLLBACK")
                raise
        return Answer(
            id=int(cursor.lastrowid or 0),
            course_id=answer.course_id,
            release_tag=answer.release_tag,
            lecture_id=answer.lecture_id,
            question_id=answer.question_id,
            option_id=answer.option_id,
            attempt=len(earlier) + 1,
            learner_name=answer.learner_name,
            student_id=answer.student_id,
            role=answer.role,
            answered_at=moment,
        )

    def answers(self, course_id: int, *, student_id: str | None = None) -> list[Answer]:
        """A course's Answers in the order they were given, optionally of one student."""
        query = "SELECT * FROM answers WHERE course_id = ?"
        parameters: tuple[object, ...] = (course_id,)
        if student_id is not None:
            query += " AND student_id = ?"
            parameters += (student_id,)
        with self.connect() as connection:
            rows = connection.execute(f"{query} ORDER BY id", parameters).fetchall()
        return [Answer(**dict(row)) for row in rows]


def _insert_session(
    connection: sqlite3.Connection,
    id_hash: str,
    *,
    kind: SessionKind,
    lifetime: timedelta,
    course_id: int | None,
    role: Role | None,
    admin_fingerprint: str | None = None,
) -> None:
    moment = now()
    connection.execute("DELETE FROM sessions WHERE expires_at < ?", (timestamp(moment),))
    connection.execute(
        "INSERT INTO sessions "
        "(id_hash, kind, course_id, role, admin_fingerprint, created_at, expires_at) "
        "VALUES (?, ?, ?, ?, ?, ?, ?)",
        (
            id_hash,
            kind,
            course_id,
            role,
            admin_fingerprint,
            timestamp(moment),
            timestamp(moment + lifetime),
        ),
    )
