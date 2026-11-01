"""Tokens, sessions, cookies and the HTTP security guards of the Classroom.

- Share Link tokens and session IDs are 256-bit random values. Session IDs are stored only as
  their SHA-256, so a leaked database opens no session. Share Link tokens are stored as well, so
  Instructors can show and copy a link again (ADR 0016); they are looked up by their hash. The
  Administrator token is compared in constant time and is only ever sent in a JSON body, never
  in a URL.
- An Administrator session is an Instructor of every course. Instructors may rotate the Learner
  link; only the Administrator rotates the Instructor link, attaches or detaches courses.
- Every session cookie is HttpOnly and SameSite=Strict, and Secure unless the public URL is
  plain HTTP on loopback. An Administrator session also records a fingerprint of the token it
  was opened with, so changing ``CLASSROOM_ADMIN_TOKEN`` ends every Administrator session.
- A wrong Administrator token costs a fixed delay; Share Link exchanges are capped per client
  address, and their tokens never reach the access log.
- A state-changing request must carry the ``X-Classroom-Request`` header, which a cross-site
  form or image cannot send and a cross-origin script cannot send without a CORS preflight that
  the Classroom never answers; a foreign ``Origin`` is refused as well.
- Every response carries a strict Content Security Policy, ``Referrer-Policy: no-referrer``,
  ``X-Content-Type-Options: nosniff`` and refuses framing (``frame-ancestors 'none'``).
"""

from __future__ import annotations

import hashlib
import hmac
import logging
import re
import secrets
import threading
import time
from collections import deque
from collections.abc import Awaitable, Callable
from datetime import timedelta
from urllib.parse import urlsplit

from fastapi import Request, Response
from fastapi.responses import JSONResponse

from course_harness_classroom.config import ClassroomSettings

ADMIN_COOKIE = "classroom_admin"
COURSE_COOKIE_PREFIX = "classroom_course_"
CSRF_HEADER = "X-Classroom-Request"
ADMIN_SESSION_LIFETIME = timedelta(hours=12)
COURSE_SESSION_LIFETIME = timedelta(days=180)
SAFE_METHODS = frozenset({"GET", "HEAD", "OPTIONS"})

CONTENT_SECURITY_POLICY = "; ".join(
    [
        "default-src 'self'",
        "script-src 'self'",
        "style-src 'self'",
        "img-src 'self' data:",
        "font-src 'self' data:",
        "connect-src 'self'",
        "object-src 'none'",
        "base-uri 'none'",
        "form-action 'self'",
        "frame-ancestors 'none'",
    ]
)
SECURITY_HEADERS = {
    "Content-Security-Policy": CONTENT_SECURITY_POLICY,
    "Referrer-Policy": "no-referrer",
    "X-Content-Type-Options": "nosniff",
    "Cross-Origin-Opener-Policy": "same-origin",
    "Cross-Origin-Resource-Policy": "same-origin",
}


def new_token() -> str:
    return secrets.token_urlsafe(32)


def token_hash(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def admin_fingerprint(settings: ClassroomSettings) -> str | None:
    """Identifies the configured Administrator token, so changing it ends existing sessions."""
    if settings.admin_token is None:
        return None
    return hashlib.sha256(f"classroom-admin-session\0{settings.admin_token}".encode()).hexdigest()


def admin_token_matches(settings: ClassroomSettings, candidate: str) -> bool:
    expected = settings.admin_token
    if expected is None:
        return False
    return hmac.compare_digest(
        hashlib.sha256(candidate.encode("utf-8")).digest(),
        hashlib.sha256(expected.encode("utf-8")).digest(),
    )


def course_cookie(slug: str) -> str:
    return f"{COURSE_COOKIE_PREFIX}{slug}"


LOOPBACK_HOSTS = frozenset({"localhost", "127.0.0.1", "::1"})


def secure_cookies(settings: ClassroomSettings) -> bool:
    """Cookies are Secure, except for a Classroom whose public URL is plain HTTP on loopback."""
    if settings.public_url is None:
        return True
    parts = urlsplit(settings.public_url)
    return not (parts.scheme == "http" and parts.hostname in LOOPBACK_HOSTS)


def public_base_url(settings: ClassroomSettings, request: Request) -> str:
    if settings.public_url is not None:
        return settings.public_url
    return str(request.base_url).rstrip("/")


def set_session_cookie(
    response: Response,
    *,
    name: str,
    value: str,
    lifetime: timedelta,
    secure: bool,
) -> None:
    response.set_cookie(
        name,
        value,
        max_age=int(lifetime.total_seconds()),
        path="/",
        httponly=True,
        samesite="strict",
        secure=secure,
    )


def clear_session_cookie(response: Response, *, name: str, secure: bool) -> None:
    response.delete_cookie(name, path="/", httponly=True, samesite="strict", secure=secure)


class ExchangeLimiter:
    """Cap how many Share Link exchanges one client address makes per minute.

    Each address has its own window, so one client guessing tokens never blocks another.
    """

    def __init__(self, *, attempts: int = 30, window_seconds: float = 60) -> None:
        self.attempts = attempts
        self.window_seconds = window_seconds
        self._attempts: dict[str, deque[float]] = {}
        self._guard = threading.Lock()

    def allow(self, client: str) -> bool:
        moment = time.monotonic()
        with self._guard:
            if len(self._attempts) > 10_000:
                self._attempts.clear()
            attempts = self._attempts.setdefault(client, deque())
            while attempts and attempts[0] < moment - self.window_seconds:
                attempts.popleft()
            if len(attempts) >= self.attempts:
                return False
            attempts.append(moment)
            return True


_SHARE_LINK_PATH = re.compile(r"^(/[li]/)[^?\s]*")


class RedactShareLinks(logging.Filter):
    """Keep Share Link tokens out of the access log: ``/l/<token>`` is logged as ``/l/…``.

    Health checks (``GET /healthz``, every 30 seconds from the container) are left out.
    """

    def filter(self, record: logging.LogRecord) -> bool:
        if isinstance(record.args, tuple) and len(record.args) >= 3:
            arguments = list(record.args)
            if arguments[2] == "/healthz":
                return False
            if isinstance(arguments[2], str):
                arguments[2] = _SHARE_LINK_PATH.sub(r"\1[redacted]", arguments[2])
                record.args = tuple(arguments)
        return True


def install_access_log_redaction() -> None:
    access = logging.getLogger("uvicorn.access")
    if not any(isinstance(item, RedactShareLinks) for item in access.filters):
        access.addFilter(RedactShareLinks())


class RateLimiter:
    """Allow at most ``limit`` events per key within a sliding window of ``window_seconds``."""

    def __init__(self, *, limit: int, window_seconds: float = 60) -> None:
        self.limit = limit
        self.window_seconds = window_seconds
        self._events: dict[str, deque[float]] = {}
        self._guard = threading.Lock()

    def allow(self, key: str) -> bool:
        """Count one event for ``key`` unless that would exceed the limit."""
        moment = time.monotonic()
        with self._guard:
            if len(self._events) > 50_000:
                self._events = {
                    name: events
                    for name, events in self._events.items()
                    if events and events[-1] >= moment - self.window_seconds
                }
            events = self._events.setdefault(key, deque())
            while events and events[0] < moment - self.window_seconds:
                events.popleft()
            if len(events) >= self.limit:
                return False
            events.append(moment)
            return True


def _expected_origins(settings: ClassroomSettings, request: Request) -> set[str]:
    origins = {f"{request.url.scheme}://{request.url.netloc}"}
    if settings.public_url is not None:
        parts = urlsplit(settings.public_url)
        origins.add(f"{parts.scheme}://{parts.netloc}")
    return origins


def security_middleware(
    settings: ClassroomSettings,
) -> Callable[[Request, Callable[[Request], Awaitable[Response]]], Awaitable[Response]]:
    async def middleware(
        request: Request, call_next: Callable[[Request], Awaitable[Response]]
    ) -> Response:
        if request.method not in SAFE_METHODS:
            origin = request.headers.get("origin")
            if request.headers.get(CSRF_HEADER) != "1" or (
                origin is not None and origin not in _expected_origins(settings, request)
            ):
                response: Response = JSONResponse(
                    {"detail": "This request was refused as a possible cross-site request."},
                    status_code=403,
                )
            else:
                response = await call_next(request)
        else:
            response = await call_next(request)
        for name, value in SECURITY_HEADERS.items():
            response.headers.setdefault(name, value)
        if request.url.path.startswith("/api/"):
            response.headers.setdefault("Cache-Control", "no-store")
        return response

    return middleware
