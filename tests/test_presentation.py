from pathlib import Path

import httpx2
import pytest

from course_harness.app import create_app
from course_harness.course_plan import (
    CoursePlanInput,
    LectureInput,
    create_course_plan,
    create_course_plan_file,
    initialize_workspace_history,
)
from course_harness.presentation import (
    Presentation,
    read_presentation_for_lecture,
)


@pytest.mark.anyio
async def test_create_and_read_presentation(tmp_path: Path) -> None:
    workspace = tmp_path / "test-course"
    workspace.mkdir()
    plan = create_course_plan(
        CoursePlanInput(
            title="Test Course",
            audience="Test audience",
            lectures=[LectureInput(title="Lecture 1"), LectureInput(title="Lecture 2")],
        )
    )
    initialize_workspace_history(workspace)
    create_course_plan_file(workspace, plan)

    transport = httpx2.ASGITransport(app=create_app(workspace))
    async with httpx2.AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.post(
            f"/api/presentations/{plan.lectures[0].id}",
            json={
                "slides": [
                    {"layout": "title", "title": "Welcome", "subtitle": "A subtitle"},
                    {
                        "layout": "bullets",
                        "title": "Key points",
                        "bullets": ["Point 1", "Point 2"],
                        "speaker_notes": "Mention the example",
                        "citations": [
                            {
                                "source_id": "source-abc123def456",
                                "label": "Chapter 1",
                                "line_start": 10,
                                "line_end": 15,
                            }
                        ],
                    },
                ]
            },
        )

        assert response.status_code == 201
        pres = Presentation.model_validate(response.json())
        assert pres.lecture_id == plan.lectures[0].id
        assert len(pres.slides) == 2
        assert pres.slides[0].layout == "title"
        assert pres.slides[0].title == "Welcome"
        assert pres.slides[0].subtitle == "A subtitle"
        assert pres.slides[1].layout == "bullets"
        assert pres.slides[1].bullets == ["Point 1", "Point 2"]
        assert pres.slides[1].speaker_notes == "Mention the example"
        assert len(pres.slides[1].citations) == 1
        assert pres.slides[1].citations[0].source_id == "source-abc123def456"

        get_response = await client.get(f"/api/presentations/{plan.lectures[0].id}")
        assert get_response.status_code == 200
        assert get_response.json()["id"] == pres.id

        list_response = await client.get("/api/presentations")
        assert list_response.status_code == 200
        summaries = list_response.json()
        assert len(summaries) == 1
        assert summaries[0]["lecture_id"] == plan.lectures[0].id
        assert summaries[0]["slide_count"] == 2


@pytest.mark.anyio
async def test_presentation_nonexistent_lecture(tmp_path: Path) -> None:
    workspace = tmp_path / "test-course"
    workspace.mkdir()
    plan = create_course_plan(
        CoursePlanInput(
            title="Test Course",
            audience="Test",
            lectures=[LectureInput(title="Lecture 1")],
        )
    )
    initialize_workspace_history(workspace)
    create_course_plan_file(workspace, plan)

    transport = httpx2.ASGITransport(app=create_app(workspace))
    async with httpx2.AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.post(
            "/api/presentations/nonexistent-id",
            json={"slides": [{"layout": "title", "title": "Nope"}]},
        )

    assert response.status_code == 404


@pytest.mark.anyio
async def test_presentation_duplicate_creation_rejected(tmp_path: Path) -> None:
    workspace = tmp_path / "test-course"
    workspace.mkdir()
    plan = create_course_plan(
        CoursePlanInput(
            title="Test Course",
            audience="Test",
            lectures=[LectureInput(title="Lecture 1")],
        )
    )
    initialize_workspace_history(workspace)
    create_course_plan_file(workspace, plan)

    transport = httpx2.ASGITransport(app=create_app(workspace))
    async with httpx2.AsyncClient(transport=transport, base_url="http://test") as client:
        first = await client.post(
            f"/api/presentations/{plan.lectures[0].id}",
            json={"slides": [{"layout": "title", "title": "First"}]},
        )
        assert first.status_code == 201

        second = await client.post(
            f"/api/presentations/{plan.lectures[0].id}",
            json={"slides": [{"layout": "title", "title": "Second"}]},
        )
        assert second.status_code == 409


@pytest.mark.anyio
async def test_delete_presentation(tmp_path: Path) -> None:
    workspace = tmp_path / "test-course"
    workspace.mkdir()
    plan = create_course_plan(
        CoursePlanInput(
            title="Test Course",
            audience="Test",
            lectures=[LectureInput(title="Lecture 1")],
        )
    )
    initialize_workspace_history(workspace)
    create_course_plan_file(workspace, plan)

    transport = httpx2.ASGITransport(app=create_app(workspace))
    async with httpx2.AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.post(
            f"/api/presentations/{plan.lectures[0].id}",
            json={"slides": [{"layout": "title", "title": "First"}]},
        )
        assert response.status_code == 201

        delete_response = await client.delete(f"/api/presentations/{plan.lectures[0].id}")
        assert delete_response.status_code == 204

        get_response = await client.get(f"/api/presentations/{plan.lectures[0].id}")
        assert get_response.status_code == 404


@pytest.mark.anyio
async def test_invalid_slide_layout_rejected(tmp_path: Path) -> None:
    workspace = tmp_path / "test-course"
    workspace.mkdir()
    plan = create_course_plan(
        CoursePlanInput(
            title="Test Course",
            audience="Test",
            lectures=[LectureInput(title="Lecture 1")],
        )
    )
    initialize_workspace_history(workspace)
    create_course_plan_file(workspace, plan)

    transport = httpx2.ASGITransport(app=create_app(workspace))
    async with httpx2.AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.post(
            f"/api/presentations/{plan.lectures[0].id}",
            json={"slides": [{"layout": "not_a_layout", "title": "Nope"}]},
        )

    assert response.status_code == 422


@pytest.mark.anyio
async def test_presentation_persistence(tmp_path: Path) -> None:
    workspace = tmp_path / "test-course"
    workspace.mkdir()
    plan = create_course_plan(
        CoursePlanInput(
            title="Test Course",
            audience="Test",
            lectures=[LectureInput(title="Lecture 1")],
        )
    )
    initialize_workspace_history(workspace)
    create_course_plan_file(workspace, plan)

    transport = httpx2.ASGITransport(app=create_app(workspace))
    async with httpx2.AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.post(
            f"/api/presentations/{plan.lectures[0].id}",
            json={"slides": [{"layout": "title", "title": "Persist test"}]},
        )
        pres_id = response.json()["id"]
        assert response.status_code == 201

        pres = read_presentation_for_lecture(workspace, plan.lectures[0].id)
        assert pres is not None
        assert pres.id == pres_id
        assert pres.slides[0].title == "Persist test"


@pytest.mark.anyio
async def test_all_slide_layouts_created(tmp_path: Path) -> None:
    workspace = tmp_path / "test-course"
    workspace.mkdir()
    plan = create_course_plan(
        CoursePlanInput(
            title="Test Course",
            audience="Test",
            lectures=[LectureInput(title="Lecture 1")],
        )
    )
    initialize_workspace_history(workspace)
    create_course_plan_file(workspace, plan)

    transport = httpx2.ASGITransport(app=create_app(workspace))
    async with httpx2.AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.post(
            f"/api/presentations/{plan.lectures[0].id}",
            json={
                "slides": [
                    {"layout": "title", "title": "Title slide", "subtitle": "Sub"},
                    {"layout": "section", "title": "Section slide"},
                    {"layout": "bullets", "title": "Bullets", "bullets": ["A", "B"]},
                    {
                        "layout": "two_column",
                        "title": "Two col",
                        "left_content": "L",
                        "right_content": "R",
                    },
                    {"layout": "big_statement", "statement": "Big statement"},
                    {"layout": "closing", "title": "Thanks", "text": "Closing"},
                    {"layout": "code", "code": "print('hello')", "language": "python"},
                    {
                        "layout": "image",
                        "title": "Image",
                        "image_url": "https://example.com/img.png",
                        "caption": "A caption",
                    },
                    {"layout": "quote", "quote": "To be", "attribution": "Shakespeare"},
                ]
            },
        )
        assert response.status_code == 201
        pres = Presentation.model_validate(response.json())
        assert len(pres.slides) == 9
        layouts = [s.layout for s in pres.slides]
        assert layouts == [
            "title",
            "section",
            "bullets",
            "two_column",
            "big_statement",
            "closing",
            "code",
            "image",
            "quote",
        ]
        assert pres.slides[0].subtitle == "Sub"  # type: ignore
        assert pres.slides[2].bullets == ["A", "B"]  # type: ignore
        assert pres.slides[3].left_content == "L"  # type: ignore
        assert pres.slides[4].statement == "Big statement"  # type: ignore
        assert pres.slides[6].code == "print('hello')"  # type: ignore
        assert pres.slides[6].language == "python"  # type: ignore
        assert pres.slides[8].quote == "To be"  # type: ignore
        assert pres.slides[8].attribution == "Shakespeare"  # type: ignore


@pytest.mark.anyio
async def test_list_slides_endpoint(tmp_path: Path) -> None:
    workspace = tmp_path / "test-course"
    workspace.mkdir()
    plan = create_course_plan(
        CoursePlanInput(
            title="Test Course",
            audience="Test",
            lectures=[LectureInput(title="Lecture 1")],
        )
    )
    initialize_workspace_history(workspace)
    create_course_plan_file(workspace, plan)

    from course_harness.presentation import BulletsSlide, TitleSlide, write_presentation

    pres = Presentation(
        id="presentation-abc123def456",
        lecture_id=plan.lectures[0].id,
        slides=[
            TitleSlide(id="slide-aaa111222333", title="First"),
            BulletsSlide(id="slide-bbb444555666", title="Second", bullets=["X"]),
        ],
    )
    write_presentation(workspace, pres)

    transport = httpx2.ASGITransport(
        app=create_app(
            workspace,
            library_data_path=tmp_path / "data",
            library_cache_path=tmp_path / "cache",
        )
    )
    async with httpx2.AsyncClient(transport=transport, base_url="http://test") as client:
        get_response = await client.get(f"/api/presentations/{plan.lectures[0].id}")
        assert get_response.status_code == 200
        data = get_response.json()
        assert len(data["slides"]) == 2
        assert data["slides"][0]["title"] == "First"
        assert data["slides"][1]["title"] == "Second"
        assert data["slides"][1]["bullets"] == ["X"]
