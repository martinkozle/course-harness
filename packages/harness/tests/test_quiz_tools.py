"""The Course Agent writes Quizzes whose Question and Option IDs stay stable."""

import json
import re
from collections.abc import AsyncIterator, Mapping
from pathlib import Path
from typing import Any

import httpx2
import pytest
from pydantic import TypeAdapter, ValidationError
from pydantic_ai import DeferredToolRequests, DeferredToolResults
from pydantic_ai.messages import (
    ModelMessage,
    ModelRequest,
    ModelResponse,
    RetryPromptPart,
    TextPart,
    ToolCallPart,
    ToolReturnPart,
)
from pydantic_ai.models.function import AgentInfo, DeltaToolCall, FunctionModel

from course_harness.app import create_app
from course_harness.core.canonical_mutation import (
    CanonicalMutationConflict,
    capture_canonical_files,
)
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
    quiz_path,
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
    SPLIT_QUESTION_HINT,
    NewQuizOption,
    NewQuizQuestion,
    QuizOptionChange,
    QuizQuestionChange,
    QuizQuestionCommand,
    QuizQuestionCommands,
    build_quiz,
    quiz_notes,
)
from course_harness.workspace_history import (
    InvalidAgentMutationError,
    record_app_authored_state,
)

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


def _new_question(**changes: object) -> NewQuizQuestion:
    return NewQuizQuestion.model_validate(
        {
            "prompt": "Why randomize?",
            "options": [
                {"text": "To make groups exchangeable", "correct": True},
                {
                    "text": "To increase the sample",
                    "correct": False,
                    "explanation": "Randomizing adds no units.",
                },
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


def test_items_left_out_stay_unchanged_and_in_place() -> None:
    """Changing one Option of one Question touches nothing else."""
    existing = _existing()
    revision = build_quiz(
        LECTURE_ID,
        [
            QuizQuestionChange(
                id=Q1,
                options=[QuizOptionChange(id=EXCHANGEABILITY, text="Exchangeability (no bias)")],
            ),
        ],
        existing,
    )

    first, second = revision.quiz.questions
    assert second == existing.questions[1]
    assert first.prompt == existing.questions[0].prompt
    assert [option.id for option in first.options] == [POSITIVITY, EXCHANGEABILITY, CONSISTENCY]
    assert first.options[1].text == "Exchangeability (no bias)"
    assert first.options[1].explanation == existing.questions[0].options[1].explanation
    assert first.options[2] == existing.questions[0].options[2]
    assert revision.removed_question_ids == []
    assert revision.removed_option_ids == []


def test_new_items_go_at_the_end_unless_every_existing_item_is_listed() -> None:
    existing = _existing()
    appended = build_quiz(
        LECTURE_ID,
        [
            _new_question(),
            QuizQuestionChange(
                id=Q1,
                options=[
                    NewQuizOption(correct=False, text="Interference", explanation="Spillover.")
                ],
            ),
        ],
        existing,
    )
    first, second, new = appended.quiz.questions
    assert (first.id, second.id) == (Q1, Q2)
    assert new.prompt == "Why randomize?"
    assert [option.text for option in first.options][-1] == "Interference"

    reordered = build_quiz(
        LECTURE_ID,
        [
            QuizQuestionChange(id=Q2),
            _new_question(),
            QuizQuestionChange(
                id=Q1,
                options=[
                    QuizOptionChange(id=CONSISTENCY),
                    NewQuizOption(correct=False, text="Interference", explanation="Spillover."),
                    QuizOptionChange(id=POSITIVITY),
                    QuizOptionChange(id=EXCHANGEABILITY),
                ],
            ),
        ],
        existing,
    )
    second, new, first = reordered.quiz.questions
    assert (second.id, first.id) == (Q2, Q1)
    assert re.fullmatch(QUESTION_ID_PATTERN, new.id)
    assert [option.text for option in first.options] == [
        "Consistency",
        "Interference",
        "Positivity",
        "Exchangeability",
    ]
    assert re.fullmatch(OPTION_ID_PATTERN, first.options[1].id)


def test_listed_existing_items_swap_their_own_positions() -> None:
    three = _existing().model_copy(
        update={
            "questions": [
                *_existing().questions,
                QuizQuestion(
                    id="question-cccccccccccc",
                    prompt="Third?",
                    options=_existing().questions[1].options,
                ),
            ]
        }
    )
    revision = build_quiz(
        LECTURE_ID,
        [QuizQuestionChange(id="question-cccccccccccc"), QuizQuestionChange(id=Q1)],
        three,
    )
    assert [question.id for question in revision.quiz.questions] == [
        "question-cccccccccccc",
        Q2,
        Q1,
    ]


def test_removal_is_explicit() -> None:
    revision = build_quiz(
        LECTURE_ID,
        [QuizQuestionChange(id=Q1, remove_option_ids=[CONSISTENCY])],
        _existing(),
        remove_question_ids=[Q2],
    )

    (first,) = revision.quiz.questions
    assert [option.id for option in first.options] == [POSITIVITY, EXCHANGEABILITY]
    assert revision.removed_question_ids == [Q2]
    assert revision.removed_option_ids == [CONSISTENCY]


def test_replacing_all_questions_removes_those_left_out() -> None:
    revision = build_quiz(
        LECTURE_ID,
        [_new_question(), QuizQuestionChange(id=Q2)],
        _existing(),
        replace_all_questions=True,
    )
    assert [question.id for question in revision.quiz.questions][1] == Q2
    assert revision.removed_question_ids == [Q1]


def test_moving_the_answer_keeps_ids_and_needs_new_explanations() -> None:
    revision = build_quiz(
        LECTURE_ID,
        [
            QuizQuestionChange(
                id=Q1,
                options=[
                    QuizOptionChange(
                        id=POSITIVITY, correct=False, explanation="Positivity is about overlap."
                    ),
                    QuizOptionChange(id=EXCHANGEABILITY, correct=True, explanation=""),
                ],
            ),
        ],
        _existing(),
    )

    first = revision.quiz.questions[0]
    assert [(option.id, option.correct) for option in first.options] == [
        (POSITIVITY, False),
        (EXCHANGEABILITY, True),
        (CONSISTENCY, False),
    ]
    assert first.options[0].explanation == "Positivity is about overlap."
    assert first.options[1].explanation is None


@pytest.mark.parametrize(
    ("questions", "removals", "message"),
    [
        ([], [Q1, Q2], "at least one Question. Use delete_quiz"),
        (
            [QuizQuestionChange(id="question-ffffffffffff")],
            [],
            "question-ffffffffffff is not a Question of this Quiz",
        ),
        (
            [QuizQuestionChange(id=Q1), QuizQuestionChange(id=Q1)],
            [],
            f"Question {Q1} is given more than once",
        ),
        ([QuizQuestionChange(id=Q1)], [Q1], "either change it or remove it"),
        ([], ["question-ffffffffffff"], "remove_question_ids: question-ffffffffffff is not"),
        (
            [QuizQuestionChange(id=Q1, remove_option_ids=["option-dddddddddddd"])],
            [],
            "Question 1 remove_option_ids: option-dddddddddddd is not an Option of this Question",
        ),
        (
            [QuizQuestionChange(id=Q2, remove_option_ids=["option-eeeeeeeeeeee"])],
            [],
            "Question 1 would have 1 Options; it needs 2 to 10",
        ),
        (
            [
                QuizQuestionChange(
                    id=Q1, options=[QuizOptionChange(id="option-dddddddddddd", text="Moved")]
                )
            ],
            [],
            f"It belongs to Question {Q2}",
        ),
        (
            [
                QuizQuestionChange(
                    id=Q1,
                    options=[
                        QuizOptionChange(id=EXCHANGEABILITY, correct=True, explanation=""),
                    ],
                )
            ],
            [],
            f"Question 1 would have 2 correct Options ({POSITIVITY}, {EXCHANGEABILITY})",
        ),
        (
            [
                QuizQuestionChange(
                    id=Q1, options=[QuizOptionChange(id=EXCHANGEABILITY, correct=True)]
                )
            ],
            [],
            f"Question 1, Option 1 ({EXCHANGEABILITY}) becomes correct, so its Explanation no "
            'longer fits. Give it a new explanation, or "" to remove it.',
        ),
        (
            [QuizQuestionChange(id=Q1, remove_option_ids=[POSITIVITY])],
            [],
            "Question 1 would have 0 correct Options",
        ),
        (
            [_new_question(citations=[{**CITATION, "source_id": "x"}])],
            [],
            "Question 1 cites x, which is not a Source of this Course",
        ),
        (
            [
                _new_question(
                    options=[{"text": "Same", "correct": True}, {"text": "same", "correct": False}]
                )
            ],
            [],
            "two Options with the same text",
        ),
    ],
)
def test_invalid_commands_explain_how_to_fix_them(
    questions: list[QuizQuestionCommand], removals: list[str], message: str
) -> None:
    with pytest.raises(ValueError, match=re.escape(message)):
        build_quiz(
            LECTURE_ID,
            questions,
            _existing(),
            remove_question_ids=removals,
            source_ids={SOURCE_ID},
        )


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


def _deps(workspace: Path, tmp_path: Path, plan: CoursePlan, **changes: Any) -> CourseAgentDeps:
    return CourseAgentDeps(
        course_state=CourseAgentState(course=plan, sources=[_source()]),
        workspace=workspace,
        data_dir=tmp_path / "data",
        cache_dir=tmp_path / "cache",
        **changes,
    )


async def _run(
    workspace: Path,
    tmp_path: Path,
    plan: CoursePlan,
    calls: list[tuple[str, dict[str, object]]],
    **deps_changes: Any,
) -> list[str]:
    """Run the autonomous agent through ``calls``; return each tool result or retry prompt."""
    returns: list[str] = []

    async def stream(
        messages: list[ModelMessage], _info: AgentInfo
    ) -> AsyncIterator[str | dict[int, DeltaToolCall]]:
        done = [
            part
            for message in messages
            if isinstance(message, ModelRequest)
            for part in message.parts
            if isinstance(part, (ToolReturnPart, RetryPromptPart))
        ]
        if done:
            last = done[-1]
            returns.append(
                str(last.content) if isinstance(last, ToolReturnPart) else last.model_response()
            )
        if len(done) < len(calls):
            name, args = calls[len(done)]
            yield {0: DeltaToolCall(name=name, json_args=json.dumps(args), tool_call_id=name)}
            return
        yield "Done."

    agent = _build_course_agent(requires_approval=False)
    async with agent.run_stream(
        "Write a Quiz.",
        deps=_deps(workspace, tmp_path, plan, **deps_changes),
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


# What a local model sent: each Question's prompt and its options as two separate items.
SPLIT_QUESTIONS = [
    {"prompt": "Which MCP primitive is model-controlled?", "citations": [CITATION]},
    {
        "options": [
            {"text": "Tools", "correct": True},
            {"text": "Resources", "correct": False},
        ]
    },
    {"prompt": "Who picks a Prompt?", "citations": [CITATION]},
    {"options": [{"text": "The user", "correct": True}, {"text": "The model", "correct": False}]},
]


def test_a_question_split_into_prompt_and_options_is_rejected_with_a_hint() -> None:
    with pytest.raises(ValidationError) as error:
        TypeAdapter(QuizQuestionCommands).validate_python(SPLIT_QUESTIONS)

    assert "Question 2 has options but no prompt or id" in str(error.value)
    assert SPLIT_QUESTION_HINT in str(error.value)


@pytest.mark.parametrize(
    ("questions", "message"),
    [
        ([{"options": SPLIT_QUESTIONS[1]["options"]}], "0.new_question.prompt\n  Field required"),
        ([{"prompt": "New?"}], "0.new_question.options\n  Field required"),
        (
            [{"prompt": "New?", "options": [{"text": "Only one", "correct": True}]}],
            "at least 2 items",
        ),
        (
            [{"prompt": "New?", "options": [{"text": "Yes"}, {"text": "No"}]}],
            "options.0.correct\n  Field required",
        ),
        (
            [{"prompt": "New?", "options": [{"id": POSITIVITY, "text": "Yes", "correct": True}]}],
            "options.0.id\n  Extra inputs are not permitted",
        ),
        ([{"id": "not-a-question"}], "question_change.id\n  String should match pattern"),
    ],
)
def test_the_schema_says_what_a_new_question_needs(
    questions: list[dict[str, object]], message: str
) -> None:
    with pytest.raises(ValidationError, match=re.escape(message)):
        TypeAdapter(QuizQuestionCommands).validate_python(questions)


def test_a_null_id_marks_a_new_question_or_option() -> None:
    question = _new_question().model_dump()
    question["id"] = None
    question["options"][0]["id"] = None

    (command,) = TypeAdapter(QuizQuestionCommands).validate_python([question])

    assert isinstance(command, NewQuizQuestion)
    assert isinstance(command.options[0], NewQuizOption)


@pytest.mark.anyio
async def test_the_agent_is_told_to_join_a_split_question_before_anything_is_written(
    tmp_path: Path,
) -> None:
    workspace, plan = _workspace(tmp_path)

    (retry,) = await _run(
        workspace,
        tmp_path,
        plan,
        [("save_quiz", {"lecture_id": LECTURE_ID, "questions": SPLIT_QUESTIONS})],
    )

    assert SPLIT_QUESTION_HINT in retry
    assert read_quiz(workspace, LECTURE_ID) is None


@pytest.mark.anyio
async def test_the_tool_result_names_what_was_removed(tmp_path: Path) -> None:
    workspace, plan = _workspace(tmp_path)
    write_quiz(workspace, _existing())

    (result,) = await _run(
        workspace,
        tmp_path,
        plan,
        [
            (
                "save_quiz",
                {
                    "lecture_id": LECTURE_ID,
                    "questions": [{"id": Q1, "remove_option_ids": [CONSISTENCY]}],
                    "remove_question_ids": [Q2],
                },
            )
        ],
    )

    assert f"Removed Questions: {Q2}." in result
    assert f"Removed Options: {CONSISTENCY}." in result
    stored = read_quiz(workspace, LECTURE_ID)
    assert stored is not None and [q.id for q in stored.questions] == [Q1]


def _approving_model(calls: list[tuple[str, dict[str, object]]]) -> FunctionModel:
    def respond(messages: list[ModelMessage], _info: AgentInfo) -> ModelResponse:
        returned = sum(
            isinstance(part, ToolReturnPart)
            for message in messages
            if isinstance(message, ModelRequest)
            for part in message.parts
        )
        if returned < len(calls) and not any(
            isinstance(message, ModelResponse)
            and any(isinstance(part, ToolCallPart) for part in message.parts)
            for message in messages[-1:]
        ):
            name, args = calls[returned]
            return ModelResponse(parts=[ToolCallPart(name, json.dumps(args), f"call-{name}")])
        return ModelResponse(parts=[TextPart("Done.")])

    return FunctionModel(function=respond)


@pytest.mark.anyio
@pytest.mark.parametrize("tool", ["save_quiz", "delete_quiz"])
async def test_guided_mode_asks_before_changing_a_quiz(tmp_path: Path, tool: str) -> None:
    workspace, plan = _workspace(tmp_path)
    write_quiz(workspace, _existing())
    args: dict[str, object] = {"lecture_id": LECTURE_ID}
    if tool == "save_quiz":
        args["questions"] = [{"id": Q1, "prompt": "Which assumption is about overlap?"}]
    agent = _build_course_agent(requires_approval=True)
    model = _approving_model([(tool, args)])
    deps = _deps(workspace, tmp_path, plan)

    first = await agent.run(
        "Change the Quiz.", deps=deps, model=model, output_type=[str, DeferredToolRequests]
    )

    assert isinstance(first.output, DeferredToolRequests)
    (request,) = first.output.approvals
    assert request.tool_name == tool
    assert read_quiz(workspace, LECTURE_ID) == _existing()

    await agent.run(
        deps=deps,
        model=model,
        output_type=[str, DeferredToolRequests],
        message_history=first.all_messages(),
        deferred_tool_results=DeferredToolResults(approvals={request.tool_call_id: True}),
    )

    stored = read_quiz(workspace, LECTURE_ID)
    if tool == "save_quiz":
        assert stored is not None
        assert stored.questions[0].prompt == "Which assumption is about overlap?"
    else:
        assert stored is None


@pytest.mark.anyio
async def test_a_quiz_change_that_leaves_the_course_invalid_is_sent_back(tmp_path: Path) -> None:
    workspace, plan = _workspace(tmp_path)
    checked: list[Mapping[str, bytes | None]] = []

    def reject(written: Mapping[str, bytes | None]) -> None:
        checked.append(written)
        raise InvalidAgentMutationError(["Quiz cites a missing Source"], reverted=True)

    (result,) = await _run(
        workspace,
        tmp_path,
        plan,
        [("save_quiz", {"lecture_id": LECTURE_ID, "questions": [_new_question().model_dump()]})],
        after_mutation=reject,
    )

    assert list(checked[0]) == [quiz_path(LECTURE_ID)]
    assert "rejected and undone" in result
    assert "Quiz cites a missing Source" in result


@pytest.mark.anyio
async def test_a_quiz_changed_during_the_run_is_not_overwritten(tmp_path: Path) -> None:
    workspace, plan = _workspace(tmp_path)
    write_quiz(workspace, _existing())
    preconditions = capture_canonical_files(workspace, {quiz_path(LECTURE_ID)})
    edited = _existing().model_copy(
        update={"questions": [_existing().questions[0].model_copy(update={"prompt": "Edited?"})]}
    )
    write_quiz(workspace, edited)

    with pytest.raises(CanonicalMutationConflict):
        await _run(
            workspace,
            tmp_path,
            plan,
            [
                (
                    "save_quiz",
                    {"lecture_id": LECTURE_ID, "questions": [{"id": Q1, "prompt": "Mine?"}]},
                )
            ],
            canonical_preconditions=preconditions,
        )

    assert read_quiz(workspace, LECTURE_ID) == edited
