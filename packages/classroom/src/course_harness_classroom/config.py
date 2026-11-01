"""Classroom settings, read from the server environment.

- ``CLASSROOM_DATA_DIR``: mirrors, rendered backgrounds, served Releases and the SQLite store.
  Default ``./classroom-data``.
- ``CLASSROOM_ADMIN_TOKEN``: the Administrator token, at least 24 characters. Unset disables
  ``/admin``.
- ``CLASSROOM_PUBLIC_URL``: the external base URL, such as ``https://classroom.example.org``,
  used in Share Links and to decide the ``Secure`` cookie flag. Default: the request's own URL.
- ``CLASSROOM_FETCH_INTERVAL``: seconds between background syncs of every course; ``0`` turns
  them off. Default 900.
- ``CLASSROOM_FETCH_TIMEOUT``: seconds one ``git fetch`` may take. Default 300.
- ``CLASSROOM_MAX_MIRROR_BYTES``: the largest a course's bare mirror may grow on disk. Default
  1 GB.
- ``CLASSROOM_STATIC_DIR``: the built Classroom interface. Default: ``frontend/classroom/dist``
  of this checkout, built by ``bun run build``.
- ``CLASSROOM_ALLOW_LOCAL_REPOS``: ``1`` also accepts local paths, ``file://``, ``http://`` and
  private hosts. For development and tests only.
"""

from __future__ import annotations

import os
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlsplit

MIN_ADMIN_TOKEN_LENGTH = 24


class ConfigurationError(ValueError):
    """The server environment holds an invalid Classroom setting."""


@dataclass(frozen=True)
class ClassroomSettings:
    data_dir: Path
    admin_token: str | None = None
    public_url: str | None = None
    fetch_interval_seconds: int = 900
    fetch_timeout_seconds: int = 300
    max_mirror_bytes: int = 1_000_000_000
    allow_local_repos: bool = False
    static_dir: Path | None = None

    def __post_init__(self) -> None:
        if self.admin_token is not None and len(self.admin_token) < MIN_ADMIN_TOKEN_LENGTH:
            raise ConfigurationError(
                f"CLASSROOM_ADMIN_TOKEN must be at least {MIN_ADMIN_TOKEN_LENGTH} characters"
            )
        if self.public_url is not None:
            parts = urlsplit(self.public_url)
            if parts.scheme not in {"http", "https"} or not parts.hostname or parts.query:
                raise ConfigurationError("CLASSROOM_PUBLIC_URL must be an http(s) base URL")
        if self.fetch_interval_seconds < 0 or self.fetch_timeout_seconds <= 0:
            raise ConfigurationError("Fetch interval and timeout must be positive")
        if self.max_mirror_bytes <= 0:
            raise ConfigurationError("CLASSROOM_MAX_MIRROR_BYTES must be positive")

    @property
    def database_path(self) -> Path:
        return self.data_dir / "classroom.sqlite3"

    @property
    def courses_dir(self) -> Path:
        return self.data_dir / "courses"

    @classmethod
    def from_environment(cls, environ: Mapping[str, str] | None = None) -> ClassroomSettings:
        environ = os.environ if environ is None else environ

        def integer(name: str, default: int) -> int:
            raw = environ.get(name, "").strip()
            if not raw:
                return default
            try:
                return int(raw)
            except ValueError as error:
                raise ConfigurationError(f"{name} must be a whole number") from error

        public_url = environ.get("CLASSROOM_PUBLIC_URL", "").strip().rstrip("/") or None
        return cls(
            data_dir=Path(environ.get("CLASSROOM_DATA_DIR", "").strip() or "classroom-data"),
            admin_token=environ.get("CLASSROOM_ADMIN_TOKEN", "").strip() or None,
            public_url=public_url,
            fetch_interval_seconds=integer("CLASSROOM_FETCH_INTERVAL", 900),
            fetch_timeout_seconds=integer("CLASSROOM_FETCH_TIMEOUT", 300),
            max_mirror_bytes=integer("CLASSROOM_MAX_MIRROR_BYTES", 1_000_000_000),
            static_dir=Path(raw_static)
            if (raw_static := environ.get("CLASSROOM_STATIC_DIR", "").strip())
            else None,
            allow_local_repos=environ.get("CLASSROOM_ALLOW_LOCAL_REPOS", "").strip().lower()
            in {"1", "true", "yes"},
        )
