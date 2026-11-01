"""The Learner API: Lectures, Quizzes without answer keys, Answers and progress."""

import json
import sqlite3
from typing import Any, cast
from urllib.parse import quote, urlsplit

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from course_harness.core.presentation import SlideCitation
from course_harness.core.quiz import QuizOption, QuizQuestion
from course_harness_classroom.config import ClassroomSettings
from course_harness_classroom.learning import feedback, shuffled_options
from course_harness_classroom.security import RateLimiter

LECTURE_ID = "lecture-aaaaaaaaaaaa"
QUESTION_ID = "question-aaaaaaaaaaaa"
CORRECT = "option-aaaaaaaaaaaa"
WRONG = "option-bbbbbbbbbbbb"
OTHER_WRONG = "option-cccccccccccc"
EXPLANATIONS = ("on average", "does not remove sampling error", "says nothing about")
ANSWER_KEY_FIELDS = {"correct", "explanation"}


@pytest.fixture
def learner(make_client: Any, admin: TestClient, course_repository: Any) -> TestClient:
    course_repository.publish("fall-2026", "Fall 2026")
    attached = admin.post(
        "/api/admin/courses", json={"repo_url": str(course_repository.remote), "slug": "causal"}
    )
    assert attached.status_code == 201
    url = admin.post("/api/admin/courses/causal/links/learner").json()["url"]
    client = make_client()
    assert client.get(urlsplit(url).path).status_code == 303
    return client


def _answer(
    client: TestClient,
    option_id: str,
    *,
    student_id: str = "s-1001",
    question_id: str = QUESTION_ID,
    lecture_id: str = LECTURE_ID,
) -> Any:
    return client.post(
        "/api/c/causal/answers",
        json={
            "lecture_id": lecture_id,
            "question_id": question_id,
            "option_id": option_id,
            "learner_name": "Ada Lovelace",
            "student_id": student_id,
        },
    )


def _student(student_id: str) -> dict[str, str]:
    return {"X-Classroom-Student": quote(student_id, safe="")}


def _answers(settings: ClassroomSettings) -> list[dict[str, Any]]:
    connection = sqlite3.connect(settings.database_path)
    connection.row_factory = sqlite3.Row
    try:
        return [dict(row) for row in connection.execute("SELECT * FROM answers ORDER BY id")]
    finally:
        connection.close()


def _keys(payload: Any) -> set[str]:
    if isinstance(payload, dict):
        return {str(key) for key in payload} | {
            key for value in payload.values() for key in _keys(value)
        }
    if isinstance(payload, list):
        return {key for value in payload for key in _keys(value)}
    return set()


def _assert_no_answer_key(payload: Any) -> None:
    """No field says whether an Option is correct and no Explanation text appears."""
    assert not _keys(payload) & ANSWER_KEY_FIELDS
    text = json.dumps(payload).lower()
    for explanation in EXPLANATIONS:
        assert explanation not in text, explanation


def test_learner_payloads_before_an_answer_never_carry_the_answer_key(
    learner: TestClient,
) -> None:
    course = learner.get("/api/c/causal")
    lecture = learner.get(f"/api/c/causal/lectures/{LECTURE_ID}")
    quizzes = learner.get("/api/c/causal/quizzes", headers=_student("s-1001"))
    progress = learner.get("/api/c/causal/progress", headers=_student("s-1001"))

    for response in (course, lecture, quizzes, progress):
        assert response.status_code == 200, response.text
        _assert_no_answer_key(response.json())
    question = quizzes.json()[0]["questions"][0]
    assert quizzes.json()[0]["lecture_id"] == LECTURE_ID
    assert question["prompt"] == "What does randomisation remove?"
    assert all(set(option) == {"id", "text"} for option in question["options"])
    assert {option["id"] for option in question["options"]} == {CORRECT, WRONG, OTHER_WRONG}
    assert course.json()["lectures"][0]["question_count"] == 1
    assert progress.json() == {"student_id": "s-1001", "questions": []}


def test_options_are_shuffled_the_same_way_for_one_student_and_question(
    learner: TestClient,
) -> None:
    def order(student_id: str) -> list[str]:
        quizzes = learner.get("/api/c/causal/quizzes", headers=_student(student_id))
        return [option["id"] for option in quizzes.json()[0]["questions"][0]["options"]]

    assert order("s-1001") == order("s-1001")
    orders = {tuple(order(f"s-{number}")) for number in range(30)}
    assert len(orders) > 1
    question = QuizQuestion(
        id=QUESTION_ID,
        prompt="Which?",
        options=[QuizOption(id=f"option-{digit * 12}", text=digit) for digit in "abcdef"],
    )
    first = [option.id for option in shuffled_options(question, "s-1")]
    assert first == [option.id for option in shuffled_options(question, "s-1")]
    assert sorted(first) == sorted(option.id for option in question.options)


def test_a_lecture_carries_its_slides_and_notes_but_not_authoring_purpose(
    learner: TestClient,
) -> None:
    lecture = learner.get(f"/api/c/causal/lectures/{LECTURE_ID}").json()

    assert lecture["title"] == "Foundations"
    assert lecture["number"] == 1 and lecture["lecture_count"] == 1
    assert lecture["previous"] is None and lecture["next"] is None
    assert lecture["question_count"] == 1
    assert lecture["download_url"].endswith(".pptx")
    assert lecture["aspect_ratio"] == pytest.approx(16 / 9, rel=0.01)
    slide = lecture["slides"][0]
    assert slide["slide"]["title"] == "Foundations"
    assert "purpose" not in slide["slide"]
    assert slide["slots"]["title"]["width"] > 0
    assert slide["background_url"] is None
    assert learner.get("/api/c/causal/lectures/lecture-ffffffffffff").status_code == 404


def test_a_wrong_answer_is_recorded_without_correctness_and_explained(
    learner: TestClient, settings: ClassroomSettings
) -> None:
    response = _answer(learner, WRONG)

    assert response.status_code == 200, response.text
    assert response.json() == {
        "option_id": WRONG,
        "correct": False,
        "explanation": "Randomisation does not remove sampling error.",
        "citations": [],
        "recorded": True,
    }
    (row,) = _answers(settings)
    assert row["release_tag"] == "course-release/fall-2026"
    assert row["lecture_id"] == LECTURE_ID
    assert row["question_id"] == QUESTION_ID
    assert row["option_id"] == WRONG
    assert row["attempt"] == 1
    assert row["learner_name"] == "Ada Lovelace"
    assert row["student_id"] == "s-1001"
    assert row["role"] == "learner"
    assert row["answered_at"]
    assert not any("correct" in column for column in row)


def test_attempts_count_per_student_and_question_and_stop_after_a_correct_answer(
    learner: TestClient, settings: ClassroomSettings
) -> None:
    assert _answer(learner, WRONG).json()["recorded"] is True
    assert _answer(learner, WRONG, student_id="s-2002").json()["recorded"] is True
    assert _answer(learner, OTHER_WRONG).json()["recorded"] is True
    correct = _answer(learner, CORRECT).json()
    assert correct["correct"] is True
    assert correct["explanation"] == "Random assignment balances confounders **on average**."
    assert correct["recorded"] is True

    again = _answer(learner, WRONG)
    assert again.status_code == 200
    assert again.json()["correct"] is False
    assert again.json()["recorded"] is False
    assert _answer(learner, CORRECT).json()["recorded"] is False

    rows = [(row["student_id"], row["option_id"], row["attempt"]) for row in _answers(settings)]
    assert rows == [
        ("s-1001", WRONG, 1),
        ("s-2002", WRONG, 1),
        ("s-1001", OTHER_WRONG, 2),
        ("s-1001", CORRECT, 3),
    ]


def test_picking_an_option_again_is_answered_but_not_recorded(
    learner: TestClient, settings: ClassroomSettings
) -> None:
    assert _answer(learner, WRONG).json()["recorded"] is True
    again = _answer(learner, WRONG).json()
    assert again["recorded"] is False
    assert again["explanation"] == "Randomisation does not remove sampling error."
    assert _answer(learner, OTHER_WRONG).json()["recorded"] is True

    # An attempt number is the count of distinct Options tried.
    attempts = [(row["option_id"], row["attempt"]) for row in _answers(settings)]
    assert attempts == [(WRONG, 1), (OTHER_WRONG, 2)]


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("lecture_id", "lecture-ffffffffffff"),
        ("question_id", "question-ffffffffffff"),
        ("option_id", "option-ffffffffffff"),
    ],
)
def test_answers_naming_unknown_ids_are_refused(
    learner: TestClient, settings: ClassroomSettings, field: str, value: str
) -> None:
    ids = {"lecture_id": LECTURE_ID, "question_id": QUESTION_ID, "option_id": WRONG}
    ids[field] = value

    response = _answer(
        learner, ids["option_id"], **{k: v for k, v in ids.items() if k != "option_id"}
    )

    assert response.status_code == 422
    assert _answers(settings) == []


def test_answers_need_an_identity_a_small_body_and_course_access(
    learner: TestClient, make_client: Any, settings: ClassroomSettings
) -> None:
    base = {
        "lecture_id": LECTURE_ID,
        "question_id": QUESTION_ID,
        "option_id": WRONG,
        "learner_name": "Ada",
        "student_id": "s-1",
    }
    blank = learner.post("/api/c/causal/answers", json={**base, "student_id": "  "})
    control = learner.post("/api/c/causal/answers", json={**base, "learner_name": "A\nB"})
    unprintable = [
        learner.post("/api/c/causal/answers", json={**base, field: f"A{character}da"})
        for field in ("learner_name", "student_id")
        for character in ("\x85", "\u202e", "\u2067", "\u2028")
    ]
    extra = learner.post("/api/c/causal/answers", json={**base, "correct": True})
    large = learner.post("/api/c/causal/answers", json={**base, "learner_name": "x" * 5_000})
    stranger = make_client().post("/api/c/causal/answers", json=base)

    assert [blank.status_code, control.status_code, extra.status_code] == [422, 422, 422]
    assert {response.status_code for response in unprintable} == {422}
    assert large.status_code == 413
    assert stranger.status_code == 401
    assert _answers(settings) == []


def test_answers_are_rate_limited_per_student_and_per_address(learner: TestClient) -> None:
    limits = cast(FastAPI, learner.app).state.answer_limits
    limits["student"] = RateLimiter(limit=2)
    limits["address"] = RateLimiter(limit=4)

    assert _answer(learner, WRONG).status_code == 200
    assert _answer(learner, OTHER_WRONG).status_code == 200
    assert _answer(learner, CORRECT).status_code == 429
    assert _answer(learner, WRONG, student_id="s-2").status_code == 200
    assert _answer(learner, WRONG, student_id="s-3").status_code == 429


def test_answers_without_course_access_are_refused_before_limits_and_body(
    learner: TestClient, make_client: Any
) -> None:
    limits = cast(FastAPI, learner.app).state.answer_limits
    limits["address"] = RateLimiter(limit=1)
    stranger = make_client()

    oversized = stranger.post("/api/c/causal/answers", content=b"x" * 10_000)
    malformed = stranger.post("/api/c/causal/answers", content=b"not json")

    assert [oversized.status_code, malformed.status_code] == [401, 401]
    # The stranger's requests used none of the address's budget.
    assert _answer(learner, WRONG).status_code == 200


def test_progress_is_restored_for_the_same_student_id(
    learner: TestClient, make_client: Any, admin: TestClient
) -> None:
    _answer(learner, WRONG)
    _answer(learner, WRONG)
    _answer(learner, OTHER_WRONG, student_id="s-2002")

    # Another device: a fresh cookie, the same student ID.
    url = admin.post("/api/admin/courses/causal/links/learner").json()["url"]
    device = make_client()
    device.get(urlsplit(url).path)
    progress = device.get("/api/c/causal/progress", headers=_student("s-1001")).json()

    (question,) = progress["questions"]
    assert question["question_id"] == QUESTION_ID
    assert question["lecture_id"] == LECTURE_ID
    assert question["correct"] is False
    assert [item["option_id"] for item in question["tried"]] == [WRONG]
    # Only the picked Option is explained; nothing points at the correct one.
    assert CORRECT not in json.dumps(progress)
    assert "on average" not in json.dumps(progress)

    _answer(device, CORRECT)
    restored = device.get("/api/c/causal/progress", headers=_student("s-1001")).json()
    (question,) = restored["questions"]
    assert question["correct"] is True
    assert [item["option_id"] for item in question["tried"]] == [WRONG, CORRECT]
    assert question["tried"][1]["explanation"].startswith("Random assignment")
    assert device.get("/api/c/causal/progress").status_code == 422
    for bad in ("s%E2%80%AE1", "s%0A1", "%FF", "x" * 65):
        response = device.get("/api/c/causal/progress", headers={"X-Classroom-Student": bad})
        assert response.status_code == 422, bad


def test_instructors_answer_like_learners_and_are_marked_as_instructors(
    make_client: Any, admin: TestClient, learner: TestClient, settings: ClassroomSettings
) -> None:
    url = admin.post("/api/admin/courses/causal/links/instructor").json()["url"]
    instructor = make_client()
    instructor.get(urlsplit(url).path)

    assert _answer(instructor, WRONG, student_id="teacher").status_code == 200
    assert [row["role"] for row in _answers(settings)] == ["instructor"]


def test_a_course_without_a_release_has_no_quizzes(
    make_client: Any, admin: TestClient, course_repository: Any
) -> None:
    admin.post(
        "/api/admin/courses", json={"repo_url": str(course_repository.remote), "slug": "causal"}
    )
    url = admin.post("/api/admin/courses/causal/links/learner").json()["url"]
    client = make_client()
    client.get(urlsplit(url).path)

    assert client.get("/api/c/causal/quizzes").status_code == 409
    assert _answer(client, WRONG).status_code == 409


def test_feedback_carries_the_picked_options_citations() -> None:
    citation = SlideCitation(
        source_id="source-aaaaaaaaaaaa", label="Rubin 1974", url="https://x.org"
    )
    question = QuizQuestion(
        id=QUESTION_ID,
        prompt="Which?",
        options=[
            QuizOption(id=CORRECT, text="A", correct=True),
            QuizOption(id=WRONG, text="B", explanation="Not B.", citations=[citation]),
        ],
    )

    wrong = feedback(question, question.options[1])

    assert wrong.correct is False
    assert wrong.citations == [citation]
    assert feedback(question, question.options[0]).correct is True
