"""Derived grading: whether each Answer is correct and what each student scores on a Question,
judged against one grading Release's Quizzes (ADR 0018).

Nothing here touches the store, Git or HTTP: callers hand in Answers and the grading Release's
Questions, so the rule can be tested on its own and an answer-key fix in a newer Release regrades
older Answers simply by grading them against it.

The score rule. A student retries a Question until they pick its correct Option. Attempt k earns
``0.5 ** (k - 1)``, except that the attempt at which only one Option remains untried earns 0: with
four Options the scores are 1, 0.5, 0.25 and 0. Attempts are counted as *distinct* wrong Options
picked before the first correct pick, per student and (Lecture, Question):

- Picking the same wrong Option again costs nothing more. The Classroom does not record a repeated
  pick, so a stored ``Answer.attempt`` is the number of distinct Options tried and the score of a
  first correct pick at stored attempt k is the rule's score for attempt k; counting distinct
  Options here keeps that true for any repeated rows recorded before that rule.
- An Option the grading Release does not have is not counted, since nothing says whether it was
  wrong; such Answers are flagged instead.
- Picks after the first correct one are ignored. They exist only when the grading Release's
  answer key differs from the key the student answered under.

A student who never picked the correct Option scores 0 on that Question and is not counted as
correct. A Question without exactly one correct Option cannot be graded.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass

from course_harness.core.quiz import QuizQuestion, correct_option


@dataclass(frozen=True)
class AnswerKey:
    """What grading needs of a Question: its Options and its correct one."""

    question_id: str
    option_ids: tuple[str, ...]
    correct_option_id: str | None

    @classmethod
    def of(cls, question: QuizQuestion) -> AnswerKey:
        key = correct_option(question)
        return cls(
            question_id=question.id,
            option_ids=tuple(option.id for option in question.options),
            correct_option_id=key.id if key else None,
        )


@dataclass(frozen=True)
class QuestionGrade:
    """One student's derived result on one Question."""

    # Whether each pick, in order, was the correct Option; ``None`` when the pick cannot be
    # judged (an Option the grading Release does not have, or a broken answer key).
    pick_correct: tuple[bool | None, ...]
    correct: bool
    # Distinct known wrong Options picked before the first correct pick (or in all, if none).
    wrong_options: int
    # The pick index of the first correct pick, if any.
    solved_at: int | None
    score: float
    first_try_correct: bool
    gradable: bool


def attempt_score(wrong_before: int, option_count: int) -> float:
    """The score of a correct pick after ``wrong_before`` distinct wrong Options.

    The pick is attempt ``wrong_before + 1``; it earns 0 when only one Option was left untried.
    """
    if wrong_before < 0 or option_count < 1:
        raise ValueError("Attempts and Options must be counted from zero and one")
    if option_count - wrong_before <= 1:
        return 0.0
    return 0.5**wrong_before


def grade_question(picks: Sequence[str], key: AnswerKey) -> QuestionGrade:
    """Grade one student's picks on one Question, in the order they were made."""
    if key.correct_option_id is None:
        return QuestionGrade(
            pick_correct=tuple(None for _ in picks),
            correct=False,
            wrong_options=0,
            solved_at=None,
            score=0.0,
            first_try_correct=False,
            gradable=False,
        )
    known = set(key.option_ids)
    pick_correct: list[bool | None] = []
    wrong: set[str] = set()
    solved_at: int | None = None
    for index, option_id in enumerate(picks):
        if option_id not in known:
            pick_correct.append(None)
            continue
        is_correct = option_id == key.correct_option_id
        pick_correct.append(is_correct)
        if solved_at is not None:
            continue
        if is_correct:
            solved_at = index
        else:
            wrong.add(option_id)
    score = attempt_score(len(wrong), len(key.option_ids)) if solved_at is not None else 0.0
    return QuestionGrade(
        pick_correct=tuple(pick_correct),
        correct=solved_at is not None,
        wrong_options=len(wrong),
        solved_at=solved_at,
        score=score,
        first_try_correct=solved_at is not None and not wrong,
        gradable=True,
    )
