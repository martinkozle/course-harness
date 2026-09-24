"""Course Agent Slide tools change only what they are asked to change."""

import json
from collections.abc import AsyncIterator
from io import BytesIO
from pathlib import Path

import pytest
from PIL import Image
from pydantic_ai.messages import ModelMessage, ModelRequest, ToolReturnPart
from pydantic_ai.models.function import AgentInfo, DeltaToolCall, FunctionModel

from course_harness import resources as res
from course_harness.course_agent import (
    CourseAgentDeps,
    CourseAgentState,
    ReplacePresentationCommand,
    SlideCommand,
    _build_course_agent,
    apply_presentation_command,
)
from course_harness.course_plan import (
    CoursePlanInput,
    LectureInput,
    create_course_plan,
    create_course_plan_file,
    initialize_workspace_history,
)
from course_harness.library import register_and_snapshot
from course_harness.presentation import (
    BulletsSlide,
    ImageSlide,
    Presentation,
    TitleSlide,
    TwoColumnSlide,
    list_presentations,
    read_presentation_for_lecture,
    write_presentation,
)
from course_harness.sources import admit_source, read_sources_index
from course_harness.workspace_history import record_app_authored_state

TITLE_ID = "slide-000000000001"
BULLETS_ID = "slide-000000000002"


def _png() -> bytes:
    buffer = BytesIO()
    Image.new("RGB", (8, 4), (15, 110, 116)).save(buffer, format="PNG")
    return buffer.getvalue()


def _authored_course(tmp_path: Path):
    workspace = tmp_path / "course"
    workspace.mkdir()
    plan = create_course_plan(
        CoursePlanInput(title="MCP", audience="Engineers", lectures=[LectureInput(title="Intro")])
    )
    plan = plan.model_copy(
        update={
            "lectures": [
                plan.lectures[0].model_copy(update={"presentation_id": "presentation-000000000001"})
            ]
        }
    )
    initialize_workspace_history(workspace)
    create_course_plan_file(workspace, plan)
    write_presentation(
        workspace,
        Presentation(
            id="presentation-000000000001",
            lecture_id=plan.lectures[0].id,
            slides=[
                TitleSlide(id=TITLE_ID, title="Intro to MCP", subtitle="Why it exists"),
                BulletsSlide(
                    id=BULLETS_ID,
                    title="The problem",
                    bullets=["Every tool needs a custom integration"],
                    speaker_notes="Start with the pain.",
                ),
            ],
        ),
    )
    record_app_authored_state(workspace)
    return workspace, plan


def _image_source(tmp_path: Path, workspace: Path) -> str:
    data_dir, cache_dir = tmp_path / "data", tmp_path / "cache"
    resource = register_and_snapshot(
        data_dir,
        cache_dir,
        res.ResourceRegistrationRequest(kind="upload", location="mcp.png", media_type="image/png"),
        _png(),
    )[0]
    return admit_source(workspace, data_dir, cache_dir, resource.id).id


def test_revising_with_only_slide_ids_keeps_their_content(tmp_path: Path) -> None:
    """The model sent existing Slides as bare ``{layout, id}`` and every deck was blanked."""
    workspace, plan = _authored_course(tmp_path)
    command = ReplacePresentationCommand(
        lecture_id=plan.lectures[0].id,
        slides=[
            SlideCommand(id=TITLE_ID, layout="title"),
            SlideCommand(layout="section", title="New section"),
            SlideCommand(id=BULLETS_ID, layout="bullets"),
        ],
    )

    presentation = apply_presentation_command(workspace, command, plan)

    title, section, bullets = presentation.slides
    assert isinstance(title, TitleSlide)
    assert (title.title, title.subtitle) == ("Intro to MCP", "Why it exists")
    assert section.title == "New section"
    assert isinstance(bullets, BulletsSlide)
    assert bullets.bullets == ["Every tool needs a custom integration"]
    assert bullets.speaker_notes == "Start with the pain."


def test_slides_left_out_of_a_revision_are_archived_not_deleted(tmp_path: Path) -> None:
    workspace, plan = _authored_course(tmp_path)
    command = ReplacePresentationCommand(
        lecture_id=plan.lectures[0].id,
        slides=[SlideCommand(id=TITLE_ID, layout="title")],
    )

    presentation = apply_presentation_command(workspace, command, plan)

    assert [(slide.id, slide.archived) for slide in presentation.slides] == [
        (TITLE_ID, False),
        (BULLETS_ID, True),
    ]
    assert presentation.slides[1].title == "The problem"


def test_fields_a_layout_does_not_store_are_rejected_with_the_accepted_fields(
    tmp_path: Path,
) -> None:
    """The model put a long note in ``attribution``, which image Slides silently dropped."""
    workspace, plan = _authored_course(tmp_path)
    command = ReplacePresentationCommand(
        lecture_id=plan.lectures[0].id,
        slides=[
            SlideCommand(id=TITLE_ID, layout="title"),
            SlideCommand(layout="image", caption="MCP", attribution="Provided by the author"),
        ],
    )

    with pytest.raises(ValueError, match="image Slides do not store attribution") as raised:
        apply_presentation_command(workspace, command, plan)

    assert "image_source_id, caption" in str(raised.value)
    stored = read_presentation_for_lecture(workspace, plan.lectures[0].id)
    assert stored is not None and len(stored.slides) == 2


def _scripted(calls: list[tuple[str, dict[str, object]]], returns: list[str]) -> FunctionModel:
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

    return FunctionModel(stream_function=stream)


async def _run(
    workspace: Path, tmp_path: Path, calls: list[tuple[str, dict[str, object]]]
) -> list[str]:
    returns: list[str] = []
    sources = read_sources_index(workspace)
    agent = _build_course_agent(requires_approval=False)
    async with agent.run_stream(
        "Add this image to the lecture.",
        deps=CourseAgentDeps(
            course_state=CourseAgentState(
                sources=sources.sources if sources else [],
                presentations=list_presentations(workspace),
            ),
            workspace=workspace,
            data_dir=tmp_path / "data",
            cache_dir=tmp_path / "cache",
        ),
        model=_scripted(calls, returns),
    ) as streamed:
        await streamed.get_output()
    return returns


@pytest.mark.anyio
async def test_agent_inserts_an_image_slide_after_the_title_without_touching_others(
    tmp_path: Path,
) -> None:
    workspace, plan = _authored_course(tmp_path)
    source_id = _image_source(tmp_path, workspace)

    returns = await _run(
        workspace,
        tmp_path,
        [
            (
                "insert_slides",
                {
                    "lecture_id": plan.lectures[0].id,
                    "after_slide_id": TITLE_ID,
                    "slides": [
                        {
                            "layout": "image",
                            "title": "MCP at a glance",
                            "image_source_id": source_id,
                            "caption": "How MCP connects hosts to tools",
                        }
                    ],
                },
            )
        ],
    )

    stored = read_presentation_for_lecture(workspace, plan.lectures[0].id)
    assert stored is not None
    title, image, bullets = stored.slides
    assert (title.id, bullets.id) == (TITLE_ID, BULLETS_ID)
    assert isinstance(bullets, BulletsSlide) and bullets.bullets
    assert isinstance(image, ImageSlide)
    assert (image.title, image.image_source_id) == ("MCP at a glance", source_id)
    assert '"position": 2' in returns[0]
    assert source_id in returns[0]


@pytest.mark.anyio
async def test_agent_updates_one_field_of_a_slide_and_sees_the_saved_result(
    tmp_path: Path,
) -> None:
    workspace, plan = _authored_course(tmp_path)

    returns = await _run(
        workspace,
        tmp_path,
        [
            ("update_slide", {"slide_id": BULLETS_ID, "changes": {"title": "The N×M problem"}}),
            ("update_slide", {"slide_id": BULLETS_ID, "changes": {"speaker_notes": ""}}),
            ("update_slide", {"slide_id": TITLE_ID, "changes": {"caption": "Not a title field"}}),
        ],
    )

    stored = read_presentation_for_lecture(workspace, plan.lectures[0].id)
    assert stored is not None
    bullets = stored.slides[1]
    assert isinstance(bullets, BulletsSlide)
    assert bullets.title == "The N×M problem"
    assert bullets.bullets == ["Every tool needs a custom integration"]
    assert bullets.speaker_notes is None
    assert '"title": "The N\\u00d7M problem"' in returns[0] or "The N×M problem" in returns[0]
    assert "title Slides do not store caption" in returns[2]


@pytest.mark.anyio
async def test_null_fields_keep_content_and_are_ignored_where_a_layout_does_not_store_them(
    tmp_path: Path,
) -> None:
    """The model sent every other layout's fields as null, retried a rejected update over
    and over, and erased a subtitle it meant to leave alone."""
    workspace, plan = _authored_course(tmp_path)
    changes = {
        "purpose": "Open the lecture.",
        "subtitle": None,
        "caption": None,
        "quote": "",
        "attribution": None,
        "bullets": [],
    }

    returns = await _run(
        workspace, tmp_path, [("update_slide", {"slide_id": TITLE_ID, "changes": changes})]
    )

    stored = read_presentation_for_lecture(workspace, plan.lectures[0].id)
    assert stored is not None
    title = stored.slides[0]
    assert isinstance(title, TitleSlide)
    assert (title.purpose, title.subtitle) == ("Open the lecture.", "Why it exists")
    assert returns[0].startswith("Saved Slide")


@pytest.mark.anyio
async def test_empty_values_clear_slide_fields(tmp_path: Path) -> None:
    workspace, plan = _authored_course(tmp_path)

    await _run(
        workspace,
        tmp_path,
        [("update_slide", {"slide_id": BULLETS_ID, "changes": {"title": "", "bullets": []}})],
    )

    stored = read_presentation_for_lecture(workspace, plan.lectures[0].id)
    assert stored is not None
    bullets = stored.slides[1]
    assert isinstance(bullets, BulletsSlide)
    assert (bullets.title, bullets.bullets) == (None, [])
    assert bullets.speaker_notes == "Start with the pain."


def test_slide_tool_schema_names_the_layouts_that_store_each_field() -> None:
    schema = SlideCommand.model_json_schema()["properties"]

    assert "Only code Slides store this" in schema["language"]["description"]
    assert "Only image, quote" not in schema["caption"]["description"]
    assert "Only image Slides store this" in schema["caption"]["description"]
    assert "description" not in schema["speaker_notes"]


def test_agent_list_items_are_stored_without_typed_bullets(tmp_path: Path) -> None:
    workspace, plan = _authored_course(tmp_path)
    command = ReplacePresentationCommand(
        lecture_id=plan.lectures[0].id,
        slides=[
            SlideCommand(id=BULLETS_ID, layout="bullets", bullets=["• Foo", "- Bar"]),
            SlideCommand.model_validate(
                {
                    "layout": "two_column",
                    "left_content": ["Before", "* Slow"],
                    "right_content": "After",
                }
            ),
        ],
    )

    presentation = apply_presentation_command(workspace, command, plan)

    bullets, columns = presentation.slides[:2]
    assert isinstance(bullets, BulletsSlide)
    assert isinstance(columns, TwoColumnSlide)
    assert bullets.bullets == ["Foo", "Bar"]
    assert columns.left_content == ["Before", "Slow"]
    assert columns.right_content == ["After"]
    schema = SlideCommand.model_json_schema()["properties"]
    assert "do not start an item with" in schema["bullets"]["description"]
    assert "first item is that heading" in schema["left_content"]["description"]


@pytest.mark.anyio
async def test_slide_tools_report_the_layout_check(tmp_path: Path) -> None:
    workspace, plan = _authored_course(tmp_path)
    crammed = [
        "Every host application needs its own custom integration for every tool it wants "
        "to call, which multiplies the maintenance work"
    ] * 12

    returns = await _run(
        workspace,
        tmp_path,
        [
            ("update_slide", {"slide_id": BULLETS_ID, "changes": {"bullets": crammed}}),
            ("update_slide", {"slide_id": BULLETS_ID, "changes": {"bullets": ["Short"]}}),
            ("lint_slides", {"lecture_id": plan.lectures[0].id}),
        ],
    )

    assert f"- {BULLETS_ID} bullets ~" in returns[0]
    assert "even at min size" in returns[0]
    assert "Layout check: every Slide fits its template." in returns[1]
    assert returns[2] == "Layout check: every Slide fits its template."
