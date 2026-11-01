"""The Quiz that checks understanding of one Lecture, as canonical Course state.

Each Lecture has at most one Quiz, stored as ``quizzes/<lecture-id>.yaml``.  The schema only
checks shape; the answer-key rules (unique Question and Option IDs, exactly one correct Option)
are reported by :func:`quiz_problems` so that Current State and Release validation can name the
exact Question or Option at fault.
"""

import os
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Literal
from uuid import uuid4

import yaml
from pydantic import BaseModel, ConfigDict, Field, ValidationError

from course_harness.core.canonical_mutation import CanonicalFile, apply_canonical_mutation
from course_harness.core.presentation import SlideCitation

LECTURE_ID_PATTERN = r"^lecture-[0-9a-f]{12}$"
QUESTION_ID_PATTERN = r"^question-[0-9a-f]{12}$"
OPTION_ID_PATTERN = r"^option-[0-9a-f]{12}$"
QUIZZES_DIRECTORY = "quizzes"
MAX_QUESTIONS = 200
MAX_OPTIONS = 10

# Citations use the Slide format so that Release validation and display treat them alike.
QuizCitation = SlideCitation


class QuizOption(BaseModel):
    """One answer choice of a Question."""

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    id: str = Field(pattern=OPTION_ID_PATTERN)
    text: str = Field(min_length=1, max_length=1_000)
    correct: bool = False
    # Required in substance for a wrong Option (a Release warning), optional for the correct one.
    explanation: str | None = Field(default=None, min_length=1, max_length=4_000)
    citations: list[QuizCitation] = Field(default_factory=list, max_length=50)


class QuizQuestion(BaseModel):
    """One single-answer multiple-choice item."""

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    id: str = Field(pattern=QUESTION_ID_PATTERN)
    prompt: str = Field(min_length=1, max_length=2_000)
    options: list[QuizOption] = Field(min_length=2, max_length=MAX_OPTIONS)
    citations: list[QuizCitation] = Field(default_factory=list, max_length=50)


class Quiz(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    schema_version: Literal[1] = 1
    lecture_id: str = Field(pattern=LECTURE_ID_PATTERN)
    questions: list[QuizQuestion] = Field(min_length=1, max_length=MAX_QUESTIONS)


class InvalidQuiz(ValueError):
    """A Quiz file exists but does not satisfy the schema."""


@dataclass(frozen=True)
class QuizProblem:
    """A broken answer-key rule in one Quiz."""

    code: Literal[
        "quiz.duplicate-question-id", "quiz.duplicate-option-id", "quiz.correct-option-count"
    ]
    message: str
    question_id: str
    option_id: str | None = None


def new_question_id() -> str:
    return f"question-{uuid4().hex[:12]}"


def new_option_id() -> str:
    return f"option-{uuid4().hex[:12]}"


def quiz_path(lecture_id: str) -> str:
    """The Workspace-relative canonical path of a Lecture's Quiz."""
    if re.fullmatch(LECTURE_ID_PATTERN, lecture_id) is None:
        raise ValueError("Lecture ID is invalid")
    return f"{QUIZZES_DIRECTORY}/{lecture_id}.yaml"


def quizzes_dir(workspace: Path) -> Path:
    return workspace / QUIZZES_DIRECTORY


def correct_option(question: QuizQuestion) -> QuizOption | None:
    """The single correct Option, or ``None`` when the answer key is broken."""
    correct = [option for option in question.options if option.correct]
    return correct[0] if len(correct) == 1 else None


def quiz_problems(quiz: Quiz) -> list[QuizProblem]:
    """Report duplicate IDs and Questions without exactly one correct Option."""
    problems: list[QuizProblem] = []
    seen_questions: set[str] = set()
    for question in quiz.questions:
        if question.id in seen_questions:
            problems.append(
                QuizProblem(
                    code="quiz.duplicate-question-id",
                    message=f"Question ID {question.id} appears more than once.",
                    question_id=question.id,
                )
            )
        seen_questions.add(question.id)
        seen_options: set[str] = set()
        for option in question.options:
            if option.id in seen_options:
                problems.append(
                    QuizProblem(
                        code="quiz.duplicate-option-id",
                        message=(
                            f"Option ID {option.id} appears more than once in Question "
                            f"{question.id}."
                        ),
                        question_id=question.id,
                        option_id=option.id,
                    )
                )
            seen_options.add(option.id)
        correct_count = sum(option.correct for option in question.options)
        if correct_count != 1:
            problems.append(
                QuizProblem(
                    code="quiz.correct-option-count",
                    message=(
                        f"Question {question.id} has {correct_count} correct Options; "
                        "it needs exactly one."
                    ),
                    question_id=question.id,
                )
            )
    return problems


def parse_quiz(content: bytes) -> Quiz:
    try:
        return Quiz.model_validate(yaml.safe_load(content.decode("utf-8")))
    except (UnicodeError, yaml.YAMLError, ValidationError, ValueError) as error:
        raise InvalidQuiz(str(error)) from error


def read_quiz(workspace: Path, lecture_id: str) -> Quiz | None:
    path = workspace / quiz_path(lecture_id)
    if not path.exists():
        return None
    try:
        content = path.read_bytes()
    except OSError as error:
        raise InvalidQuiz(str(error)) from error
    quiz = parse_quiz(content)
    if quiz.lecture_id != lecture_id:
        raise InvalidQuiz(f"Quiz file for {lecture_id} names Lecture {quiz.lecture_id}")
    return quiz


def list_quizzes(workspace: Path) -> list[Quiz]:
    """Every readable Quiz whose file name matches its Lecture, in file name order."""
    directory = quizzes_dir(workspace)
    if not directory.is_dir():
        return []
    results: list[Quiz] = []
    for path in sorted(directory.glob("lecture-*.yaml")):
        try:
            quiz = read_quiz(workspace, path.stem)
        except ValueError:
            continue
        if quiz is not None:
            results.append(quiz)
    return results


def serialize_quiz(quiz: Quiz) -> bytes:
    """Return the exact canonical bytes written for a Quiz."""
    return yaml.safe_dump(
        quiz.model_dump(mode="json"), allow_unicode=True, sort_keys=False, width=100
    ).encode("utf-8")


def write_quiz(workspace: Path, quiz: Quiz, *, expected: CanonicalFile | None = None) -> None:
    relative_path = quiz_path(quiz.lecture_id)
    if expected is not None:
        apply_canonical_mutation(
            workspace,
            expected={relative_path: expected},
            updates={relative_path: serialize_quiz(quiz)},
        )
        return
    directory = quizzes_dir(workspace)
    directory.mkdir(parents=True, exist_ok=True)
    temporary_path = directory / f".{quiz.lecture_id}-{uuid4().hex}.yaml.tmp"
    try:
        with temporary_path.open("x", encoding="utf-8") as stream:
            stream.write(serialize_quiz(quiz).decode("utf-8"))
            stream.flush()
            os.fsync(stream.fileno())
        temporary_path.replace(workspace / relative_path)
    finally:
        temporary_path.unlink(missing_ok=True)


def delete_quiz_file(
    workspace: Path, lecture_id: str, *, expected: CanonicalFile | None = None
) -> bool:
    relative_path = quiz_path(lecture_id)
    if expected is not None:
        if expected.content is None:
            return False
        apply_canonical_mutation(
            workspace, expected={relative_path: expected}, updates={relative_path: None}
        )
        return True
    path = workspace / relative_path
    if not path.exists():
        return False
    path.unlink()
    return True


def question_by_id(quiz: Quiz, question_id: str) -> QuizQuestion | None:
    return next((question for question in quiz.questions if question.id == question_id), None)
