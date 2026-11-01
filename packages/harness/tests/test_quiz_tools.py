"""The Course Agent writes Quizzes whose Question and Option IDs stay stable."""

import json
import re
from collections.abc import AsyncIterator
from pathlib import Path

import httpx2
import pytest
from pydantic_ai.messages import ModelMessage, ModelRequest, ToolReturnPart
from pydantic_ai.models.function import AgentInfo, DeltaToolCall, FunctionModel

from course_harness.app import create_app
from course_harness.core.course_plan import (
    CoursePlan,
    Lecture,
    create_course_plan_file,
    initialize_workspace_history,
)
from course_harness.core.presentation import SlideCitation
from course_harness.core.quiz import (
    OPTION_ID_PATTERN,
    QUESTION_ID_PATTERN,
    Quiz,
    QuizOption,
    QuizQuestion,
    quiz_problems,
    read_quiz,
    write_quiz,
)
from course_harness.core.sources import Source
from course_harness.course_agent import (
    CourseAgentDeps,
    CourseAgentState,
    _build_course_agent,
    course_state_snapshot,
)
from course_harness.quiz_authoring import (
    QuizOptionCommand,
    QuizQuestionCommand,
    build_quiz,
    quiz_notes,
)
from course_harness.workspace_history import record_app_authored_state

LECTURE_ID = "lecture-aaaaaaaaaaaa"
OTHER_LECTURE_ID = "lecture-bbbbbbbbbbbb"
SOURCE_ID = "source-aaaaaaaaaaaa"
CITATION = {"source_id": SOURCE_ID, "label": "What If, ch. 1", "line_start": 0, "line_end": 4}
Q1 = "question-aaaaaaaaaaaa"
Q2 = "question-bbbbbbbbbbbb"
POSITIVITY = "option-aaaaaaaaaaaa"
EXCHANGEABILITY = "option-bbbbbbbbbbbb"
CONSISTENCY = "option-cccccccccccc"


def _existing() -> Quiz:
    citation = SlideCitation.model_validate(CITATION)
    return Quiz(
        lecture_id=LECTURE_ID,
        questions=[
            QuizQuestion(
                id=Q1,
                prompt="Which assumption says every unit could receive each treatment?",
                options=[
                    QuizOption(id=POSITIVITY, text="Positivity", correct=True),
                    QuizOption(
                        id=EXCHANGEABILITY,
                        text="Exchangeability",
                        explanation="Exchangeability is about comparable groups.",
                        citations=[citation],
                    ),
                    QuizOption(
                        id=CONSISTENCY,
                        text="Consistency",
                        explanation="Consistency links observed and potential outcomes.",
                    ),
                ],
                citations=[citation],
            ),
            QuizQuestion(
                id=Q2,
                prompt="What does a **DAG** encode?",
                options=[
                    QuizOption(id="option-dddddddddddd", text="Causal assumptions", correct=True),
                    QuizOption(
                        id="option-eeeeeeeeeeee",
                        text="Estimated effects",
                        explanation="A DAG holds no numbers.",
                    ),
                ],
            ),
        ],
    )


def _new_question(**changes: object) -> QuizQuestionCommand:
    return QuizQuestionCommand.model_validate(
        {
            "prompt": "Why randomize?",
            "options": [
                {"text": "To make groups exchangeable", "correct": True},
                {"text": "To increase the sample", "explanation": "Randomizing adds no units."},
            ],
            "citations": [CITATION],
            **changes,
        }
    )


def test_a_new_quiz_gets_ids_minted_for_every_question_and_option() -> None:
    revision = build_quiz(LECTURE_ID, [_new_question()], None, source_ids={SOURCE_ID})

    (question,) = revision.quiz.questions
    assert re.fullmatch(QUESTION_ID_PATTERN, question.id)
    assert all(re.fullmatch(OPTION_ID_PATTERN, option.id) for option in question.options)
    assert [option.correct for option in question.options] == [True, False]
    assert quiz_problems(revision.quiz) == []


def test_a_revision_keeps_ids_and_unmentioned_content() -> None:
    """Sending only IDs keeps a Question; a rewritten Option keeps its ID and its other fields."""
    existing = _existing()
    revision = build_quiz(
        LECTURE_ID,
        [
            QuizQuestionCommand(id=Q2),
            QuizQuestionCommand(
                id=Q1,
                options=[
                    QuizOptionCommand(id=CONSISTENCY),
                    QuizOptionCommand(id=POSITIVITY, text="Positivity (overlap)"),
                    QuizOptionCommand(id=EXCHANGEABILITY),
                    QuizOptionCommand(text="Interference", explanation="That is about spillover."),
                ],
            ),
        ],
        existing,
    )

    second, first = revision.quiz.questions
    assert second == existing.questions[1]
    assert first.prompt == existing.questions[0].prompt
    assert first.citations == existing.questions[0].citations
    assert [option.id for option in first.options[:3]] == [CONSISTENCY, POSITIVITY, EXCHANGEABILITY]
    assert first.options[1].text == "Positivity (overlap)" and first.options[1].correct
    assert first.options[2] == existing.questions[0].options[1]
    assert re.fullmatch(OPTION_ID_PATTERN, first.options[3].id)
    assert revision.removed_question_ids == []
    assert revision.removed_option_ids == []


def test_marking_another_option_correct_moves_the_answer_and_keeps_ids() -> None:
    revision = build_quiz(
        LECTURE_ID,
        [
            QuizQuestionCommand(
                id=Q1,
                options=[
                    QuizOptionCommand(id=POSITIVITY, explanation="Positivity is about overlap."),
                    QuizOptionCommand(id=EXCHANGEABILITY, correct=True, explanation=""),
                ],
            ),
            QuizQuestionCommand(id=Q2),
        ],
        _existing(),
    )

    first = revision.quiz.questions[0]
    assert [(option.id, option.correct) for option in first.options] == [
        (POSITIVITY, False),
        (EXCHANGEABILITY, True),
    ]
    assert first.options[1].explanation is None
    assert revision.removed_option_ids == [CONSISTENCY]


@pytest.mark.parametrize(
    ("questions", "message"),
    [
        ([], "at least one Question. Use delete_quiz"),
        (
            [QuizQuestionCommand(id="question-ffffffffffff")],
            "question-ffffffffffff is not a Question of this Quiz",
        ),
        (
            [QuizQuestionCommand(id=Q1), QuizQuestionCommand(id=Q1)],
            f"Question {Q1} is given more than once",
        ),
        (
            [
                QuizQuestionCommand(
                    id=Q1,
                    options=[
                        QuizOptionCommand(id=POSITIVITY),
                        QuizOptionCommand(id="option-dddddddddddd"),
                    ],
                )
            ],
            f"It belongs to Question {Q2}",
        ),
        (
            [
                QuizQuestionCommand(
                    id=Q1,
                    options=[
                        QuizOptionCommand(id=POSITIVITY, correct=False),
                        QuizOptionCommand(id=EXCHANGEABILITY),
                    ],
                )
            ],
            "Question 1 has 0 correct Options",
        ),
        ([QuizQuestionCommand(prompt="New?")], "Question 1 is new, so it needs its options"),
        (
            [QuizQuestionCommand(id=Q1), _new_question(citations=[{**CITATION, "source_id": "x"}])],
            "Question 2 cites x, which is not a Source of this Course",
        ),
        (
            [
                QuizQuestionCommand(id=Q1),
                _new_question(options=[{"text": "Same", "correct": True}, {"text": "same"}]),
            ],
            "two Options with the same text",
        ),
    ],
)
def test_invalid_commands_explain_how_to_fix_them(
    questions: list[QuizQuestionCommand], message: str
) -> None:
    with pytest.raises(ValueError, match=re.escape(message)):
        build_quiz(LECTURE_ID, questions, _existing(), source_ids={SOURCE_ID})


def test_a_revision_must_keep_some_question_unless_replacing_all() -> None:
    with pytest.raises(ValueError, match="must keep existing Question IDs") as raised:
        build_quiz(LECTURE_ID, [_new_question()], _existing())
    assert Q1 in str(raised.value) and Q2 in str(raised.value)

    revision = build_quiz(LECTURE_ID, [_new_question()], _existing(), replace_all_questions=True)
    assert revision.removed_question_ids == [Q1, Q2]


def test_notes_name_what_to_fix_before_a_release() -> None:
    revision = build_quiz(
        LECTURE_ID,
        [_new_question(citations=[]), _new_question(), _new_question()],
        None,
    )
    revision.quiz.questions[1].options[1].explanation = None

    notes = quiz_notes(revision.quiz)

    assert notes[0] == "Question 1 cites no Source."
    assert notes[1].startswith("Question 2 has wrong Options without an Explanation")
    assert "position 1 in every Question" in notes[2]


def _workspace(tmp_path: Path) -> tuple[Path, CoursePlan]:
    workspace = tmp_path / "course"
    workspace.mkdir()
    plan = CoursePlan(
        id="course-aaaaaaaaaaaa",
        title="Causal inference",
        audience="Graduate students",
        lectures=[
            Lecture(id=LECTURE_ID, title="Foundations"),
            Lecture(id=OTHER_LECTURE_ID, title="Applications"),
        ],
    )
    initialize_workspace_history(workspace)
    create_course_plan_file(workspace, plan)
    record_app_authored_state(workspace)
    return workspace, plan


def _source() -> Source:
    return Source(
        id=SOURCE_ID,
        resource_id="resource-aaaaaaaaaaaa",
        source_version_id="a" * 64,
        label="Causal Inference: What If",
        admitted_at="2026-09-14T00:00:00+00:00",
    )


async def _run(
    workspace: Path, tmp_path: Path, plan: CoursePlan, calls: list[tuple[str, dict[str, object]]]
) -> list[str]:
    returns: list[str] = []

    async def stream(
        messages: list[ModelMessage], _info: AgentInfo
    ) -> AsyncIterator[str | dict[int, DeltaToolCall]]:
        done = [
            part
            for message in messages
            if isinstance(message, ModelRequest)
            for part in message.parts
            if isinstance(part, ToolReturnPart)
        ]
        if done:
            returns.append(str(done[-1].content))
        if len(done) < len(calls):
            name, args = calls[len(done)]
            yield {0: DeltaToolCall(name=name, json_args=json.dumps(args), tool_call_id=name)}
            return
        yield "Done."

    agent = _build_course_agent(requires_approval=False)
    async with agent.run_stream(
        "Write a Quiz.",
        deps=CourseAgentDeps(
            course_state=CourseAgentState(course=plan, sources=[_source()]),
            workspace=workspace,
            data_dir=tmp_path / "data",
            cache_dir=tmp_path / "cache",
        ),
        model=FunctionModel(stream_function=stream),
    ) as streamed:
        await streamed.get_output()
    return returns


@pytest.mark.anyio
async def test_the_agent_saves_reads_and_deletes_a_quiz(tmp_path: Path) -> None:
    workspace, plan = _workspace(tmp_path)
    question = _new_question().model_dump(exclude_none=True)

    returns = await _run(
        workspace,
        tmp_path,
        plan,
        [
            ("save_quiz", {"lecture_id": "lecture-ffffffffffff", "questions": [question]}),
            ("save_quiz", {"lecture_id": LECTURE_ID, "questions": [question]}),
            ("read_quiz", {"lecture_id": LECTURE_ID}),
            ("read_quiz", {"lecture_id": OTHER_LECTURE_ID}),
        ],
    )

    assert returns[0].startswith("The Quiz was not saved: Lecture lecture-ffffffffffff")
    assert LECTURE_ID in returns[0]
    saved = read_quiz(workspace, LECTURE_ID)
    assert saved is not None
    assert saved.questions[0].id in returns[1]
    assert saved.questions[0].options[1].id in returns[1]
    assert "Every wrong Option is explained" in returns[1]
    assert saved.questions[0].id in returns[2]
    assert "has no Quiz yet" in returns[3]

    revised_prompt = "Why do randomized trials *balance* confounders?"
    returns = await _run(
        workspace,
        tmp_path,
        plan,
        [
            (
                "save_quiz",
                {
                    "lecture_id": LECTURE_ID,
                    "questions": [{"id": saved.questions[0].id, "prompt": revised_prompt}],
                },
            ),
            ("delete_quiz", {"lecture_id": LECTURE_ID}),
            ("delete_quiz", {"lecture_id": LECTURE_ID}),
        ],
    )

    assert "Saved the Quiz" in returns[0]
    assert returns[1] == f"Deleted the Quiz for Lecture {LECTURE_ID}."
    assert returns[2] == f"Lecture {LECTURE_ID} has no Quiz."
    assert read_quiz(workspace, LECTURE_ID) is None


@pytest.mark.anyio
async def test_a_revision_keeps_the_ids_learners_answered(tmp_path: Path) -> None:
    workspace, plan = _workspace(tmp_path)
    write_quiz(workspace, _existing())

    await _run(
        workspace,
        tmp_path,
        plan,
        [
            (
                "save_quiz",
                {
                    "lecture_id": LECTURE_ID,
                    "questions": [
                        {"id": Q1, "prompt": "Which assumption is about overlap?"},
                        {"id": Q2},
                    ],
                },
            )
        ],
    )

    stored = read_quiz(workspace, LECTURE_ID)
    assert stored is not None
    assert stored.questions[0].prompt == "Which assumption is about overlap?"
    assert [option.id for option in stored.questions[0].options] == [
        POSITIVITY,
        EXCHANGEABILITY,
        CONSISTENCY,
    ]
    assert stored.questions[1] == _existing().questions[1]


def test_the_course_state_names_lectures_with_quizzes() -> None:
    snapshot = course_state_snapshot(CourseAgentState(), [_existing()])
    assert f"Lecture {LECTURE_ID}: 2 Question(s)" in snapshot
    assert "No Quizzes have been authored yet." in course_state_snapshot(CourseAgentState())


@pytest.mark.anyio
async def test_the_api_lists_quizzes_and_reads_one(tmp_path: Path) -> None:
    workspace, _plan = _workspace(tmp_path)
    write_quiz(workspace, _existing())
    transport = httpx2.ASGITransport(app=create_app(workspace))
    async with httpx2.AsyncClient(transport=transport, base_url="http://test") as client:
        listed = await client.get("/api/quizzes")
        read = await client.get(f"/api/quizzes/{LECTURE_ID}")
        missing = await client.get(f"/api/quizzes/{OTHER_LECTURE_ID}")
        malformed = await client.get("/api/quizzes/not-a-lecture")
        (workspace / "quizzes" / f"{OTHER_LECTURE_ID}.yaml").write_text("questions: 3\n")
        invalid = await client.get(f"/api/quizzes/{OTHER_LECTURE_ID}")

    assert listed.json() == [{"lecture_id": LECTURE_ID, "question_count": 2}]
    assert Quiz.model_validate(read.json()) == _existing()
    assert missing.status_code == 404
    assert malformed.status_code == 404
    assert invalid.status_code == 422
