"""Share Links: creation, the token-for-cookie exchange, rotation and course access."""

import logging
from dataclasses import replace
from datetime import timedelta
from typing import Any

import pytest
from fastapi.testclient import TestClient

from course_harness_classroom.config import ClassroomSettings
from course_harness_classroom.security import ExchangeLimiter, RedactShareLinks, token_hash
from course_harness_classroom.store import Store

PRESENTATION_ID = "presentation-aaaaaaaaaaaa"
COOKIE = "classroom_course_causal"


@pytest.fixture
def attached(admin: TestClient, course_repository: Any) -> Any:
    release = course_repository.publish("fall-2026", "Fall 2026")
    response = admin.post(
        "/api/admin/courses", json={"repo_url": str(course_repository.remote), "slug": "causal"}
    )
    assert response.status_code == 201
    return release


def _link(admin: TestClient, role: str) -> str:
    response = admin.post(f"/api/admin/courses/causal/links/{role}")
    assert response.status_code == 200, response.text
    return response.json()["url"]


def _path(url: str) -> str:
    return url.removeprefix("http://localhost")


def test_a_share_link_is_shown_once_and_only_its_hash_is_stored(
    admin: TestClient, attached: Any, settings: ClassroomSettings
) -> None:
    url = _link(admin, "learner")

    assert url.startswith("http://localhost/l/")
    token = url.rsplit("/", 1)[1]
    assert len(token) >= 40
    course = admin.get("/api/admin/courses").json()[0]
    assert [link["role"] for link in course["links"]] == ["learner"]
    assert token not in str(course)
    database = settings.database_path.read_bytes()
    assert token.encode() not in database
    assert Store(settings.database_path).share_link_by_hash(token_hash(token)) is not None


def test_share_links_use_the_public_url(
    make_client: Any, settings: ClassroomSettings, course_repository: Any
) -> None:
    public = replace(settings, public_url="https://class.example.org")
    # Secure cookies travel only over HTTPS.
    client = make_client(public, base_url="https://testserver")
    client.post("/api/admin/session", json={"token": public.admin_token})
    client.post("/api/admin/courses", json={"repo_url": str(course_repository.remote)})

    response = client.post("/api/admin/courses/remote/links/instructor")

    assert response.json()["url"].startswith("https://class.example.org/i/")


def test_opening_a_link_swaps_the_token_for_a_cookie_and_a_clean_url(
    make_client: Any, admin: TestClient, attached: Any
) -> None:
    url = _link(admin, "learner")
    learner = make_client()

    response = learner.get(_path(url))

    assert response.status_code == 303
    assert response.headers["location"] == "/c/causal"
    assert response.headers["referrer-policy"] == "no-referrer"
    assert response.headers["cache-control"] == "no-store"
    cookie = response.headers["set-cookie"]
    assert cookie.startswith(f"{COOKIE}=")
    assert url.rsplit("/", 1)[1] not in cookie
    assert "HttpOnly" in cookie and "SameSite=strict" in cookie and "Path=/" in cookie

    course = learner.get("/api/c/causal")
    assert course.status_code == 200
    body = course.json()
    assert body["role"] == "learner"
    assert body["title"] == "Causal inference"
    assert body["release"]["tag"] == "course-release/fall-2026"
    assert body["lectures"][0]["title"] == "Foundations"
    assert body["lectures"][0]["has_quiz"] is True
    assert body["lectures"][0]["slide_count"] == 1


def test_a_link_works_only_for_its_own_role_and_course(
    make_client: Any, admin: TestClient, attached: Any
) -> None:
    url = _link(admin, "learner")
    visitor = make_client()

    as_instructor = visitor.get(_path(url).replace("/l/", "/i/"))
    assert as_instructor.headers["location"] == "/link-not-valid"
    assert visitor.get("/l/not-a-token").headers["location"] == "/link-not-valid"
    assert "set-cookie" not in visitor.get("/l/not-a-token").headers
    assert visitor.get("/api/c/causal").status_code == 401
    assert visitor.get("/api/c/unknown").status_code == 401


def test_rotating_a_link_invalidates_the_old_link_and_its_sessions(
    make_client: Any, admin: TestClient, attached: Any
) -> None:
    old = _link(admin, "learner")
    instructor_url = _link(admin, "instructor")
    learner = make_client()
    instructor = make_client()
    learner.get(_path(old))
    instructor.get(_path(instructor_url))
    assert learner.get("/api/c/causal").status_code == 200

    new = _link(admin, "learner")

    assert new != old
    assert learner.get("/api/c/causal").status_code == 401
    assert make_client().get(_path(old)).headers["location"] == "/link-not-valid"
    assert make_client().get(_path(new)).headers["location"] == "/c/causal"
    # Rotating the Learner link leaves Instructors signed in.
    assert instructor.get("/api/c/causal").json()["role"] == "instructor"
    link = admin.get("/api/admin/courses").json()[0]["links"][0]
    assert link["role"] == "learner" and link["rotated_at"] is not None


def test_detaching_a_course_ends_its_sessions(
    make_client: Any, admin: TestClient, attached: Any
) -> None:
    learner = make_client()
    learner.get(_path(_link(admin, "learner")))

    admin.delete("/api/admin/courses/causal")

    assert learner.get("/api/c/causal").status_code == 401


def test_the_release_pptx_and_backgrounds_need_the_course_cookie(
    make_client: Any, admin: TestClient, attached: Any
) -> None:
    learner = make_client()
    learner.get(_path(_link(admin, "learner")))
    download_url = learner.get("/api/c/causal").json()["lectures"][0]["download_url"]
    name = f"fall-2026-{attached.commit_oid[:12]}"
    assert download_url == f"/api/c/causal/assets/{name}/artifacts/{PRESENTATION_ID}.pptx"

    download = learner.get(download_url)

    assert download.status_code == 200
    assert download.content[:2] == b"PK"
    assert len(download.content) == attached.artifacts[0].size
    assert 'filename="foundations.pptx"' in download.headers["content-disposition"]
    assert make_client().get(download_url).status_code == 401
    for path in [
        f"/api/c/causal/assets/{name}/release.json",
        f"/api/c/causal/assets/{name}/../../mirror.git/HEAD",
        f"/api/c/causal/assets/{name}/artifacts/..%2frelease.json",
        f"/api/c/causal/assets/fall-2026-000000000000/artifacts/{PRESENTATION_ID}.pptx",
        f"/api/c/causal/assets/{name}/previews/_builtin-default/backgrounds/v1/title.png",
    ]:
        assert learner.get(path).status_code == 404, path


def test_share_link_exchanges_are_capped_per_address(
    make_client: Any, admin: TestClient, attached: Any
) -> None:
    url = _path(_link(admin, "learner"))
    visitor = make_client()

    statuses = [visitor.get("/l/guess").status_code for _ in range(30)]
    assert set(statuses) == {303}
    capped = visitor.get(url)
    assert capped.status_code == 429
    assert "set-cookie" not in capped.headers


def test_the_exchange_limiter_keeps_addresses_apart() -> None:
    limiter = ExchangeLimiter(attempts=2, window_seconds=60)

    assert limiter.allow("192.0.2.1") and limiter.allow("192.0.2.1")
    assert not limiter.allow("192.0.2.1")
    assert limiter.allow("192.0.2.2")


def test_share_link_tokens_are_redacted_from_the_access_log() -> None:
    record = logging.LogRecord(
        "uvicorn.access",
        logging.INFO,
        __file__,
        1,
        '%s - "%s %s HTTP/%s" %d',
        ("192.0.2.1:5000", "GET", "/l/secret-token-value?x=1", "1.1", 303),
        None,
    )

    assert RedactShareLinks().filter(record)
    assert "secret-token-value" not in record.getMessage()
    assert "/l/[redacted]?x=1" in record.getMessage()


def test_health_checks_are_left_out_of_the_access_log() -> None:
    record = logging.LogRecord(
        "uvicorn.access",
        logging.INFO,
        __file__,
        1,
        '%s - "%s %s HTTP/%s" %d',
        ("127.0.0.1:5000", "GET", "/healthz", "1.1", 200),
        None,
    )

    assert not RedactShareLinks().filter(record)


def test_a_session_is_only_opened_while_the_link_exists(
    admin: TestClient, attached: Any, settings: ClassroomSettings
) -> None:
    token = _link(admin, "learner").rsplit("/", 1)[1]
    store = Store(settings.database_path)
    _link(admin, "learner")  # rotated before the exchange commits

    opened = store.exchange_share_link(
        token_hash(token), "learner", session_hash="s" * 64, lifetime=timedelta(days=1)
    )

    assert opened is None
    assert store.session("s" * 64) is None
