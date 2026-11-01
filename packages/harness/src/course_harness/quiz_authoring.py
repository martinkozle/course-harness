"""How the Course Agent creates and revises a Lecture's Quiz.

The agent refers to existing Questions and Options by ID and leaves the ID out for new ones,
so it never invents an ID: :func:`build_quiz` mints them.  A field left out or given as null
keeps its current value, so the agent can revise one Question without repeating the others.
"""

from collections import Counter
from collections.abc import Iterable, Sequence
from dataclasses import dataclass

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from course_harness.core.presentation import SlideCitation
from course_harness.core.quiz import (
    MAX_OPTIONS,
    MAX_QUESTIONS,
    OPTION_ID_PATTERN,
    QUESTION_ID_PATTERN,
    Quiz,
    QuizOption,
    QuizQuestion,
    new_option_id,
    new_question_id,
)


class QuizOptionCommand(BaseModel):
    """One Option of a Question. Fields left out or given as null keep their current value."""

    model_config = ConfigDict(extra="forbid")

    id: str | None = Field(
        default=None,
        pattern=OPTION_ID_PATTERN,
        description=(
            "The ID of an Option this Question already has, to keep it. Leave it out for a new "
            "Option; a new ID is created. Keep the ID when you fix the wording, the Explanation "
            "or which Option is correct; leave it out when the Option comes to say something "
            "different."
        ),
    )
    text: str | None = Field(
        default=None,
        max_length=1_000,
        description="Plain text of the answer choice, without a letter or number in front.",
    )
    correct: bool | None = Field(
        default=None, description="True for the one correct Option of the Question."
    )
    explanation: str | None = Field(
        default=None,
        max_length=4_000,
        description=(
            "Markdown shown when this Option is picked. For a wrong Option, say why it is "
            "wrong, grounded in the Sources; for the correct Option it is optional. Give an "
            'empty string ("") to remove it.'
        ),
    )
    citations: list[SlideCitation] | None = Field(
        default=None,
        max_length=50,
        description="Course Sources that support the Explanation, as on Slides.",
    )


class QuizQuestionCommand(BaseModel):
    """One Question. Fields left out or given as null keep their current value."""

    model_config = ConfigDict(extra="forbid")

    id: str | None = Field(
        default=None,
        pattern=QUESTION_ID_PATTERN,
        description=(
            "The ID of a Question this Quiz already has, to keep it. Leave it out for a new "
            "Question; a new ID is created."
        ),
    )
    prompt: str | None = Field(
        default=None, max_length=2_000, description="The question, in Markdown."
    )
    options: list[QuizOptionCommand] | None = Field(
        default=None,
        max_length=MAX_OPTIONS,
        description=(
            f"Two to {MAX_OPTIONS} Options in the order shown, exactly one of them correct. "
            "When given, this is the full list: Options of this Question you leave out are "
            "removed. Leave it out to keep the current Options."
        ),
    )
    citations: list[SlideCitation] | None = Field(
        default=None,
        max_length=50,
        description="Course Sources that support the Question and its correct answer.",
    )


@dataclass(frozen=True)
class QuizRevision:
    """A Quiz built from a command, and what the command removed."""

    quiz: Quiz
    removed_question_ids: list[str]
    removed_option_ids: list[str]


def build_quiz(
    lecture_id: str,
    questions: Sequence[QuizQuestionCommand],
    existing: Quiz | None,
    *,
    replace_all_questions: bool = False,
    source_ids: Iterable[str] | None = None,
) -> QuizRevision:
    """Apply a save command on top of a Lecture's current Quiz.

    ``questions`` is the full Question order: existing Questions left out are removed.
    Raises ``ValueError`` with a message the Course Agent can act on.
    """
    if not questions:
        raise ValueError(
            "A Quiz needs at least one Question. Use delete_quiz to remove the whole Quiz."
        )
    if len(questions) > MAX_QUESTIONS:
        raise ValueError(f"A Quiz holds at most {MAX_QUESTIONS} Questions.")
    current = {question.id: question for question in existing.questions} if existing else {}
    option_owner = {
        option.id: question.id for question in current.values() for option in question.options
    }
    known_sources = set(source_ids) if source_ids is not None else None
    used: set[str] = set()
    built: list[QuizQuestion] = []
    removed_options: list[str] = []
    for number, command in enumerate(questions, start=1):
        label = f"Question {number}"
        previous = None
        if command.id is not None:
            if command.id not in current:
                raise ValueError(
                    f"{label}: {command.id} is not a Question of this Quiz. Leave the id out "
                    "for a new Question, or use one of "
                    f"{_question_list(current.values()) if current else 'none (the Quiz is new)'}."
                )
            if command.id in used:
                raise ValueError(f"{label}: Question {command.id} is given more than once.")
            previous = current[command.id]
        identity = command.id or new_question_id()
        used.add(identity)
        options, removed = _build_options(
            label, command.options, previous, option_owner, known_sources
        )
        removed_options.extend(removed)
        prompt = (
            command.prompt
            if command.prompt is not None
            else (previous.prompt if previous else None)
        )
        if not prompt or not prompt.strip():
            raise ValueError(f"{label} needs a prompt.")
        citations = (
            command.citations
            if command.citations is not None
            else (previous.citations if previous else [])
        )
        _check_citations(f"{label}", citations, known_sources)
        try:
            built.append(
                QuizQuestion(id=identity, prompt=prompt, options=options, citations=citations)
            )
        except ValidationError as error:
            raise ValueError(f"{label}: {_readable(error)}") from None
    if current and not replace_all_questions and not used & current.keys():
        raise ValueError(
            "Revising a Quiz must keep existing Question IDs so that earlier Answers stay "
            "linked: set the id of each Question you keep to one of "
            f"{_question_list(current.values())}. Set replace_all_questions only when the "
            "Course Author explicitly asks to replace every Question."
        )
    removed_questions = [identity for identity in current if identity not in used]
    return QuizRevision(
        quiz=Quiz(lecture_id=lecture_id, questions=built),
        removed_question_ids=removed_questions,
        removed_option_ids=removed_options,
    )


def _build_options(
    label: str,
    commands: list[QuizOptionCommand] | None,
    previous: QuizQuestion | None,
    option_owner: dict[str, str],
    known_sources: set[str] | None,
) -> tuple[list[QuizOption], list[str]]:
    if commands is None:
        if previous is None:
            raise ValueError(f"{label} is new, so it needs its options.")
        return list(previous.options), []
    if len(commands) < 2:
        raise ValueError(f"{label} needs at least two Options.")
    current = {option.id: option for option in previous.options} if previous else {}
    # Marking one Option correct is enough to move the answer: the others become wrong.
    moves_answer = any(command.correct is True for command in commands)
    used: set[str] = set()
    options: list[QuizOption] = []
    for position, command in enumerate(commands, start=1):
        where = f"{label}, Option {position}"
        existing = None
        if command.id is not None:
            if command.id not in current:
                owner = option_owner.get(command.id)
                hint = (
                    f" It belongs to Question {owner}; an Option cannot move to another "
                    "Question, so leave its id out to add it here as a new Option."
                    if owner is not None
                    else " Leave the id out for a new Option."
                )
                raise ValueError(
                    f"{where}: {command.id} is not an Option of this Question.{hint}"
                    + (f" Its Options are {', '.join(current)}." if current else "")
                )
            if command.id in used:
                raise ValueError(f"{where}: Option {command.id} is given more than once.")
            existing = current[command.id]
        identity = command.id or new_option_id()
        used.add(identity)
        text = command.text if command.text is not None else (existing.text if existing else None)
        if not text or not text.strip():
            raise ValueError(f"{where} needs its text.")
        explanation = (
            (command.explanation or None)
            if command.explanation is not None
            else (existing.explanation if existing else None)
        )
        if explanation is not None and not explanation.strip():
            explanation = None
        citations = (
            command.citations
            if command.citations is not None
            else (existing.citations if existing else [])
        )
        _check_citations(where, citations, known_sources)
        if command.correct is not None:
            correct = command.correct
        else:
            correct = existing is not None and existing.correct and not moves_answer
        try:
            options.append(
                QuizOption(
                    id=identity,
                    text=text,
                    correct=correct,
                    explanation=explanation,
                    citations=citations,
                )
            )
        except ValidationError as error:
            raise ValueError(f"{where}: {_readable(error)}") from None
    correct_count = sum(option.correct for option in options)
    if correct_count != 1:
        raise ValueError(
            f"{label} has {correct_count} correct Options; set correct to true on exactly "
            "one Option."
        )
    repeated = [
        text for text, count in Counter(o.text.casefold() for o in options).items() if count > 1
    ]
    if repeated:
        raise ValueError(f"{label} has two Options with the same text: {repeated[0]!r}.")
    removed = [identity for identity in current if identity not in used]
    return options, removed


def _check_citations(
    where: str, citations: Sequence[SlideCitation], known_sources: set[str] | None
) -> None:
    if known_sources is None:
        return
    for citation in citations:
        if citation.source_id not in known_sources:
            raise ValueError(
                f"{where} cites {citation.source_id}, which is not a Source of this Course. "
                "Use list_sources to find the Source you mean."
            )


def _question_list(questions: Iterable[QuizQuestion]) -> str:
    return ", ".join(f"{question.id} ({_excerpt(question.prompt)})" for question in questions)


def _excerpt(text: str, limit: int = 60) -> str:
    flat = " ".join(text.split())
    return flat if len(flat) <= limit else flat[: limit - 1] + "…"


def _readable(error: ValidationError) -> str:
    return "; ".join(
        f"{'.'.join(str(part) for part in item['loc']) or 'value'}: {item['msg']}"
        for item in error.errors()
    )


def quiz_outline(quiz: Quiz) -> str:
    """The saved Quiz with every ID, so the agent can revise it without reading it again."""
    lines: list[str] = []
    for number, question in enumerate(quiz.questions, start=1):
        lines.append(f"{number}. {question.id}: {_excerpt(question.prompt, 100)}")
        for letter, option in zip("ABCDEFGHIJ", question.options, strict=False):
            mark = " [correct]" if option.correct else ""
            lines.append(f"   {letter}. {option.id}: {_excerpt(option.text, 80)}{mark}")
    return "\n".join(lines)


def quiz_notes(quiz: Quiz) -> list[str]:
    """What to improve before a Release; none of it keeps the Quiz from being saved."""
    notes: list[str] = []
    for number, question in enumerate(quiz.questions, start=1):
        unexplained = [
            option.id
            for option in question.options
            if not option.correct and not option.explanation
        ]
        if unexplained:
            notes.append(
                f"Question {number} has wrong Options without an Explanation: "
                f"{', '.join(unexplained)}. Explain why each one is wrong."
            )
        if not question.citations and not any(option.citations for option in question.options):
            notes.append(f"Question {number} cites no Source.")
    positions = {
        next(index for index, option in enumerate(question.options) if option.correct)
        for question in quiz.questions
    }
    if len(quiz.questions) >= 3 and len(positions) == 1:
        notes.append(
            f"The correct Option is in position {positions.pop() + 1} in every Question; vary "
            "its position."
        )
    return notes
