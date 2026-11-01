"""The derived score rule and its regrading against another Release's answer key."""

from dataclasses import replace

import pytest

from course_harness.core.quiz import QuizOption, QuizQuestion
from course_harness_classroom.grading import AnswerKey, attempt_score, grade_question
from course_harness_classroom.insights import (
    ReleaseQuestion,
    ReleaseQuizzes,
    answers_csv,
    build_insights,
)
from course_harness_classroom.store import Answer, Role

LECTURE = "lecture-aaaaaaaaaaaa"
QUESTION = "question-aaaaaaaaaaaa"


def option(letter: str) -> str:
    return f"option-{letter * 12}"


def key(count: int, correct: str = "a") -> AnswerKey:
    letters = "abcdefghij"[:count]
    return AnswerKey(
        question_id=QUESTION,
        option_ids=tuple(option(letter) for letter in letters),
        correct_option_id=option(correct),
    )


@pytest.mark.parametrize(
    ("options", "scores"),
    [
        (2, [1, 0]),
        (3, [1, 0.5, 0]),
        (4, [1, 0.5, 0.25, 0]),
        (5, [1, 0.5, 0.25, 0.125, 0]),
    ],
)
def test_attempt_k_earns_half_the_previous_until_one_option_is_left(
    options: int, scores: list[float]
) -> None:
    assert [attempt_score(wrong, options) for wrong in range(options)] == scores


def test_a_first_correct_pick_scores_one_and_counts_as_first_try() -> None:
    grade = grade_question([option("a")], key(4))

    assert grade.correct and grade.first_try_correct
    assert grade.score == 1
    assert grade.pick_correct == (True,)
    assert grade.solved_at == 0


def test_each_distinct_wrong_option_halves_the_score() -> None:
    grade = grade_question([option("b"), option("c"), option("a")], key(4))

    assert grade.correct and not grade.first_try_correct
    assert grade.wrong_options == 2
    assert grade.score == 0.25
    assert grade.pick_correct == (False, False, True)


def test_the_last_remaining_option_earns_nothing() -> None:
    grade = grade_question([option("b"), option("c"), option("d"), option("a")], key(4))
    assert grade.correct and grade.score == 0
    assert grade_question([option("b"), option("a")], key(2)).score == 0


def test_repeating_a_wrong_option_does_not_cost_another_attempt() -> None:
    grade = grade_question([option("b"), option("b"), option("a")], key(3))

    assert grade.wrong_options == 1
    assert grade.score == 0.5
    assert grade.pick_correct == (False, False, True)


def test_a_question_never_answered_correctly_scores_zero() -> None:
    grade = grade_question([option("b"), option("c")], key(4))

    assert not grade.correct and grade.score == 0 and grade.solved_at is None


def test_options_the_key_does_not_know_are_neither_wrong_nor_counted() -> None:
    grade = grade_question([option("z"), option("b"), option("a")], key(3))

    assert grade.pick_correct == (None, False, True)
    assert grade.wrong_options == 1
    assert grade.score == 0.5


def test_picks_after_the_first_correct_one_do_not_change_the_score() -> None:
    grade = grade_question([option("a"), option("b"), option("c")], key(3))

    assert grade.score == 1 and grade.solved_at == 0
    assert grade.pick_correct == (True, False, False)


def test_a_broken_answer_key_cannot_be_graded() -> None:
    broken = AnswerKey(
        question_id=QUESTION, option_ids=(option("a"), option("b")), correct_option_id=None
    )
    grade = grade_question([option("a")], broken)

    assert not grade.gradable and not grade.correct
    assert grade.pick_correct == (None,)


def test_a_fixed_answer_key_regrades_earlier_picks() -> None:
    picks = [option("b"), option("a")]
    old = grade_question(picks, key(3, correct="a"))
    fixed = grade_question(picks, key(3, correct="b"))

    assert old.correct and old.score == 0.5
    assert fixed.correct and fixed.first_try_correct and fixed.score == 1
    assert fixed.pick_correct == (True, False)


# Insights over a Release's Quizzes


def question(correct: str = "a", *, prompt: str = "Which?", count: int = 4) -> QuizQuestion:
    return QuizQuestion(
        id=QUESTION,
        prompt=prompt,
        options=[
            QuizOption(
                id=option(letter), text=f"Option {letter.upper()}", correct=letter == correct
            )
            for letter in "abcdefghij"[:count]
        ],
    )


def release(tag: str, item: QuizQuestion, published_at: str) -> ReleaseQuizzes:
    return ReleaseQuizzes(
        tag=tag,
        name=tag.removeprefix("course-release/").title(),
        published_at=published_at,
        questions={(LECTURE, QUESTION): ReleaseQuestion(LECTURE, "Foundations", item)},
    )


OLD = release("course-release/old", question("a"), "2026-09-01T00:00:00+00:00")
FIXED = release("course-release/fixed", question("b"), "2026-10-01T00:00:00+00:00")


def answers(*rows: tuple[str, str, str, Role]) -> list[Answer]:
    """Answers from ``(student ID, option letter, Release tag, role)`` rows, in order."""
    result: list[Answer] = []
    attempts: dict[str, int] = {}
    for number, (student, letter, tag, role) in enumerate(rows, start=1):
        attempts[student] = attempts.get(student, 0) + 1
        result.append(
            Answer(
                id=number,
                course_id=1,
                release_tag=tag,
                lecture_id=LECTURE,
                question_id=QUESTION,
                option_id=option(letter),
                attempt=attempts[student],
                learner_name=f"Learner {student}",
                student_id=student,
                role=role,
                answered_at=f"2026-10-02T10:00:{number:02d}.000+00:00",
            )
        )
    return result


SAMPLE = answers(
    ("s1", "a", OLD.tag, "learner"),
    ("s2", "c", OLD.tag, "learner"),
    ("s2", "a", OLD.tag, "learner"),
    ("s3", "b", FIXED.tag, "learner"),
    ("t1", "c", FIXED.tag, "instructor"),
)


def test_insights_grade_old_answers_against_the_fixed_key_and_flag_them() -> None:
    insights = build_insights(
        SAMPLE,
        FIXED,
        {OLD.tag: OLD, FIXED.tag: FIXED},
        served_tag=FIXED.tag,
        include_instructors=False,
    )

    row = insights.questions[0]
    assert row.learners == 3 and row.attempts == 4
    # Under the fixed key only s3 answered correctly, on the first try.
    assert row.correct_learners == 1
    assert row.first_try_correct_rate == pytest.approx(1 / 3)
    assert row.mean_score == pytest.approx(1 / 3)
    assert row.correct_option_text == "Option B"
    assert row.most_chosen_wrong is not None
    assert row.most_chosen_wrong.text == "Option A"
    assert row.most_chosen_wrong.learners == 2
    assert row.changes.answer_key_changed == 3
    assert {item.tag: item.answers for item in row.releases} == {OLD.tag: 3, FIXED.tag: 1}
    assert insights.overview.hidden_instructor_answers == 1
    assert [student.student_id for student in insights.students] == ["s1", "s2", "s3"]
    assert {student.student_id: student.attempts for student in insights.students} == {
        "s1": 1,
        "s2": 2,
        "s3": 1,
    }
    assert [item.tag for item in insights.releases] == [FIXED.tag, OLD.tag]
    assert insights.graded_release.tag == FIXED.tag


def test_insights_can_grade_against_an_earlier_release() -> None:
    insights = build_insights(
        SAMPLE,
        OLD,
        {OLD.tag: OLD, FIXED.tag: FIXED},
        served_tag=FIXED.tag,
        include_instructors=False,
    )

    row = insights.questions[0]
    assert row.correct_learners == 2
    scores = {student.student_id: student.total_score for student in insights.students}
    assert scores == {"s1": 1, "s2": 0.5, "s3": 0}
    assert insights.graded_release.tag == OLD.tag and not insights.graded_release.served


def test_instructor_answers_are_included_only_when_asked() -> None:
    insights = build_insights(
        SAMPLE, FIXED, {OLD.tag: OLD}, served_tag=FIXED.tag, include_instructors=True
    )

    assert insights.overview.learners == 4
    assert insights.overview.hidden_instructor_answers == 0
    assert any(student.role == "instructor" for student in insights.students)


def test_changed_wording_and_removed_or_unreadable_questions_are_flagged() -> None:
    reworded = release(
        "course-release/reworded", question("b", prompt="Which one?"), "2026-11-01T00:00:00+00:00"
    )
    rows = answers(
        ("s1", "b", FIXED.tag, "learner"),
        ("s2", "b", "course-release/lost", "learner"),
    )
    insights = build_insights(
        rows,
        reworded,
        {FIXED.tag: FIXED, "course-release/lost": None},
        served_tag=reworded.tag,
        include_instructors=False,
    )

    changes = insights.questions[0].changes
    assert changes.question_changed == 1
    assert changes.release_unavailable == 1
    assert not next(
        item for item in insights.releases if item.tag == "course-release/lost"
    ).readable

    empty = ReleaseQuizzes(tag="course-release/empty", name="Empty", published_at="", questions={})
    removed = build_insights(
        rows, empty, {FIXED.tag: FIXED}, served_tag=empty.tag, include_instructors=False
    )
    assert removed.questions == []
    assert removed.removed_questions[0].prompt == "Which?"
    assert removed.removed_questions[0].answers == 2


def test_the_csv_carries_derived_correctness_and_scores_that_sum_to_totals() -> None:
    lines = answers_csv(SAMPLE, OLD, include_instructors=False).splitlines()

    assert lines[0].startswith("answered_at,student_id,learner_name,role,release_tag")
    rows = [line.split(",") for line in lines[1:]]
    assert len(rows) == 4
    assert [(row[1], row[-2], row[-1]) for row in rows] == [
        ("s1", "true", "1"),
        ("s2", "false", "0"),
        ("s2", "true", "0.5"),
        ("s3", "false", "0"),
    ]


def test_the_csv_never_starts_a_cell_with_a_formula() -> None:
    rows = answers(("=HYPERLINK(1)", "a", OLD.tag, "learner"))
    rows[0] = replace(rows[0], learner_name="+cmd|' /C calc'!A0")
    content = answers_csv(rows, OLD, include_instructors=False)

    assert "'=HYPERLINK(1)" in content
    assert "'+cmd" in content
    assert ",=" not in content and ",+" not in content


def test_the_most_chosen_wrong_option_counts_only_picks_before_the_first_correct_one() -> None:
    # Under the old key "a" was correct, so these students moved on to other Options after "b".
    rows = answers(
        ("s1", "b", OLD.tag, "learner"),
        ("s1", "c", OLD.tag, "learner"),
        ("s2", "b", OLD.tag, "learner"),
        ("s2", "c", OLD.tag, "learner"),
        ("s3", "d", OLD.tag, "learner"),
    )
    insights = build_insights(
        rows, FIXED, {OLD.tag: OLD}, served_tag=FIXED.tag, include_instructors=False
    )

    wrong = insights.questions[0].most_chosen_wrong
    assert wrong is not None
    assert (wrong.text, wrong.learners, wrong.picks) == ("Option D", 1, 1)


def test_the_average_score_counts_unanswered_questions_as_zero() -> None:
    other = question("b").model_copy(update={"id": "question-bbbbbbbbbbbb"})
    two = ReleaseQuizzes(
        tag=FIXED.tag,
        name="Fixed",
        published_at="",
        questions={
            **FIXED.questions,
            (LECTURE, other.id): ReleaseQuestion(LECTURE, "Foundations", other),
        },
    )
    rows = answers(("s1", "b", FIXED.tag, "learner"))
    overview = build_insights(rows, two, {}, served_tag=two.tag, include_instructors=False).overview

    assert overview.mean_answered_score == 1
    assert overview.average_total_score == 1
    assert overview.average_score == 0.5
