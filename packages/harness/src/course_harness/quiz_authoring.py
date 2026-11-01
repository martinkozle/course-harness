"""How the Course Agent creates and revises a Lecture's Quiz.

The agent refers to existing Questions and Options by ID and leaves the ID out for new ones,
so it never invents an ID: :func:`build_quiz` mints them.  Nothing is removed unless the
command names it: Questions and Options the command leaves out stay as they are, and a field
left out or given as null keeps its current value.

Order follows one rule for Questions and for the Options of a Question:
when the command lists every existing item it keeps, the order it gives is used, new items
included.  Otherwise existing items stay where they are, the existing items it lists fill their
own current positions in the order they are listed, and new items are added at the end.
"""

from collections.abc import Callable, Iterable, Mapping, Sequence
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

ORDER_RULE = (
    "When you list every existing item you keep, the order you give is used. Otherwise "
    "existing items stay where they are, the ones you list take their own positions in the "
    "order you list them, and new items are added at the end."
)


class QuizOptionCommand(BaseModel):
    """One Option of a Question. Fields left out or given as null keep their current value."""

    model_config = ConfigDict(extra="forbid")

    id: str | None = Field(
        default=None,
        pattern=OPTION_ID_PATTERN,
        description=(
            "The ID of an Option this Question already has, to change it. Leave it out for a "
            "new Option; a new ID is created. Keep the ID when you fix the wording, the "
            "Explanation or which Option is correct; remove the Option and add a new one when "
            "it comes to say something different."
        ),
    )
    text: str | None = Field(
        default=None,
        max_length=1_000,
        description="Plain text of the answer choice, without a letter or number in front.",
    )
    correct: bool | None = Field(
        default=None,
        description=(
            "True for the one correct Option of the Question. When you change it on an "
            "existing Option, also give its new explanation."
        ),
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
            "The ID of a Question this Quiz already has, to change it. Leave it out for a new "
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
            f"Options to add or change; a Question has two to {MAX_OPTIONS} Options, exactly "
            "one of them correct. Options you leave out stay unchanged. " + ORDER_RULE
        ),
    )
    remove_option_ids: list[str] = Field(
        default_factory=list,
        max_length=MAX_OPTIONS,
        description="IDs of this Question's Options to remove.",
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


def _merge_order[T, C](
    current: Sequence[T],
    commands: Sequence[C],
    key: Callable[[T], str],
    existing_id: Callable[[C], str | None],
    removed: set[str],
    build: Callable[[C, T | None], T],
) -> list[T]:
    """Apply ``ORDER_RULE``: build every item, keeping untouched ones as they are."""
    kept = [item for item in current if key(item) not in removed]
    by_id = {key(item): item for item in kept}
    listed = [existing_id(command) for command in commands]
    listed_existing = {identity for identity in listed if identity is not None}
    if kept and listed_existing == set(by_id):
        return [
            build(command, by_id[identity] if identity is not None else None)
            for command, identity in zip(commands, listed, strict=True)
        ]
    updates = iter(
        build(command, by_id[identity])
        for command, identity in zip(commands, listed, strict=True)
        if identity is not None
    )
    ordered = [next(updates) if key(item) in listed_existing else item for item in kept]
    ordered.extend(
        build(command, None)
        for command, identity in zip(commands, listed, strict=True)
        if identity is None
    )
    return ordered


def build_quiz(
    lecture_id: str,
    questions: Sequence[QuizQuestionCommand],
    existing: Quiz | None,
    *,
    remove_question_ids: Sequence[str] = (),
    replace_all_questions: bool = False,
    source_ids: Iterable[str] | None = None,
) -> QuizRevision:
    """Apply a save command on top of a Lecture's current Quiz.

    Raises ``ValueError`` with a message the Course Agent can act on.
    """
    current = list(existing.questions) if existing else []
    current_ids = {question.id: question for question in current}
    known_sources = set(source_ids) if source_ids is not None else None
    removed = _checked_removals(
        "remove_question_ids", remove_question_ids, current_ids, "a Question of this Quiz"
    )
    seen: set[str] = set()
    for number, command in enumerate(questions, start=1):
        if command.id is None:
            continue
        if command.id not in current_ids:
            raise ValueError(
                f"Question {number}: {command.id} is not a Question of this Quiz. Leave the id "
                "out for a new Question, or use one of "
                f"{_question_list(current) if current else 'none (the Quiz is new)'}."
            )
        if command.id in seen:
            raise ValueError(f"Question {number}: Question {command.id} is given more than once.")
        if command.id in removed:
            raise ValueError(
                f"Question {number}: Question {command.id} is also in remove_question_ids; "
                "either change it or remove it."
            )
        seen.add(command.id)
    option_owner = {option.id: question.id for question in current for option in question.options}
    number_of = {id(command): number for number, command in enumerate(questions, start=1)}
    removed_options: list[str] = []

    def build(command: QuizQuestionCommand, previous: QuizQuestion | None) -> QuizQuestion:
        label = f"Question {number_of[id(command)]}"
        options, gone = _build_options(label, command, previous, option_owner, known_sources)
        removed_options.extend(gone)
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
        _check_citations(label, citations, known_sources)
        identity = previous.id if previous else new_question_id()
        try:
            return QuizQuestion(id=identity, prompt=prompt, options=options, citations=citations)
        except ValidationError as error:
            raise ValueError(f"{label}: {_readable(error)}") from None

    if replace_all_questions:
        removed = removed | (set(current_ids) - seen)
    built = _merge_order(
        current,
        questions,
        key=lambda question: question.id,
        existing_id=lambda command: command.id,
        removed=removed,
        build=build,
    )
    if not built:
        raise ValueError(
            "A Quiz needs at least one Question. Use delete_quiz to remove the whole Quiz."
        )
    if len(built) > MAX_QUESTIONS:
        raise ValueError(f"A Quiz holds at most {MAX_QUESTIONS} Questions.")
    return QuizRevision(
        quiz=Quiz(lecture_id=lecture_id, questions=built),
        removed_question_ids=[question.id for question in current if question.id in removed],
        removed_option_ids=removed_options,
    )


def _checked_removals(
    field: str, identities: Sequence[str], current: Mapping[str, object], what: str
) -> set[str]:
    for identity in identities:
        if identity not in current:
            listed = ", ".join(current) or "none"
            raise ValueError(f"{field}: {identity} is not {what}. Its IDs are {listed}.")
    return set(identities)


def _build_options(
    label: str,
    command: QuizQuestionCommand,
    previous: QuizQuestion | None,
    option_owner: dict[str, str],
    known_sources: set[str] | None,
) -> tuple[list[QuizOption], list[str]]:
    if previous is None and command.options is None:
        raise ValueError(f"{label} is new, so it needs its options.")
    if previous is None and command.remove_option_ids:
        raise ValueError(f"{label} is new, so it has no Options to remove.")
    current = list(previous.options) if previous else []
    current_ids = {option.id: option for option in current}
    removed = _checked_removals(
        f"{label} remove_option_ids",
        command.remove_option_ids,
        current_ids,
        "an Option of this Question",
    )
    commands = command.options or []
    seen: set[str] = set()
    for position, option in enumerate(commands, start=1):
        if option.id is None:
            continue
        where = f"{label}, Option {position}"
        if option.id not in current_ids:
            owner = option_owner.get(option.id)
            hint = (
                f" It belongs to Question {owner}; an Option cannot move to another "
                "Question, so leave its id out to add it here as a new Option."
                if owner is not None
                else " Leave the id out for a new Option."
            )
            raise ValueError(
                f"{where}: {option.id} is not an Option of this Question.{hint}"
                + (f" Its Options are {', '.join(current_ids)}." if current_ids else "")
            )
        if option.id in seen:
            raise ValueError(f"{where}: Option {option.id} is given more than once.")
        if option.id in removed:
            raise ValueError(
                f"{where}: Option {option.id} is also in remove_option_ids; either change it "
                "or remove it."
            )
        seen.add(option.id)
    position_of = {id(option): position for position, option in enumerate(commands, start=1)}

    def build(option: QuizOptionCommand, existing: QuizOption | None) -> QuizOption:
        where = f"{label}, Option {position_of[id(option)]}"
        text = option.text if option.text is not None else (existing.text if existing else None)
        if not text or not text.strip():
            raise ValueError(f"{where} needs its text.")
        correct = (
            option.correct
            if option.correct is not None
            else (existing.correct if existing else False)
        )
        if existing is not None and correct != existing.correct and option.explanation is None:
            becomes = "correct" if correct else "wrong"
            raise ValueError(
                f"{where} ({existing.id}) becomes {becomes}, so its Explanation no longer fits. "
                'Give it a new explanation, or "" to remove it.'
            )
        if option.explanation is not None:
            explanation = option.explanation if option.explanation.strip() else None
        else:
            explanation = existing.explanation if existing else None
        citations = (
            option.citations
            if option.citations is not None
            else (existing.citations if existing else [])
        )
        _check_citations(where, citations, known_sources)
        try:
            return QuizOption(
                id=existing.id if existing else new_option_id(),
                text=text,
                correct=correct,
                explanation=explanation,
                citations=citations,
            )
        except ValidationError as error:
            raise ValueError(f"{where}: {_readable(error)}") from None

    options = _merge_order(
        current,
        commands,
        key=lambda option: option.id,
        existing_id=lambda option: option.id,
        removed=removed,
        build=build,
    )
    if not 2 <= len(options) <= MAX_OPTIONS:
        raise ValueError(f"{label} would have {len(options)} Options; it needs 2 to {MAX_OPTIONS}.")
    correct = [option.id for option in options if option.correct]
    if len(correct) != 1:
        named = f" ({', '.join(correct)})" if correct else ""
        raise ValueError(
            f"{label} would have {len(correct)} correct Options{named}; exactly one must be "
            "correct. Set correct on each Option whose answer changes, with its new "
            "explanation."
        )
    texts = [option.text.casefold() for option in options]
    repeated = next((text for text in texts if texts.count(text) > 1), None)
    if repeated is not None:
        raise ValueError(f"{label} has two Options with the same text: {repeated!r}.")
    return options, [option.id for option in current if option.id in removed]


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
