"""Tokens, sessions, cookies and the HTTP security guards of the Classroom.

- Share Link tokens and session IDs are 256-bit random values; only their SHA-256 is stored, so
  a leaked database grants nothing. The Administrator token is compared in constant time and is
  only ever sent in a JSON body, never in a URL.
- Every session cookie is HttpOnly and SameSite=Strict, and Secure whenever the Classroom is
  reached over HTTPS (its public URL or the request itself).
- A state-changing request must carry the ``X-Classroom-Request`` header, which a cross-site
  form or image cannot send and a cross-origin script cannot send without a CORS preflight that
  the Classroom never answers; a foreign ``Origin`` is refused as well.
- Every response carries a strict Content Security Policy, ``Referrer-Policy: no-referrer``,
  ``X-Content-Type-Options: nosniff`` and refuses framing.
"""

from __future__ import annotations

import hashlib
import hmac
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
    "X-Frame-Options": "DENY",
    "Cross-Origin-Opener-Policy": "same-origin",
    "Cross-Origin-Resource-Policy": "same-origin",
}


def new_token() -> str:
    return secrets.token_urlsafe(32)


def token_hash(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


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


def uses_https(settings: ClassroomSettings, request: Request) -> bool:
    if settings.public_url is not None:
        return settings.public_url.startswith("https://")
    return request.url.scheme == "https"


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


class FailedLoginLimiter:
    """Refuse further Administrator logins from a client after repeated failures."""

    def __init__(self, *, attempts: int = 10, window_seconds: float = 300) -> None:
        self.attempts = attempts
        self.window_seconds = window_seconds
        self._failures: dict[str, deque[float]] = {}
        self._guard = threading.Lock()

    def _recent(self, client: str) -> deque[float]:
        failures = self._failures.setdefault(client, deque())
        cutoff = time.monotonic() - self.window_seconds
        while failures and failures[0] < cutoff:
            failures.popleft()
        return failures

    def blocked(self, client: str) -> bool:
        with self._guard:
            return len(self._recent(client)) >= self.attempts

    def record_failure(self, client: str) -> None:
        with self._guard:
            self._recent(client).append(time.monotonic())
            if len(self._failures) > 10_000:
                self._failures.clear()


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
