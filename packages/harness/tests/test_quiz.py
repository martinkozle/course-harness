import json
import subprocess
from collections.abc import Callable
from pathlib import Path

import httpx2
import pytest
from pydantic import ValidationError

from course_harness.app import create_app
from course_harness.core import releases
from course_harness.core.canonical_mutation import capture_canonical_file
from course_harness.core.course_plan import CoursePlan, Lecture, read_course_plan, write_course_plan
from course_harness.core.presentation import BulletsSlide, Presentation, SlideCitation
from course_harness.core.quiz import (
    InvalidQuiz,
    Quiz,
    QuizOption,
    QuizQuestion,
    correct_option,
    delete_quiz_file,
    list_quizzes,
    new_option_id,
    new_question_id,
    option_by_id,
    parse_quiz,
    question_by_id,
    quiz_path,
    quiz_problems,
    read_quiz,
    serialize_quiz,
    write_quiz,
)
from course_harness.core.release_validation import (
    ReleaseSelection,
    Waiver,
    validate_release,
)
from course_harness.core.releases import ReleaseError
from course_harness.core.sources import Source, SourcesIndex, write_sources_index
from course_harness.course_agent import (
    CoursePlanLectureCommand,
    ReplaceCoursePlanCommand,
    apply_course_plan_mutation,
)
from course_harness.release_service import (
    PublishReleaseRequest,
    ReleaseValidationError,
    publish_release,
    read_release,
)
from course_harness.workspace_history import (
    DriftAcceptRequest,
    RevisionCreateRequest,
    accept_workspace_drift,
    create_revision,
    list_revisions,
    read_current_state,
    record_app_authored_state,
)

LECTURE_ID = "lecture-aaaaaaaaaaaa"
SECOND_LECTURE_ID = "lecture-bbbbbbbbbbbb"
SOURCE_ID = "source-aaaaaaaaaaaa"
CITATION = SlideCitation(source_id=SOURCE_ID, label="Hernán and Robins, ch. 1")


def _plan() -> CoursePlan:
    return CoursePlan(
        id="course-aaaaaaaaaaaa",
        title="Causal inference",
        audience="Graduate students",
        lectures=[
            Lecture(id=LECTURE_ID, title="Foundations"),
            Lecture(id=SECOND_LECTURE_ID, title="Applications"),
        ],
    )


def _sources() -> SourcesIndex:
    return SourcesIndex(
        sources=[
            Source(
                id=SOURCE_ID,
                resource_id="resource-aaaaaaaaaaaa",
                source_version_id="a" * 64,
                label="Causal Inference: What If",
                admitted_at="2026-09-14T00:00:00+00:00",
            )
        ]
    )


def _question(
    question_id: str = "question-aaaaaaaaaaaa",
    *,
    citations: list[SlideCitation] | None = None,
    wrong_explanation: str | None = "Exchangeability is a different assumption.",
) -> QuizQuestion:
    return QuizQuestion(
        id=question_id,
        prompt="Which assumption says treated units could have been untreated?",
        options=[
            QuizOption(id="option-aaaaaaaaaaaa", text="Positivity", correct=True),
            QuizOption(
                id="option-bbbbbbbbbbbb", text="Exchangeability", explanation=wrong_explanation
            ),
        ],
        citations=[CITATION] if citations is None else citations,
    )


def _quiz(lecture_id: str = LECTURE_ID, *questions: QuizQuestion) -> Quiz:
    return Quiz(lecture_id=lecture_id, questions=list(questions) or [_question()])


def test_a_quiz_round_trips_through_its_human_readable_lecture_file(tmp_path: Path) -> None:
    quiz = _quiz()

    write_quiz(tmp_path, quiz)

    path = tmp_path / "quizzes" / f"{LECTURE_ID}.yaml"
    assert quiz_path(LECTURE_ID) == f"quizzes/{LECTURE_ID}.yaml"
    assert path.read_bytes() == serialize_quiz(quiz)
    text = path.read_text(encoding="utf-8")
    assert text.startswith(f"schema_version: 1\nlecture_id: {LECTURE_ID}\nquestions:\n")
    assert "prompt: Which assumption" in text
    assert read_quiz(tmp_path, LECTURE_ID) == quiz
    assert read_quiz(tmp_path, SECOND_LECTURE_ID) is None
    assert list_quizzes(tmp_path) == [quiz]
    assert quiz_problems(quiz) == []
    assert correct_option(quiz.questions[0]) == quiz.questions[0].options[0]
    question = question_by_id(quiz, "question-aaaaaaaaaaaa")
    assert question is not None
    assert option_by_id(question, "option-bbbbbbbbbbbb") == question.options[1]
    assert option_by_id(question, "option-ffffffffffff") is None


def test_conditional_quiz_writes_and_deletes_use_canonical_mutation(tmp_path: Path) -> None:
    quiz = _quiz()
    write_quiz(tmp_path, quiz, expected=capture_canonical_file(tmp_path, quiz_path(LECTURE_ID)))
    captured = capture_canonical_file(tmp_path, quiz_path(LECTURE_ID))
    assert captured.content == serialize_quiz(quiz)

    assert delete_quiz_file(tmp_path, LECTURE_ID, expected=captured) is True
    assert read_quiz(tmp_path, LECTURE_ID) is None
    assert delete_quiz_file(tmp_path, LECTURE_ID) is False


def test_generated_ids_follow_the_codebase_convention() -> None:
    question = QuizQuestion(
        id=new_question_id(),
        prompt="Pick one",
        options=[
            QuizOption(id=new_option_id(), text="Yes", correct=True),
            QuizOption(id=new_option_id(), text="No", explanation="Because."),
        ],
    )
    assert question.id.startswith("question-")
    assert all(option.id.startswith("option-") for option in question.options)


@pytest.mark.parametrize(
    "mutate",
    [
        lambda data: data.update(lecture_id="Foundations"),
        lambda data: data.update(questions=[]),
        lambda data: data.update(unexpected=True),
        lambda data: data["questions"][0].update(id="q1"),
        lambda data: data["questions"][0].update(prompt="  \n "),
        lambda data: data["questions"][0]["options"][1].update(explanation="   "),
        lambda data: data["questions"][0].update(options=data["questions"][0]["options"][:1]),
        lambda data: data["questions"][0]["options"][0].update(id="option-1"),
        lambda data: data["questions"][0]["options"][0].update(text=""),
        lambda data: data["questions"][0]["options"][1].update(explanation=""),
        lambda data: data["questions"][0]["citations"][0].update(source_id=""),
        lambda data: data["questions"][0]["citations"][0].update(page=3),
    ],
)
def test_malformed_quizzes_are_rejected_by_the_schema(mutate: Callable[[dict], None]) -> None:
    data = _quiz().model_dump(mode="json")
    mutate(data)
    with pytest.raises(ValidationError):
        Quiz.model_validate(data)


def test_unreadable_or_mismatched_quiz_files_are_invalid(tmp_path: Path) -> None:
    with pytest.raises(InvalidQuiz):
        parse_quiz(b"questions: [unclosed")
    # Canonical YAML never shares nodes, so anchors and aliases are rejected.
    aliased = serialize_quiz(_quiz()).replace(
        b"lecture_id: lecture-aaaaaaaaaaaa", b"lecture_id: &id lecture-aaaaaaaaaaaa\nx: *id"
    )
    with pytest.raises(InvalidQuiz, match="anchors or aliases"):
        parse_quiz(aliased)
    directory = tmp_path / "quizzes"
    directory.mkdir()
    (directory / f"{SECOND_LECTURE_ID}.yaml").write_bytes(serialize_quiz(_quiz()))
    with pytest.raises(InvalidQuiz, match="names Lecture"):
        read_quiz(tmp_path, SECOND_LECTURE_ID)
    assert list_quizzes(tmp_path) == []
    with pytest.raises(ValueError):
        quiz_path("../course")


def test_answer_key_problems_name_the_question_and_option() -> None:
    question = _question()
    no_correct = question.model_copy(
        update={
            "id": "question-bbbbbbbbbbbb",
            "options": [
                option.model_copy(update={"correct": False}) for option in question.options
            ],
        }
    )
    two_correct = question.model_copy(
        update={
            "id": "question-cccccccccccc",
            "options": [option.model_copy(update={"correct": True}) for option in question.options],
        }
    )
    duplicate_option = question.model_copy(
        update={
            "id": "question-dddddddddddd",
            "options": [
                question.options[0],
                question.options[0].model_copy(update={"correct": False}),
                question.options[0].model_copy(update={"correct": False}),
            ],
        }
    )
    # Each duplicated ID is reported once, however often it repeats.
    quiz = _quiz(
        LECTURE_ID, question, question, question, no_correct, two_correct, duplicate_option
    )

    assert [(p.code, p.question_id, p.option_id) for p in quiz_problems(quiz)] == [
        ("quiz.duplicate-question-id", "question-aaaaaaaaaaaa", None),
        ("quiz.correct-option-count", "question-bbbbbbbbbbbb", None),
        ("quiz.correct-option-count", "question-cccccccccccc", None),
        ("quiz.duplicate-option-id", "question-dddddddddddd", "option-aaaaaaaaaaaa"),
    ]
    assert correct_option(no_correct) is None
    assert correct_option(two_correct) is None


def _validate(*quizzes: Quiz, lecture_ids: list[str] | None = None, waivers=None):  # noqa: ANN202
    return validate_release(
        plan=_plan(),
        presentations=[],
        sources=_sources(),
        selection=ReleaseSelection(lecture_ids=lecture_ids or [LECTURE_ID, SECOND_LECTURE_ID]),
        quizzes=quizzes,
        waivers=waivers,
    )


def test_release_validation_blocks_broken_answer_keys_and_citations() -> None:
    question = _question()
    broken = question.model_copy(
        update={
            "id": "question-bbbbbbbbbbbb",
            "options": [
                option.model_copy(update={"correct": False}) for option in question.options
            ],
            "citations": [
                SlideCitation(source_id="source-ffffffffffff", label="Missing"),
                SlideCitation(source_id=SOURCE_ID, label="Backwards", line_start=5, line_end=2),
            ],
        }
    )
    result = _validate(
        _quiz(LECTURE_ID, question, broken),
        # The same Question ID in another Lecture's Quiz would make Answers ambiguous.
        _quiz(SECOND_LECTURE_ID, _question()),
    )

    assert result.structurally_valid is False
    assert result.can_publish is False
    assert [(finding.id, finding.severity) for finding in result.findings] == [
        ("quiz.correct-option-count:lecture-aaaaaaaaaaaa:question-bbbbbbbbbbbb", "error"),
        (
            "citation.missing-source:lecture-aaaaaaaaaaaa:question-bbbbbbbbbbbb:0"
            ":source-ffffffffffff",
            "error",
        ),
        ("citation.invalid-coordinates:lecture-aaaaaaaaaaaa:question-bbbbbbbbbbbb:1", "error"),
        (
            "quiz.unexplained-option:lecture-aaaaaaaaaaaa:question-bbbbbbbbbbbb"
            ":option-aaaaaaaaaaaa",
            "warning",
        ),
        (
            "quiz.duplicate-question-id:lecture-bbbbbbbbbbbb:question-aaaaaaaaaaaa"
            ":lecture-aaaaaaaaaaaa",
            "error",
        ),
    ]
    citation = result.findings[1].target
    assert citation.scope == "citation"
    assert citation.question_id == "question-bbbbbbbbbbbb"
    assert citation.option_id is None
    assert citation.citation_index == 0


def test_option_citations_are_checked_and_count_as_question_grounding() -> None:
    question = _question(citations=[])
    cited_option = question.options[1].model_copy(
        update={"citations": [SlideCitation(source_id="source-ffffffffffff", label="Gone")]}
    )
    question = question.model_copy(update={"options": [question.options[0], cited_option]})

    result = _validate(_quiz(LECTURE_ID, question))

    assert [finding.code for finding in result.findings] == ["citation.missing-source"]
    assert result.findings[0].target.option_id == "option-bbbbbbbbbbbb"
    assert result.findings[0].id.endswith(
        ":question-aaaaaaaaaaaa:option-bbbbbbbbbbbb:0:source-ffffffffffff"
    )


def test_release_validation_warns_conservatively_and_accepts_waivers() -> None:
    quiz = _quiz(LECTURE_ID, _question(citations=[], wrong_explanation=None))

    result = _validate(quiz)

    assert result.structurally_valid is True
    assert result.can_publish is False
    assert [(finding.id, finding.target.scope) for finding in result.findings] == [
        (
            "quiz.unexplained-option:lecture-aaaaaaaaaaaa:question-aaaaaaaaaaaa"
            ":option-bbbbbbbbbbbb",
            "option",
        ),
        ("grounding.uncited-question:lecture-aaaaaaaaaaaa:question-aaaaaaaaaaaa", "question"),
    ]
    waived = _validate(
        quiz,
        waivers=[
            Waiver(finding_id=finding.id, justification="Reviewed by the Course Author")
            for finding in result.findings
        ],
    )
    assert waived.can_publish is True


def test_release_validation_ignores_quizzes_of_unselected_lectures() -> None:
    broken = _quiz(SECOND_LECTURE_ID, _question(citations=[], wrong_explanation=None))

    result = _validate(_quiz(), broken, lecture_ids=[LECTURE_ID])

    assert result.findings == []
    assert result.can_publish is True


def test_findings_without_quiz_targets_keep_their_earlier_serialized_shape() -> None:
    result = validate_release(
        plan=_plan(),
        presentations=[],
        sources=SourcesIndex(),
        selection=ReleaseSelection(lecture_ids=["lecture-cccccccccccc"]),
    )
    target = result.findings[0].model_dump(mode="json")["target"]
    assert "question_id" not in target
    assert "option_id" not in target


def _initialize(workspace: Path) -> None:
    workspace.mkdir()
    subprocess.run(["git", "init", "--quiet", "--initial-branch=main", str(workspace)], check=True)
    write_course_plan(workspace, _plan())
    write_sources_index(workspace, _sources())


def test_quiz_files_are_part_of_revisions_drift_and_current_state(tmp_path: Path) -> None:
    workspace = tmp_path / "course"
    _initialize(workspace)
    write_quiz(workspace, _quiz())

    state = read_current_state(workspace)
    assert state.validation.valid is True
    assert f"quizzes/{LECTURE_ID}.yaml" in [change.path for change in state.changes]
    create_revision(workspace, RevisionCreateRequest(summary="Add a Quiz"))
    record_app_authored_state(workspace)
    committed = subprocess.run(
        ["git", "ls-tree", "-r", "--name-only", "HEAD"],
        cwd=workspace,
        check=True,
        capture_output=True,
        text=True,
    ).stdout.split()
    assert f"quizzes/{LECTURE_ID}.yaml" in committed
    assert read_current_state(workspace).drift == "clean"

    # An external edit to a Quiz is Workspace Drift that can be reviewed and accepted.
    write_quiz(
        workspace,
        _quiz(LECTURE_ID, _question().model_copy(update={"prompt": "Edited outside"})),
    )
    drift = read_current_state(workspace)
    assert drift.drift == "drift"
    assert [entry.path for entry in drift.drift_changes] == [f"quizzes/{LECTURE_ID}.yaml"]
    revision = accept_workspace_drift(
        workspace,
        DriftAcceptRequest(summary="Accept a Quiz edit", drift_id=drift.drift_id or ""),
    )
    assert revision.id in {item.id for item in list_revisions(workspace)}
    assert read_current_state(workspace).drift == "clean"


def test_current_state_reports_invalid_and_inconsistent_quizzes(tmp_path: Path) -> None:
    workspace = tmp_path / "course"
    _initialize(workspace)
    question = _question()
    write_quiz(
        workspace,
        _quiz(
            LECTURE_ID,
            question.model_copy(
                update={
                    "options": [
                        option.model_copy(update={"correct": True}) for option in question.options
                    ],
                    "citations": [SlideCitation(source_id="source-ffffffffffff", label="Gone")],
                }
            ),
        ),
    )
    write_quiz(workspace, _quiz(SECOND_LECTURE_ID))
    write_quiz(workspace, _quiz("lecture-cccccccccccc", _question("question-cccccccccccc")))
    (workspace / "quizzes" / "notes.yaml").write_text("questions: []\n", encoding="utf-8")
    (workspace / "quizzes" / "lecture-dddddddddddd.yaml").write_text("- [", encoding="utf-8")

    findings = read_current_state(workspace).validation.findings

    assert "quizzes/notes.yaml is not named after a Lecture ID" in findings
    assert any(item.startswith("quizzes/lecture-dddddddddddd.yaml is invalid") for item in findings)
    assert (
        f"Quiz for Lecture {LECTURE_ID}: Question question-aaaaaaaaaaaa has 2 correct Options; "
        "it needs exactly one."
    ) in findings
    assert (
        "Question question-aaaaaaaaaaaa Citation references missing Source source-ffffffffffff"
    ) in findings
    assert (
        "Question ID question-aaaaaaaaaaaa appears in the Quizzes of Lectures "
        f"{LECTURE_ID} and {SECOND_LECTURE_ID}"
    ) in findings
    assert "Quiz for Lecture lecture-cccccccccccc references a missing Lecture" in findings


def _publish(workspace: Path, tmp_path: Path, slug: str, waivers: list[Waiver] | None = None):  # noqa: ANN202
    return publish_release(
        workspace=workspace,
        release_data_root=tmp_path / "releases",
        templates_data_root=tmp_path / "templates",
        evidence_line_counts={},
        request=PublishReleaseRequest(
            slug=slug,
            name=slug.title(),
            selection=ReleaseSelection(lecture_ids=[LECTURE_ID, SECOND_LECTURE_ID]),
            waivers=waivers or [],
        ),
    )


def test_releases_include_quizzes_readable_from_tag_objects(tmp_path: Path) -> None:
    workspace = tmp_path / "course"
    _initialize(workspace)
    create_revision(workspace, RevisionCreateRequest(summary="Initial Course"))
    record_app_authored_state(workspace)
    without_quizzes = _publish(workspace, tmp_path, "draft")
    manifest = subprocess.run(
        ["git", "cat-file", "tag", "refs/tags/course-release/draft"],
        cwd=workspace,
        check=True,
        capture_output=True,
    ).stdout.split(b"\n\n", 1)[1]
    assert "quizzes" not in json.loads(manifest)
    assert without_quizzes.quizzes == []

    quiz = _quiz()
    write_quiz(workspace, quiz)
    record_app_authored_state(workspace)
    create_revision(workspace, RevisionCreateRequest(summary="Add a Quiz"))
    record_app_authored_state(workspace)
    release = _publish(workspace, tmp_path, "fall-2026")

    assert [(item.lecture_id, item.path) for item in release.quizzes] == [
        (LECTURE_ID, f"quizzes/{LECTURE_ID}.yaml")
    ]
    mirror = tmp_path / "mirror.git"
    subprocess.run(
        ["git", "clone", "--quiet", "--mirror", str(workspace), str(mirror)],
        check=True,
        capture_output=True,
    )
    assert releases.read_release(mirror, "fall-2026") == release
    assert releases.read_release(mirror, "draft") == without_quizzes
    assert releases.tagged_quiz(mirror, release.commit_oid, LECTURE_ID) == quiz
    with pytest.raises(ReleaseError):
        releases.tagged_quiz(mirror, release.commit_oid, SECOND_LECTURE_ID)
    with pytest.raises(ReleaseError):
        releases.tagged_quiz(mirror, release.commit_oid, "../course")

    # A manifest that hides a tagged Quiz no longer verifies.
    payload = release.model_dump(mode="json")
    del payload["quizzes"]
    forged = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    subprocess.run(
        ["git", "tag", "--delete", "course-release/fall-2026"],
        cwd=workspace,
        check=True,
        capture_output=True,
    )
    subprocess.run(
        [
            "git",
            "-c",
            "tag.gpgSign=false",
            "-c",
            "user.name=Course Harness tests",
            "-c",
            "user.email=course-harness-tests@example.invalid",
            "tag",
            "--annotate",
            "--cleanup=verbatim",
            "--file=-",
            "course-release/fall-2026",
            release.commit_oid,
        ],
        cwd=workspace,
        input=forged.encode() + b"\n",
        check=True,
    )
    with pytest.raises(ReleaseError, match="Quizzes do not match"):
        read_release(workspace, "fall-2026")


def test_publishing_a_quiz_with_warnings_requires_waivers(tmp_path: Path) -> None:
    workspace = tmp_path / "course"
    _initialize(workspace)
    write_quiz(workspace, _quiz(LECTURE_ID, _question(citations=[], wrong_explanation=None)))
    create_revision(workspace, RevisionCreateRequest(summary="Add a Quiz"))
    record_app_authored_state(workspace)

    with pytest.raises(ReleaseValidationError):
        _publish(workspace, tmp_path, "fall-2026")
    release = _publish(
        workspace,
        tmp_path,
        "fall-2026",
        waivers=[
            Waiver(
                finding_id=(
                    "quiz.unexplained-option:lecture-aaaaaaaaaaaa:question-aaaaaaaaaaaa"
                    ":option-bbbbbbbbbbbb"
                ),
                justification="The wrong Option is self-evident",
            ),
            Waiver(
                finding_id="grounding.uncited-question:lecture-aaaaaaaaaaaa:question-aaaaaaaaaaaa",
                justification="General knowledge",
            ),
        ],
    )
    assert read_release(workspace, "fall-2026") == release
    assert [finding.waived for finding in release.validation.findings] == [True, True]


def test_markdown_prompts_and_explanations_keep_their_leading_whitespace() -> None:
    prompt = "    x = 1  # an indented code block\n\nWhat does this print?\n"
    explanation = "    print(x)\n\nIt prints `1`."
    data = _quiz().model_dump(mode="json")
    data["questions"][0]["prompt"] = prompt
    data["questions"][0]["options"][1]["explanation"] = explanation
    data["questions"][0]["options"][1]["text"] = "  Exchangeability  "

    quiz = parse_quiz(serialize_quiz(Quiz.model_validate(data)))

    assert quiz.questions[0].prompt == prompt
    assert quiz.questions[0].options[1].explanation == explanation
    assert quiz.questions[0].options[1].text == "Exchangeability"


def test_repeated_ids_are_checked_once_so_finding_ids_stay_unique() -> None:
    question = _question(citations=[], wrong_explanation=None)
    repeated_option = question.model_copy(
        update={
            "id": "question-bbbbbbbbbbbb",
            "options": [question.options[0], question.options[1], question.options[1]],
        }
    )

    result = _validate(_quiz(LECTURE_ID, question, question, question, repeated_option))

    finding_ids = [finding.id for finding in result.findings]
    assert len(finding_ids) == len(set(finding_ids))
    assert [finding.code for finding in result.findings] == [
        "quiz.duplicate-question-id",
        "quiz.duplicate-option-id",
        "quiz.unexplained-option",
        "grounding.uncited-question",
        "quiz.unexplained-option",
        "grounding.uncited-question",
    ]


def test_slide_and_quiz_citations_share_one_rule_set() -> None:
    citation = SlideCitation(source_id="source-ffffffffffff", label="Gone")
    lecture = (
        _plan().lectures[0].model_copy(update={"presentation_id": "presentation-aaaaaaaaaaaa"})
    )
    result = validate_release(
        plan=_plan().model_copy(update={"lectures": [lecture]}),
        presentations=[
            Presentation(
                id="presentation-aaaaaaaaaaaa",
                lecture_id=LECTURE_ID,
                slides=[BulletsSlide(id="slide-aaaaaaaaaaaa", bullets=["A"], citations=[citation])],
            )
        ],
        sources=_sources(),
        selection=ReleaseSelection(
            lecture_ids=[LECTURE_ID], artifact_ids=["presentation-aaaaaaaaaaaa"]
        ),
        quizzes=[_quiz(LECTURE_ID, _question(citations=[citation]))],
    )

    assert [(finding.id, finding.message) for finding in result.findings] == [
        (
            "citation.missing-source:presentation-aaaaaaaaaaaa:slide-aaaaaaaaaaaa:0"
            ":source-ffffffffffff",
            "Citation 1 on Slide slide-aaaaaaaaaaaa references missing Source source-ffffffffffff.",
        ),
        (
            "citation.missing-source:lecture-aaaaaaaaaaaa:question-aaaaaaaaaaaa:0"
            ":source-ffffffffffff",
            "Citation 1 on Question question-aaaaaaaaaaaa references missing Source "
            "source-ffffffffffff.",
        ),
    ]


def test_dropping_a_lecture_from_the_course_plan_removes_its_quiz(tmp_path: Path) -> None:
    workspace = tmp_path / "course"
    _initialize(workspace)
    write_quiz(workspace, _quiz())
    write_quiz(workspace, _quiz(SECOND_LECTURE_ID, _question("question-bbbbbbbbbbbb")))

    plan, written = apply_course_plan_mutation(
        workspace,
        ReplaceCoursePlanCommand(
            title="Causal inference",
            audience="Graduate students",
            lectures=[CoursePlanLectureCommand(id=SECOND_LECTURE_ID, title="Applications")],
        ),
    )

    assert [lecture.id for lecture in plan.lectures] == [SECOND_LECTURE_ID]
    assert set(written) == {"course.yaml", quiz_path(LECTURE_ID)}
    assert written[quiz_path(LECTURE_ID)] is None
    assert read_quiz(workspace, LECTURE_ID) is None
    assert read_quiz(workspace, SECOND_LECTURE_ID) is not None
    assert read_current_state(workspace).validation.valid is True


@pytest.mark.anyio
async def test_removing_a_lecture_through_the_api_removes_its_quiz(tmp_path: Path) -> None:
    workspace = tmp_path / "course"
    workspace.mkdir()
    transport = httpx2.ASGITransport(app=create_app(workspace))
    async with httpx2.AsyncClient(transport=transport, base_url="http://test") as client:
        created = await client.post(
            "/api/course",
            json={
                "title": "Field Methods",
                "audience": "New researchers",
                "lectures": [{"title": "Questions"}, {"title": "Sampling"}],
            },
        )
        first_id, second_id = [lecture["id"] for lecture in created.json()["lectures"]]
        write_quiz(workspace, _quiz(second_id, _question(citations=[])))
        record_app_authored_state(workspace)

        removed = await client.delete(f"/api/course/lectures/{second_id}")

    assert removed.status_code == 200
    assert read_quiz(workspace, second_id) is None
    plan = read_course_plan(workspace)
    assert plan is not None
    assert [lecture.id for lecture in plan.lectures] == [first_id]
    state = read_current_state(workspace)
    assert state.validation.valid is True
    assert state.drift == "clean"
