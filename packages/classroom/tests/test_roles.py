"""Who may do what with a course: the Administrator as an Instructor of every course, Instructors
managing the Learner link, and Share Links shown again, including those made before tokens were
kept."""

import sqlite3
from dataclasses import replace
from typing import Any

import pytest
from fastapi.testclient import TestClient

from course_harness_classroom.config import ClassroomSettings
from course_harness_classroom.security import CSRF_HEADER, token_hash
from course_harness_classroom.store import MIGRATIONS, Store

QUESTION = {
    "lecture_id": "lecture-aaaaaaaaaaaa",
    "question_id": "question-aaaaaaaaaaaa",
    "option_id": "option-bbbbbbbbbbbb",
    "learner_name": "Ada Lovelace",
    "student_id": "s-1",
}


@pytest.fixture
def attached(admin: TestClient, course_repository: Any) -> None:
    course_repository.publish("fall-2026", "Fall 2026")
    response = admin.post(
        "/api/admin/courses", json={"repo_url": str(course_repository.remote), "slug": "causal"}
    )
    assert response.status_code == 201


def _open(make_client: Any, url: str) -> TestClient:
    client = make_client()
    response = client.get(url.removeprefix("http://localhost"))
    assert response.headers["location"] == "/c/causal"
    return client


def _admin_link(admin: TestClient, role: str) -> str:
    response = admin.post(f"/api/admin/courses/causal/links/{role}")
    assert response.status_code == 200, response.text
    return response.json()["url"]


def _links(client: TestClient) -> dict[str, Any]:
    response = client.get("/api/c/causal/links")
    assert response.status_code == 200, response.text
    return response.json()


def test_the_administrator_opens_any_course_as_an_instructor(
    admin: TestClient, attached: None
) -> None:
    # No Share Link exists yet: the Administrator session alone grants access.
    course = admin.get("/api/c/causal")
    assert course.status_code == 200
    assert course.json()["role"] == "instructor"
    assert course.json()["administrator"] is True
    assert admin.get("/api/c/causal/insights").status_code == 200
    assert admin.get("/api/c/causal/sync").status_code == 200
    assert _links(admin) == {"links": [], "can_rotate": ["learner", "instructor"]}

    answered = admin.post("/api/c/causal/answers", json=QUESTION)
    assert answered.status_code == 200
    # The Administrator's answers count as an Instructor's, left out of Insights by default.
    insights = admin.get("/api/c/causal/insights").json()
    assert insights["overview"]["hidden_instructor_answers"] == 1

    admin.delete("/api/admin/session")
    assert admin.get("/api/c/causal").status_code == 401


def test_a_changed_admin_token_ends_instructor_access_too(
    admin: TestClient, attached: None, make_client: Any, settings: ClassroomSettings
) -> None:
    restarted = make_client(replace(settings, admin_token="another-administrator-token-000"))
    restarted.cookies.update(admin.cookies)

    assert restarted.get("/api/c/causal").status_code == 401


def test_an_administrator_session_opens_no_course_that_is_not_attached(
    admin: TestClient, attached: None
) -> None:
    assert admin.get("/api/c/unknown").status_code == 401
    assert admin.get("/api/c/unknown/links").status_code == 401


def test_an_instructor_sees_both_links_and_rotates_only_the_learner_link(
    admin: TestClient, attached: None, make_client: Any
) -> None:
    learner_url = _admin_link(admin, "learner")
    instructor_url = _admin_link(admin, "instructor")
    learner = _open(make_client, learner_url)
    instructor = _open(make_client, instructor_url)
    assert instructor.get("/api/c/causal").json()["administrator"] is False

    links = _links(instructor)
    assert links["can_rotate"] == ["learner"]
    assert {link["role"]: link["url"] for link in links["links"]} == {
        "learner": learner_url,
        "instructor": instructor_url,
    }

    refused = instructor.post("/api/c/causal/links/instructor")
    assert refused.status_code == 403
    assert refused.json()["detail"] == "Only the Administrator can rotate the Instructor link."
    assert _links(instructor)["links"][1]["url"] == instructor_url

    rotated = instructor.post("/api/c/causal/links/learner")
    assert rotated.status_code == 200
    new_url = rotated.json()["url"]
    assert new_url.startswith("http://localhost/l/") and new_url != learner_url
    assert rotated.json()["rotated_at"] is not None
    # The old Learner link and its sessions end; the Instructor stays signed in.
    assert learner.get("/api/c/causal").status_code == 401
    old_link = make_client().get(learner_url.removeprefix("http://localhost"))
    assert old_link.headers["location"] == "/link-not-valid"
    assert instructor.get("/api/c/causal").status_code == 200
    _open(make_client, new_url)


def test_the_administrator_rotates_the_instructor_link_from_the_course(
    admin: TestClient, attached: None, make_client: Any
) -> None:
    instructor = _open(make_client, _admin_link(admin, "instructor"))

    rotated = admin.post("/api/c/causal/links/instructor")

    assert rotated.status_code == 200
    assert instructor.get("/api/c/causal").status_code == 401
    assert admin.get("/api/c/causal").status_code == 200
    _open(make_client, rotated.json()["url"])


def test_a_learner_can_neither_see_nor_rotate_links(
    admin: TestClient, attached: None, make_client: Any
) -> None:
    learner = _open(make_client, _admin_link(admin, "learner"))

    assert learner.get("/api/c/causal").json()["administrator"] is False
    assert learner.get("/api/c/causal/links").status_code == 403
    assert learner.post("/api/c/causal/links/learner").status_code == 403
    assert learner.post("/api/c/causal/links/instructor").status_code == 403
    assert make_client().get("/api/c/causal/links").status_code == 401


def test_attaching_and_detaching_stay_with_the_administrator(
    admin: TestClient, attached: None, make_client: Any, course_repository: Any
) -> None:
    instructor = _open(make_client, _admin_link(admin, "instructor"))

    assert instructor.get("/api/admin/courses").status_code == 401
    assert instructor.delete("/api/admin/courses/causal").status_code == 401
    assert (
        instructor.post(
            "/api/admin/courses", json={"repo_url": str(course_repository.remote)}
        ).status_code
        == 401
    )
    assert instructor.post("/api/admin/courses/causal/links/instructor").status_code == 401


def test_rotating_a_link_needs_the_request_header(admin: TestClient, attached: None) -> None:
    response = admin.post("/api/c/causal/links/learner", headers={CSRF_HEADER: ""})

    assert response.status_code == 403
    assert _links(admin)["links"] == []


def _version_2_database(settings: ClassroomSettings, token: str) -> None:
    """A Classroom database as it was before tokens were kept: a link stored by hash only."""
    settings.database_path.parent.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(settings.database_path, isolation_level=None)
    for number, script in enumerate(MIGRATIONS[:2], start=1):
        connection.executescript(f"BEGIN; {script} PRAGMA user_version = {number}; COMMIT;")
    connection.execute(
        "INSERT INTO courses (id, slug, repo_url, created_at) "
        "VALUES (1, 'causal', 'https://example.org/causal.git', '2026-10-01T09:00:00+00:00')"
    )
    connection.execute(
        "INSERT INTO share_links (course_id, role, token_hash, created_at) "
        "VALUES (1, 'learner', ?, '2026-10-01T09:00:00+00:00')",
        (token_hash(token),),
    )
    connection.close()


def test_the_migration_keeps_hash_only_links_working_and_asks_to_rotate_them(
    settings: ClassroomSettings, make_client: Any
) -> None:
    token = "legacy-learner-share-link-token-0123456789"
    _version_2_database(settings, token)

    store = Store(settings.database_path)

    with store.connect() as connection:
        assert connection.execute("PRAGMA user_version").fetchone()[0] == len(MIGRATIONS)
    link = store.share_links(1)["learner"]
    assert link.token is None
    admin = make_client()
    admin.post("/api/admin/session", json={"token": settings.admin_token})
    listed = admin.get("/api/admin/courses").json()[0]["links"]
    assert listed == [
        {
            "role": "learner",
            "created_at": "2026-10-01T09:00:00+00:00",
            "rotated_at": None,
            "url": None,
            "needs_rotation": True,
        }
    ]
    assert _links(admin)["links"][0]["needs_rotation"] is True
    # The old link still opens the course until it is rotated.
    assert make_client().get(f"/l/{token}").headers["location"] == "/c/causal"

    rotated = admin.post("/api/admin/courses/causal/links/learner").json()

    assert rotated["link"]["needs_rotation"] is False
    assert admin.get("/api/admin/courses").json()[0]["links"][0]["url"] == rotated["url"]
    assert make_client().get(f"/l/{token}").headers["location"] == "/link-not-valid"
