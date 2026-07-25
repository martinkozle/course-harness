from __future__ import annotations

from io import BytesIO
from pathlib import Path

import httpx2
import pytest
from pptx import Presentation as PPTXPresentation

from course_harness.app import create_app
from course_harness.course_plan import (
    CoursePlanInput,
    LectureInput,
    create_course_plan,
    create_course_plan_file,
    initialize_workspace_history,
)


def _build_presentation(workspace: Path, lecture_id: str, slides: list[dict]):
    from course_harness.presentation import SLIDE_CLASSES_BY_LAYOUT, write_presentation
    from course_harness.presentation import Presentation as PresModel

    hydrated = []
    for s in slides:
        layout = s.get("layout", "title")
        cls = SLIDE_CLASSES_BY_LAYOUT[layout]
        hydrated.append(cls(**s))
    pres = PresModel(
        id="presentation-abc123def456",
        lecture_id=lecture_id,
        slides=hydrated,
    )
    write_presentation(workspace, pres)
    return pres


@pytest.mark.anyio
async def test_export_title_slide(tmp_path: Path) -> None:
    workspace = tmp_path / "export-course"
    workspace.mkdir()
    plan = create_course_plan(
        CoursePlanInput(
            title="Export Test",
            audience="Test",
            lectures=[LectureInput(title="L1")],
        )
    )
    initialize_workspace_history(workspace)
    create_course_plan_file(workspace, plan)
    _build_presentation(
        workspace,
        plan.lectures[0].id,
        [{"layout": "title", "id": "slide-000000000001", "title": "Hello", "subtitle": "World"}],
    )

    transport = httpx2.ASGITransport(app=create_app(workspace))
    async with httpx2.AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.get(f"/api/presentations/{plan.lectures[0].id}/export")

    assert response.status_code == 200
    assert response.headers["content-type"] == (
        "application/vnd.openxmlformats-officedocument.presentationml.presentation"
    )

    pptx = PPTXPresentation(BytesIO(response.content))
    assert len(pptx.slides) == 1
    slide = pptx.slides[0]
    assert slide.slide_layout.name is not None
    text_runs = [
        run.text
        for shape in slide.shapes
        if shape.has_text_frame
        for paragraph in shape.text_frame.paragraphs
        for run in paragraph.runs
    ]
    assert "Hello" in text_runs
    assert "World" in text_runs


@pytest.mark.anyio
async def test_export_all_layouts(tmp_path: Path) -> None:
    workspace = tmp_path / "export-all"
    workspace.mkdir()
    plan = create_course_plan(
        CoursePlanInput(
            title="All Layouts",
            audience="Test",
            lectures=[LectureInput(title="L1")],
        )
    )
    initialize_workspace_history(workspace)
    create_course_plan_file(workspace, plan)
    lid = plan.lectures[0].id
    all_slides = [
        {
            "layout": "title",
            "id": "slide-00000000000a",
            "title": "Title Slide",
            "subtitle": "Subtitle",
        },
        {"layout": "section", "id": "slide-00000000000b", "title": "Section Header"},
        {
            "layout": "bullets",
            "id": "slide-00000000000c",
            "title": "Bullets",
            "bullets": ["One", "Two", "Three"],
        },
        {
            "layout": "two_column",
            "id": "slide-00000000000d",
            "title": "Two Column",
            "left_content": "Left side",
            "right_content": "Right side",
        },
        {
            "layout": "big_statement",
            "id": "slide-00000000000e",
            "title": "Minor header",
            "statement": "BIG STATEMENT",
        },
        {"layout": "closing", "id": "slide-00000000000f", "title": "Closing", "text": "Thanks"},
        {
            "layout": "code",
            "id": "slide-000000000010",
            "title": "Code",
            "code": "def hello():\n    pass",
            "language": "python",
        },
        {
            "layout": "quote",
            "id": "slide-000000000011",
            "title": "Quote",
            "quote": "To be or not",
            "attribution": "Bard",
        },
    ]
    _build_presentation(workspace, lid, all_slides)

    transport = httpx2.ASGITransport(app=create_app(workspace))
    async with httpx2.AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.get(f"/api/presentations/{lid}/export")

    assert response.status_code == 200
    pptx = PPTXPresentation(BytesIO(response.content))
    assert len(pptx.slides) == 8
    text_runs = [
        run.text
        for slide in pptx.slides
        for shape in slide.shapes
        if shape.has_text_frame
        for paragraph in shape.text_frame.paragraphs
        for run in paragraph.runs
    ]
    combined = " ".join(text_runs)
    assert "Title Slide" in combined
    assert "BIG STATEMENT" in combined
    assert "Left side" in combined
    assert "To be or not" in combined
    assert "Bard" in combined
    assert "def hello():" in combined


@pytest.mark.anyio
async def test_export_citations_and_notes(tmp_path: Path) -> None:
    workspace = tmp_path / "export-cite"
    workspace.mkdir()
    plan = create_course_plan(
        CoursePlanInput(
            title="Citations Test",
            audience="Test",
            lectures=[LectureInput(title="L1")],
        )
    )
    initialize_workspace_history(workspace)
    create_course_plan_file(workspace, plan)
    _build_presentation(
        workspace,
        plan.lectures[0].id,
        [
            {
                "layout": "title",
                "id": "slide-000000000001",
                "title": "Slide 1",
                "speaker_notes": "Speaker note content here.",
                "citations": [
                    {
                        "source_id": "src-aaaa1111bbbb",
                        "label": "Paper A",
                        "url": "https://example.com/a",
                    },
                    {
                        "source_id": "src-cccc2222dddd",
                        "label": "Paper B",
                        "line_start": 10,
                        "line_end": 20,
                    },
                ],
            },
        ],
    )

    transport = httpx2.ASGITransport(app=create_app(workspace))
    async with httpx2.AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.get(f"/api/presentations/{plan.lectures[0].id}/export")

    assert response.status_code == 200
    pptx = PPTXPresentation(BytesIO(response.content))
    slide = pptx.slides[0]
    has_notes = False
    try:
        notes = slide.notes_slide
        notes_text = notes.notes_text_frame.text
        has_notes = True
    except Exception:
        notes_text = ""
    if has_notes:
        assert "Speaker note content here" in notes_text
        assert "Paper A" in notes_text
        assert "lines 11" in notes_text
    shape_texts = []
    for shape in slide.shapes:
        if shape.has_text_frame:
            for paragraph in shape.text_frame.paragraphs:
                for run in paragraph.runs:
                    shape_texts.append(run.text)
    all_text = " ".join(shape_texts)
    assert "Paper A" in all_text


@pytest.mark.anyio
async def test_export_slide_order(tmp_path: Path) -> None:
    workspace = tmp_path / "export-order"
    workspace.mkdir()
    plan = create_course_plan(
        CoursePlanInput(
            title="Order",
            audience="Test",
            lectures=[LectureInput(title="L1")],
        )
    )
    initialize_workspace_history(workspace)
    create_course_plan_file(workspace, plan)
    _build_presentation(
        workspace,
        plan.lectures[0].id,
        [
            {"layout": "title", "id": "slide-000000000001", "title": "First"},
            {"layout": "title", "id": "slide-000000000002", "title": "Second"},
            {"layout": "title", "id": "slide-000000000003", "title": "Third"},
        ],
    )

    transport = httpx2.ASGITransport(app=create_app(workspace))
    async with httpx2.AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.get(f"/api/presentations/{plan.lectures[0].id}/export")

    assert response.status_code == 200
    pptx = PPTXPresentation(BytesIO(response.content))
    slide_titles = []
    for s in pptx.slides:
        for shape in s.shapes:
            if shape.has_text_frame:
                txt = shape.text_frame.text.strip()
                if txt:
                    slide_titles.append(txt)
    assert slide_titles == ["First", "Second", "Third"]


@pytest.mark.anyio
async def test_export_skips_archived(tmp_path: Path) -> None:
    workspace = tmp_path / "export-archived"
    workspace.mkdir()
    plan = create_course_plan(
        CoursePlanInput(
            title="Archived",
            audience="Test",
            lectures=[LectureInput(title="L1")],
        )
    )
    initialize_workspace_history(workspace)
    create_course_plan_file(workspace, plan)
    _build_presentation(
        workspace,
        plan.lectures[0].id,
        [
            {"layout": "title", "id": "slide-000000000001", "title": "Active"},
            {"layout": "title", "id": "slide-000000000002", "title": "Hidden", "archived": True},
        ],
    )

    transport = httpx2.ASGITransport(app=create_app(workspace))
    async with httpx2.AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.get(f"/api/presentations/{plan.lectures[0].id}/export")

    assert response.status_code == 200
    pptx = PPTXPresentation(BytesIO(response.content))
    assert len(pptx.slides) == 1
    titles = []
    for s in pptx.slides:
        for shape in s.shapes:
            if shape.has_text_frame:
                txt = shape.text_frame.text.strip()
                if txt:
                    titles.append(txt)
    assert titles == ["Active"]


@pytest.mark.anyio
async def test_export_no_presentation_returns_404(tmp_path: Path) -> None:
    workspace = tmp_path / "export-404"
    workspace.mkdir()
    plan = create_course_plan(
        CoursePlanInput(
            title="No Pres",
            audience="Test",
            lectures=[LectureInput(title="L1")],
        )
    )
    initialize_workspace_history(workspace)
    create_course_plan_file(workspace, plan)

    transport = httpx2.ASGITransport(app=create_app(workspace))
    async with httpx2.AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.get(f"/api/presentations/{plan.lectures[0].id}/export")

    assert response.status_code == 404


@pytest.mark.anyio
async def test_export_empty_presentation(tmp_path: Path) -> None:
    workspace = tmp_path / "export-empty"
    workspace.mkdir()
    plan = create_course_plan(
        CoursePlanInput(
            title="Empty",
            audience="Test",
            lectures=[LectureInput(title="L1")],
        )
    )
    initialize_workspace_history(workspace)
    create_course_plan_file(workspace, plan)
    _build_presentation(workspace, plan.lectures[0].id, [])

    transport = httpx2.ASGITransport(app=create_app(workspace))
    async with httpx2.AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.get(f"/api/presentations/{plan.lectures[0].id}/export")

    assert response.status_code == 200
    pptx = PPTXPresentation(BytesIO(response.content))
    assert len(pptx.slides) == 0
