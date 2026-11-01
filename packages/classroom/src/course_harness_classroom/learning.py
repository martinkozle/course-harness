"""What Learners see of a served Release: Lectures, Quizzes without their answer keys, the
feedback on one picked Option, and a student's progress derived from their Answers.

Nothing here hands out whether an Option is correct or its Explanation before the student has
picked that Option; :func:`learner_quizzes` is the only Quiz view sent ahead of an Answer.
"""

from __future__ import annotations

import hashlib
from collections.abc import Iterable

from pydantic import BaseModel

from course_harness.core.presentation import SlideCitation
from course_harness.core.quiz import QuizOption, QuizQuestion, correct_option, option_by_id
from course_harness_classroom.served import ServedLecture, ServedSnapshot
from course_harness_classroom.store import Answer


class LearnerOption(BaseModel):
    id: str
    text: str


class LearnerQuestion(BaseModel):
    id: str
    prompt: str
    options: list[LearnerOption]


class LearnerQuiz(BaseModel):
    lecture_id: str
    lecture_title: str
    questions: list[LearnerQuestion]


class OptionFeedback(BaseModel):
    """What a student learns about an Option they picked."""

    option_id: str
    correct: bool
    explanation: str | None
    citations: list[SlideCitation]


class QuestionProgress(BaseModel):
    lecture_id: str
    question_id: str
    correct: bool
    # The Options the student picked, first pick first, each once.
    tried: list[OptionFeedback]


def shuffled_options(question: QuizQuestion, seed: str) -> list[QuizOption]:
    """The Question's Options in an order fixed for this seed and Question but unrelated to
    the authored order, which often puts the correct Option first."""

    def key(option: QuizOption) -> str:
        material = f"{seed}\0{question.id}\0{option.id}".encode()
        return hashlib.sha256(material).hexdigest()

    return sorted(question.options, key=key)


def learner_quizzes(snapshot: ServedSnapshot, *, student_id: str) -> list[LearnerQuiz]:
    """Every Quiz of the Release in Lecture order, without correctness or Explanations."""
    return [
        LearnerQuiz(
            lecture_id=lecture.id,
            lecture_title=lecture.title,
            questions=[
                LearnerQuestion(
                    id=question.id,
                    prompt=question.prompt,
                    options=[
                        LearnerOption(id=option.id, text=option.text)
                        for option in shuffled_options(question, student_id)
                    ],
                )
                for question in lecture.quiz.questions
            ],
        )
        for lecture in snapshot.lectures
        if lecture.quiz is not None
    ]


def find_question(
    snapshot: ServedSnapshot, lecture_id: str, question_id: str
) -> tuple[ServedLecture, QuizQuestion] | None:
    lecture = next((item for item in snapshot.lectures if item.id == lecture_id), None)
    if lecture is None or lecture.quiz is None:
        return None
    question = next((item for item in lecture.quiz.questions if item.id == question_id), None)
    return (lecture, question) if question is not None else None


def is_correct(question: QuizQuestion, option_id: str) -> bool:
    key = correct_option(question)
    return key is not None and key.id == option_id


def feedback(question: QuizQuestion, option: QuizOption) -> OptionFeedback:
    return OptionFeedback(
        option_id=option.id,
        correct=is_correct(question, option.id),
        explanation=option.explanation,
        citations=list(option.citations),
    )


def progress(snapshot: ServedSnapshot, answers: Iterable[Answer]) -> list[QuestionProgress]:
    """One student's state on each Question of the served Release they answered.

    Correctness is derived against the served Release, so a fixed answer key regrades earlier
    Answers. Answers to Questions or Options the Release no longer has are left out.
    """
    picks: dict[tuple[str, str], list[str]] = {}
    for answer in answers:
        picked = picks.setdefault((answer.lecture_id, answer.question_id), [])
        if answer.option_id not in picked:
            picked.append(answer.option_id)
    result: list[QuestionProgress] = []
    for lecture in snapshot.lectures:
        if lecture.quiz is None:
            continue
        for question in lecture.quiz.questions:
            tried = [
                feedback(question, option)
                for option_id in picks.get((lecture.id, question.id), [])
                if (option := option_by_id(question, option_id)) is not None
            ]
            if tried:
                result.append(
                    QuestionProgress(
                        lecture_id=lecture.id,
                        question_id=question.id,
                        correct=any(item.correct for item in tried),
                        tried=tried,
                    )
                )
    return result
