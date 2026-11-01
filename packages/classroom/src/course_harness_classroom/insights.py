"""Instructor Insights: where Learners get stuck, per Question, per Release and per student.

Every number is derived from the stored Answers graded against one Release's Quizzes (the served
Release by default), using :mod:`course_harness_classroom.grading`. Answers given under another
Release are compared with that Release's Quizzes, so an Instructor can see which Answers were
given to a Question that has changed since, or that the grading Release no longer has.
"""

from __future__ import annotations

import csv
import io
from collections import Counter
from collections.abc import Iterable, Sequence
from dataclasses import dataclass

from pydantic import BaseModel

from course_harness.core.quiz import QuizQuestion, correct_option
from course_harness_classroom.grading import AnswerKey, QuestionGrade, grade_question
from course_harness_classroom.store import Answer, Role

QuestionKey = tuple[str, str]


@dataclass(frozen=True)
class ReleaseQuestion:
    lecture_id: str
    lecture_title: str
    question: QuizQuestion


@dataclass(frozen=True)
class ReleaseQuizzes:
    """The Questions of one Release, in course order, with what identifies the Release."""

    tag: str
    name: str
    published_at: str
    # Keyed by (Lecture ID, Question ID), like Answers and progress.
    questions: dict[QuestionKey, ReleaseQuestion]


# Responses


class WrongOption(BaseModel):
    option_id: str
    text: str
    # Learners who picked it at least once, and how often it was picked in all.
    learners: int
    picks: int


class ReleaseCount(BaseModel):
    tag: str
    answers: int


class AnswerChanges(BaseModel):
    """How many of a Question's Answers were given to a different version of it."""

    # The Release the Answer was given under had a different correct Option.
    answer_key_changed: int = 0
    # The prompt or the Options changed, but the correct Option did not.
    question_changed: int = 0
    # The picked Option is not part of the Question in the grading Release.
    option_removed: int = 0
    # The Release the Answer was given under could not be read, so nothing can be compared.
    release_unavailable: int = 0


class QuestionInsight(BaseModel):
    lecture_id: str
    lecture_title: str
    question_id: str
    # Position in the course, from 1, across all Quizzes.
    number: int
    prompt: str
    option_count: int
    correct_option_text: str | None
    attempts: int
    learners: int
    correct_learners: int
    first_try_correct_rate: float | None
    mean_score: float | None
    most_chosen_wrong: WrongOption | None
    releases: list[ReleaseCount]
    changes: AnswerChanges
    gradable: bool


class RemovedQuestion(BaseModel):
    """A Question that has Answers but that the grading Release no longer has."""

    question_id: str
    lecture_id: str
    prompt: str | None
    answers: int
    learners: int


class StudentInsight(BaseModel):
    student_id: str
    name: str
    role: Role
    # Every recorded pick, so an ID with picks but no progress, or a burst, stands out.
    attempts: int
    questions_answered: int
    correct: int
    total_score: float
    last_activity: str


class ReleaseInsight(BaseModel):
    tag: str
    name: str | None
    published_at: str | None
    answers: int
    learners: int
    first_answer_at: str | None
    last_answer_at: str | None
    served: bool
    graded: bool
    readable: bool


class Overview(BaseModel):
    learners: int
    questions: int
    answers: int
    # The mean over every answered (student, Question) pair, from 0 to 1.
    mean_score: float | None
    # The mean over students of the sum of their Question scores, out of ``questions``.
    average_total_score: float | None
    # Answers given with an Instructor link that these numbers leave out.
    hidden_instructor_answers: int


class Insights(BaseModel):
    graded_release: ReleaseInsight
    include_instructors: bool
    overview: Overview
    questions: list[QuestionInsight]
    removed_questions: list[RemovedQuestion]
    students: list[StudentInsight]
    releases: list[ReleaseInsight]


# Assembly


def visible_answers(answers: Iterable[Answer], *, include_instructors: bool) -> list[Answer]:
    return [answer for answer in answers if include_instructors or answer.role != "instructor"]


def question_key(answer: Answer) -> QuestionKey:
    return (answer.lecture_id, answer.question_id)


StudentQuestion = tuple[str, QuestionKey]


def _picks(answers: Sequence[Answer]) -> dict[StudentQuestion, list[Answer]]:
    grouped: dict[StudentQuestion, list[Answer]] = {}
    for answer in answers:
        grouped.setdefault((answer.student_id, question_key(answer)), []).append(answer)
    return grouped


def grades(
    answers: Sequence[Answer], grading: ReleaseQuizzes
) -> dict[StudentQuestion, QuestionGrade]:
    """Each student's grade on each Question of the grading Release they answered."""
    keys = {key: AnswerKey.of(item.question) for key, item in grading.questions.items()}
    return {
        pair: grade_question([answer.option_id for answer in picks], keys[pair[1]])
        for pair, picks in _picks(answers).items()
        if pair[1] in keys
    }


def _question_signature(question: QuizQuestion) -> tuple[object, ...]:
    return (question.prompt, tuple((option.id, option.text) for option in question.options))


def _changes(
    current: QuizQuestion,
    answers: Sequence[Answer],
    graded_tag: str,
    releases: dict[str, ReleaseQuizzes | None],
) -> AnswerChanges:
    changes = AnswerChanges()
    current_key = correct_option(current)
    current_options = {option.id for option in current.options}
    for answer in answers:
        if answer.option_id not in current_options:
            changes.option_removed += 1
        if answer.release_tag == graded_tag:
            continue
        release = releases.get(answer.release_tag)
        if release is None:
            changes.release_unavailable += 1
            continue
        then = release.questions.get(question_key(answer))
        if then is None:
            changes.question_changed += 1
            continue
        then_key = correct_option(then.question)
        if (then_key.id if then_key else None) != (current_key.id if current_key else None):
            changes.answer_key_changed += 1
        elif _question_signature(then.question) != _question_signature(current):
            changes.question_changed += 1
    return changes


def _release_insight(
    tag: str,
    answers: Sequence[Answer],
    releases: dict[str, ReleaseQuizzes | None],
    *,
    served_tag: str,
    graded_tag: str,
) -> ReleaseInsight:
    release = releases.get(tag)
    times = [answer.answered_at for answer in answers]
    return ReleaseInsight(
        tag=tag,
        name=release.name if release else None,
        published_at=release.published_at if release else None,
        answers=len(answers),
        learners=len({answer.student_id for answer in answers}),
        first_answer_at=min(times) if times else None,
        last_answer_at=max(times) if times else None,
        served=tag == served_tag,
        graded=tag == graded_tag,
        readable=release is not None,
    )


def build_insights(
    all_answers: Sequence[Answer],
    grading: ReleaseQuizzes,
    releases: dict[str, ReleaseQuizzes | None],
    *,
    served_tag: str,
    include_instructors: bool,
) -> Insights:
    """Insights from a course's Answers, graded against ``grading``.

    ``releases`` maps the Release tag of every Answer to its Quizzes, or to ``None`` when that
    Release can no longer be read.
    """
    releases = {**releases, grading.tag: grading}
    answers = visible_answers(all_answers, include_instructors=include_instructors)
    graded = grades(answers, grading)
    by_question: dict[QuestionKey, list[Answer]] = {}
    for answer in answers:
        by_question.setdefault(question_key(answer), []).append(answer)
    grades_by_question: dict[QuestionKey, list[QuestionGrade]] = {}
    grades_by_student: dict[str, list[QuestionGrade]] = {}
    for (student_id, key), grade in graded.items():
        grades_by_question.setdefault(key, []).append(grade)
        grades_by_student.setdefault(student_id, []).append(grade)

    questions: list[QuestionInsight] = []
    for number, (pair, item) in enumerate(grading.questions.items(), start=1):
        question = item.question
        question_answers = by_question.get(pair, [])
        question_grades = grades_by_question.get(pair, [])
        key = correct_option(question)
        texts = {option.id: option.text for option in question.options}
        wrong_learners: dict[str, set[str]] = {}
        wrong_picks: Counter[str] = Counter()
        for answer in question_answers:
            if answer.option_id in texts and (key is None or answer.option_id != key.id):
                wrong_learners.setdefault(answer.option_id, set()).add(answer.student_id)
                wrong_picks[answer.option_id] += 1
        most_chosen = (
            max(
                wrong_learners,
                key=lambda option_id: (len(wrong_learners[option_id]), wrong_picks[option_id]),
            )
            if wrong_learners and key is not None
            else None
        )
        learners = len(question_grades)
        gradable = key is not None
        questions.append(
            QuestionInsight(
                lecture_id=item.lecture_id,
                lecture_title=item.lecture_title,
                question_id=question.id,
                number=number,
                prompt=question.prompt,
                option_count=len(question.options),
                correct_option_text=key.text if key else None,
                attempts=len(question_answers),
                learners=learners,
                correct_learners=sum(grade.correct for grade in question_grades),
                first_try_correct_rate=(
                    sum(grade.first_try_correct for grade in question_grades) / learners
                    if learners and gradable
                    else None
                ),
                mean_score=(
                    sum(grade.score for grade in question_grades) / learners
                    if learners and gradable
                    else None
                ),
                most_chosen_wrong=(
                    WrongOption(
                        option_id=most_chosen,
                        text=texts[most_chosen],
                        learners=len(wrong_learners[most_chosen]),
                        picks=wrong_picks[most_chosen],
                    )
                    if most_chosen
                    else None
                ),
                releases=[
                    ReleaseCount(tag=tag, answers=count)
                    for tag, count in Counter(
                        answer.release_tag for answer in question_answers
                    ).items()
                ],
                changes=_changes(question, question_answers, grading.tag, releases),
                gradable=gradable,
            )
        )

    removed: list[RemovedQuestion] = []
    for pair, question_answers in by_question.items():
        if pair in grading.questions:
            continue
        prompt = None
        for answer in reversed(question_answers):
            release = releases.get(answer.release_tag)
            then = release.questions.get(pair) if release else None
            if then is not None:
                prompt = then.question.prompt
                break
        removed.append(
            RemovedQuestion(
                question_id=pair[1],
                lecture_id=pair[0],
                prompt=prompt,
                answers=len(question_answers),
                learners=len({answer.student_id for answer in question_answers}),
            )
        )

    students: dict[str, list[Answer]] = {}
    for answer in answers:
        students.setdefault(answer.student_id, []).append(answer)
    student_rows: list[StudentInsight] = []
    for student_id, student_answers in students.items():
        own = grades_by_student.get(student_id, [])
        latest = student_answers[-1]
        student_rows.append(
            StudentInsight(
                student_id=student_id,
                name=latest.learner_name,
                role=latest.role,
                attempts=len(student_answers),
                questions_answered=len(own),
                correct=sum(grade.correct for grade in own),
                total_score=sum(grade.score for grade in own),
                last_activity=max(answer.answered_at for answer in student_answers),
            )
        )
    student_rows.sort(key=lambda row: (row.name.casefold(), row.student_id))

    by_release: dict[str, list[Answer]] = {}
    for answer in answers:
        by_release.setdefault(answer.release_tag, []).append(answer)
    tags = list(dict.fromkeys([served_tag, grading.tag, *by_release]))
    release_rows = [
        _release_insight(
            tag,
            by_release.get(tag, []),
            releases,
            served_tag=served_tag,
            graded_tag=grading.tag,
        )
        for tag in tags
    ]
    release_rows.sort(key=lambda row: row.published_at or "", reverse=True)

    gradable_grades = [grade for grade in graded.values() if grade.gradable]
    return Insights(
        graded_release=next(row for row in release_rows if row.graded),
        include_instructors=include_instructors,
        overview=Overview(
            learners=len(students),
            questions=len(grading.questions),
            answers=len(answers),
            mean_score=(
                sum(grade.score for grade in gradable_grades) / len(gradable_grades)
                if gradable_grades
                else None
            ),
            average_total_score=(
                sum(row.total_score for row in student_rows) / len(student_rows)
                if student_rows
                else None
            ),
            hidden_instructor_answers=(
                0
                if include_instructors
                else sum(answer.role == "instructor" for answer in all_answers)
            ),
        ),
        questions=questions,
        removed_questions=removed,
        students=student_rows,
        releases=release_rows,
    )


CSV_COLUMNS = (
    "answered_at",
    "student_id",
    "learner_name",
    "role",
    "release_tag",
    "lecture_id",
    "question_id",
    "option_id",
    "option_text",
    "attempt",
    "graded_release_tag",
    "correct",
    "score",
)
FORMULA_PREFIXES = ("=", "+", "-", "@", "\t", "\r")


def _cell(value: object) -> str:
    """A CSV cell that a spreadsheet never runs as a formula."""
    text = "" if value is None else str(value)
    return f"'{text}" if text.startswith(FORMULA_PREFIXES) else text


def answers_csv(
    all_answers: Sequence[Answer], grading: ReleaseQuizzes, *, include_instructors: bool
) -> str:
    """One row per Answer, with its correctness and score derived against ``grading``.

    ``correct`` is blank when the Answer cannot be judged. ``score`` holds the Question's score
    on the pick that first answered it correctly and 0 on the student's other picks of a graded
    Question, so a column sum is the student's total; it is blank when the Answer cannot be
    graded.
    """
    answers = visible_answers(all_answers, include_instructors=include_instructors)
    graded = grades(answers, grading)
    positions: dict[int, tuple[QuestionGrade, int]] = {}
    for pair, picks in _picks(answers).items():
        grade = graded.get(pair)
        if grade is None:
            continue
        for index, answer in enumerate(picks):
            positions[answer.id] = (grade, index)
    buffer = io.StringIO()
    writer = csv.writer(buffer, lineterminator="\r\n")
    writer.writerow(CSV_COLUMNS)
    for answer in answers:
        item = grading.questions.get(question_key(answer))
        text = (
            next(
                (option.text for option in item.question.options if option.id == answer.option_id),
                None,
            )
            if item
            else None
        )
        correct: bool | None = None
        score: float | None = None
        if answer.id in positions:
            grade, index = positions[answer.id]
            correct = grade.pick_correct[index]
            if grade.gradable and correct is not None:
                score = grade.score if index == grade.solved_at else 0.0
        writer.writerow(
            [
                _cell(value)
                for value in (
                    answer.answered_at,
                    answer.student_id,
                    answer.learner_name,
                    answer.role,
                    answer.release_tag,
                    answer.lecture_id,
                    answer.question_id,
                    answer.option_id,
                    text,
                    answer.attempt,
                    grading.tag,
                    "" if correct is None else ("true" if correct else "false"),
                    "" if score is None else f"{score:g}",
                )
            ]
        )
    return buffer.getvalue()
