"""The Administrator's session, the cross-site request guard and course management."""

from dataclasses import replace
from typing import Any

from fastapi.testclient import TestClient

from course_harness_classroom.config import ClassroomSettings
from course_harness_classroom.security import ADMIN_COOKIE, CSRF_HEADER

ADMIN_TOKEN = "classroom-admin-token-for-tests-0123456789"


def test_admin_endpoints_require_a_session(make_client: Any) -> None:
    client = make_client()

    assert client.get("/api/admin/session").json() == {"configured": True, "authenticated": False}
    assert client.get("/api/admin/courses").status_code == 401
    assert client.post("/api/admin/courses", json={"repo_url": "/x"}).status_code == 401


def test_a_wrong_token_is_refused_and_repeated_failures_are_limited(make_client: Any) -> None:
    client = make_client()

    for _ in range(10):
        response = client.post("/api/admin/session", json={"token": "wrong"})
        assert response.status_code == 401
        assert ADMIN_COOKIE not in response.cookies
    assert client.post("/api/admin/session", json={"token": ADMIN_TOKEN}).status_code == 429


def test_the_token_is_exchanged_for_an_http_only_strict_cookie(make_client: Any) -> None:
    client = make_client()

    response = client.post("/api/admin/session", json={"token": ADMIN_TOKEN})

    assert response.status_code == 200
    cookie = response.headers["set-cookie"]
    assert cookie.startswith(f"{ADMIN_COOKIE}=")
    assert ADMIN_TOKEN not in cookie
    assert "HttpOnly" in cookie
    assert "SameSite=strict" in cookie
    assert "Path=/" in cookie
    assert "Secure" not in cookie
    assert client.get("/api/admin/courses").json() == []
    assert client.get("/api/admin/session").json()["authenticated"] is True

    assert client.delete("/api/admin/session").status_code == 204
    assert client.get("/api/admin/courses").status_code == 401


def test_the_cookie_is_secure_when_the_classroom_is_public_over_https(
    make_client: Any, settings: ClassroomSettings
) -> None:
    public = replace(settings, public_url="https://classroom.example.org")
    client = make_client(public)

    response = client.post(
        "/api/admin/session",
        json={"token": ADMIN_TOKEN},
        headers={"Origin": "https://classroom.example.org"},
    )

    assert response.status_code == 200
    assert "Secure" in response.headers["set-cookie"]


def test_a_forged_cookie_is_not_a_session(make_client: Any) -> None:
    client = make_client()
    client.cookies.set(ADMIN_COOKIE, "forged")

    assert client.get("/api/admin/courses").status_code == 401


def test_without_a_configured_token_nobody_can_sign_in(
    make_client: Any, settings: ClassroomSettings
) -> None:
    client = make_client(replace(settings, admin_token=None))

    assert client.get("/api/admin/session").json() == {"configured": False, "authenticated": False}
    assert client.post("/api/admin/session", json={"token": ""}).status_code == 422
    assert client.post("/api/admin/session", json={"token": ADMIN_TOKEN}).status_code == 503


def test_state_changing_requests_need_the_classroom_header(make_client: Any) -> None:
    client = make_client()
    client.headers.pop(CSRF_HEADER)

    response = client.post("/api/admin/session", json={"token": ADMIN_TOKEN})
    assert response.status_code == 403
    assert "set-cookie" not in response.headers

    response = client.post(
        "/api/admin/session", json={"token": ADMIN_TOKEN}, headers={CSRF_HEADER: "1"}
    )
    assert response.status_code == 200
    # A form post from another site cannot set the header; nor may another origin.
    assert client.delete("/api/admin/session").status_code == 403
    assert (
        client.delete(
            "/api/admin/session",
            headers={CSRF_HEADER: "1", "Origin": "https://attacker.example"},
        ).status_code
        == 403
    )
    assert client.get("/api/admin/courses").status_code == 200


def test_attach_list_and_detach(
    admin: TestClient, course_repository: Any, settings: ClassroomSettings
) -> None:
    course_repository.publish("fall-2026", "Fall 2026")

    attached = admin.post("/api/admin/courses", json={"repo_url": str(course_repository.remote)})

    assert attached.status_code == 201
    assert attached.json()["slug"] == "remote"
    assert [course["slug"] for course in admin.get("/api/admin/courses").json()] == ["remote"]
    duplicate = admin.post(
        "/api/admin/courses", json={"repo_url": str(course_repository.remote), "slug": "other"}
    )
    assert duplicate.status_code == 409
    assert (settings.courses_dir / "1").is_dir()

    assert admin.delete("/api/admin/courses/remote").status_code == 204
    assert admin.get("/api/admin/courses").json() == []
    assert not (settings.courses_dir / "1").exists()
    assert admin.delete("/api/admin/courses/remote").status_code == 404


def test_attaching_refuses_unsafe_addresses_and_slugs(
    make_client: Any, settings: ClassroomSettings
) -> None:
    client = make_client(replace(settings, allow_local_repos=False))
    client.post("/api/admin/session", json={"token": ADMIN_TOKEN})

    for url in ["http://github.com/a/b", "https://127.0.0.1/a.git", "/srv/course.git"]:
        response = client.post("/api/admin/courses", json={"repo_url": url})
        assert response.status_code == 422, url
    response = client.post(
        "/api/admin/courses", json={"repo_url": "https://github.com/a/b", "slug": "../x"}
    )
    assert response.status_code == 422
    assert client.get("/api/admin/courses").json() == []
