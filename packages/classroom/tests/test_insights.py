"""Instructor Insights over HTTP: role gating, grading against Releases, CSV and "Sync now"."""

import time
from typing import Any
from urllib.parse import urlsplit

import pytest
from fastapi.testclient import TestClient

from course_harness.core.quiz import Quiz, QuizOption, QuizQuestion, write_quiz
from course_harness.workspace_history import (
    RevisionCreateRequest,
    create_revision,
    record_app_authored_state,
)

LECTURE_ID = "lecture-aaaaaaaaaaaa"
QUESTION_ID = "question-aaaaaaaaaaaa"
CONFOUNDING = "option-aaaaaaaaaaaa"
SAMPLING = "option-bbbbbbbbbbbb"
MEASUREMENT = "option-cccccccccccc"
FALL = "course-release/fall-2026"
WINTER = "course-release/winter-2026"


def _join(make_client: Any, admin: TestClient, role: str) -> TestClient:
    url = admin.post(f"/api/admin/courses/causal/links/{role}").json()["url"]
    client = make_client()
    assert client.get(urlsplit(url).path).status_code == 303
    return client


@pytest.fixture
def classroom(make_client: Any, admin: TestClient, course_repository: Any) -> dict[str, Any]:
    course_repository.publish("fall-2026", "Fall 2026")
    attached = admin.post(
        "/api/admin/courses", json={"repo_url": str(course_repository.remote), "slug": "causal"}
    )
    assert attached.status_code == 201
    return {
        "admin": admin,
        "repository": course_repository,
        "learner": _join(make_client, admin, "learner"),
        "instructor": _join(make_client, admin, "instructor"),
    }


def _answer(client: TestClient, option_id: str, student_id: str, name: str = "Ada") -> None:
    response = client.post(
        "/api/c/causal/answers",
        json={
            "lecture_id": LECTURE_ID,
            "question_id": QUESTION_ID,
            "option_id": option_id,
            "learner_name": name,
            "student_id": student_id,
        },
    )
    assert response.status_code == 200, response.text


def _publish_with_key(repository: Any, correct: str, slug: str, name: str) -> None:
    """Publish a Release whose only Question has a different correct Option."""
    time.sleep(1.1)  # Releases are ordered by tag creation time, in whole seconds
    texts = {
        CONFOUNDING: "Confounding",
        SAMPLING: "Sampling error",
        MEASUREMENT: "Measurement error",
    }
    write_quiz(
        repository.workspace,
        Quiz(
            lecture_id=LECTURE_ID,
            questions=[
                QuizQuestion(
                    id=QUESTION_ID,
                    prompt="What does randomisation remove?",
                    options=[
                        QuizOption(
                            id=option_id,
                            text=text,
                            correct=option_id == correct,
                            explanation=None if option_id == correct else f"Not {text}.",
                        )
                        for option_id, text in texts.items()
                    ],
                )
            ],
        ),
    )
    record_app_authored_state(repository.workspace)
    create_revision(repository.workspace, RevisionCreateRequest(summary=f"Fix the key for {name}"))
    repository.publish(slug, name)


def test_insights_are_for_instructors_only(classroom: dict[str, Any], make_client: Any) -> None:
    learner = classroom["learner"]
    for path in ("/api/c/causal/insights", "/api/c/causal/answers.csv", "/api/c/causal/sync"):
        assert learner.get(path).status_code == 403
        assert make_client().get(path).status_code == 401
    assert learner.post("/api/c/causal/sync").status_code == 403
    assert classroom["instructor"].get("/api/c/causal/insights").status_code == 200


def test_insights_show_where_learners_get_stuck(classroom: dict[str, Any]) -> None:
    learner, instructor = classroom["learner"], classroom["instructor"]
    _answer(learner, CONFOUNDING, "s-1", "Ada")
    _answer(learner, SAMPLING, "s-2", "Grace")
    _answer(learner, CONFOUNDING, "s-2", "Grace")
    _answer(learner, SAMPLING, "s-3", "Alan")
    _answer(instructor, SAMPLING, "teacher", "Instructor")

    insights = instructor.get("/api/c/causal/insights").json()

    assert insights["overview"] == {
        "learners": 3,
        "questions": 1,
        "answers": 4,
        "mean_score": pytest.approx(0.5),
        "average_total_score": pytest.approx(0.5),
        "hidden_instructor_answers": 1,
    }
    question = insights["questions"][0]
    assert question["prompt"] == "What does randomisation remove?"
    assert question["lecture_title"] == "Foundations"
    assert question["attempts"] == 4 and question["learners"] == 3
    assert question["first_try_correct_rate"] == pytest.approx(1 / 3)
    assert question["most_chosen_wrong"] == {
        "option_id": SAMPLING,
        "text": "Sampling error",
        "learners": 2,
        "picks": 2,
    }
    assert question["changes"] == {
        "answer_key_changed": 0,
        "question_changed": 0,
        "option_removed": 0,
        "release_unavailable": 0,
    }
    students = {row["student_id"]: row for row in insights["students"]}
    assert set(students) == {"s-1", "s-2", "s-3"}
    assert students["s-2"]["name"] == "Grace"
    assert students["s-2"]["attempts"] == 2
    assert students["s-2"]["total_score"] == 0.5
    assert insights["releases"] == [
        {
            "tag": FALL,
            "name": "Fall 2026",
            "published_at": insights["releases"][0]["published_at"],
            "answers": 4,
            "learners": 3,
            "first_answer_at": insights["releases"][0]["first_answer_at"],
            "last_answer_at": insights["releases"][0]["last_answer_at"],
            "served": True,
            "graded": True,
            "readable": True,
        }
    ]

    with_instructors = instructor.get(
        "/api/c/causal/insights", params={"include_instructors": "true"}
    ).json()
    assert with_instructors["overview"]["learners"] == 4
    assert "teacher" in {row["student_id"] for row in with_instructors["students"]}


def test_a_fixed_answer_key_regrades_earlier_answers(classroom: dict[str, Any]) -> None:
    learner, instructor, admin = classroom["learner"], classroom["instructor"], classroom["admin"]
    _answer(learner, SAMPLING, "s-1")
    _answer(learner, CONFOUNDING, "s-1")

    _publish_with_key(classroom["repository"], SAMPLING, "winter-2026", "Winter 2026")
    synced = admin.post("/api/admin/courses/causal/sync").json()
    assert synced["release"]["tag"] == WINTER

    latest = instructor.get("/api/c/causal/insights").json()
    question = latest["questions"][0]
    assert latest["graded_release"]["tag"] == WINTER
    assert question["first_try_correct_rate"] == 1
    assert question["mean_score"] == 1
    assert question["changes"]["answer_key_changed"] == 2
    assert question["correct_option_text"] == "Sampling error"
    releases = {row["tag"]: row for row in latest["releases"]}
    assert releases[FALL]["answers"] == 2 and releases[FALL]["readable"]
    assert releases[WINTER]["served"] and releases[WINTER]["answers"] == 0

    earlier = instructor.get("/api/c/causal/insights", params={"release": FALL}).json()
    assert earlier["graded_release"]["tag"] == FALL
    assert earlier["questions"][0]["mean_score"] == 0.5
    assert earlier["questions"][0]["changes"]["answer_key_changed"] == 0

    csv = instructor.get("/api/c/causal/answers.csv", params={"release": FALL})
    assert csv.status_code == 200
    assert csv.headers["content-type"].startswith("text/csv")
    assert 'filename="causal-answers-fall-2026.csv"' in csv.headers["content-disposition"]
    rows = [line.split(",") for line in csv.text.splitlines()]
    assert rows[0][-3:] == ["graded_release_tag", "correct", "score"]
    assert [row[-2:] for row in rows[1:]] == [["false", "0"], ["true", "0.5"]]
    latest_csv = instructor.get("/api/c/causal/answers.csv").text.splitlines()
    assert [line.split(",")[-2:] for line in latest_csv[1:]] == [["true", "1"], ["false", "0"]]


def test_unknown_or_malformed_releases_are_refused(classroom: dict[str, Any]) -> None:
    instructor = classroom["instructor"]
    missing = instructor.get("/api/c/causal/insights", params={"release": "course-release/nope"})
    assert missing.status_code == 404
    malformed = instructor.get("/api/c/causal/insights", params={"release": "../../etc"})
    assert malformed.status_code == 422


def test_sync_now_runs_in_the_background_and_reports_its_outcome(
    classroom: dict[str, Any],
) -> None:
    instructor = classroom["instructor"]
    _publish_with_key(classroom["repository"], SAMPLING, "winter-2026", "Winter 2026")

    started = instructor.post("/api/c/causal/sync")
    assert started.status_code == 202
    deadline = time.monotonic() + 30
    status = started.json()
    while status["syncing"] or status["release"]["tag"] != WINTER:
        assert time.monotonic() < deadline, status
        time.sleep(0.1)
        status = instructor.get("/api/c/causal/sync").json()
    assert status["sync_status"] == "ok"
    assert instructor.get("/api/c/causal").json()["release"]["tag"] == WINTER


def test_background_sync_does_not_start_twice(classroom: dict[str, Any]) -> None:
    service = classroom["instructor"].app.state.sync
    course = service.store.course_by_slug("causal")
    release = service._lock(course.id)
    release.acquire()
    try:
        assert service.start_background_sync(course.id)
        assert service.is_syncing(course.id)
        assert not service.start_background_sync(course.id)
    finally:
        release.release()
    deadline = time.monotonic() + 30
    while service.is_syncing(course.id):
        assert time.monotonic() < deadline
        time.sleep(0.05)
