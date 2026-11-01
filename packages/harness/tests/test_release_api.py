from pathlib import Path

import httpx2
import pytest

from course_harness.app import create_app
from course_harness.canonical_mutation import capture_canonical_files
from course_harness.course_plan import (
    CoursePlan,
    Lecture,
    initialize_workspace_history,
    read_course_plan,
    write_course_plan,
)
from course_harness.presentation import Presentation, SlideCitation, TitleSlide, write_presentation
from course_harness.sources import Source, SourcesIndex, write_sources_index
from course_harness.workspace_history import (
    RevisionCreateRequest,
    create_revision,
    record_app_authored_state,
)

COURSE_ID = "course-aaaaaaaaaaaa"
LECTURE_ID = "lecture-aaaaaaaaaaaa"
PRESENTATION_ID = "presentation-aaaaaaaaaaaa"


def _release_request(*, slug: str = "fall-2026") -> dict[str, object]:
    return {
        "slug": slug,
        "name": "Fall 2026",
        "selection": {
            "lecture_ids": [LECTURE_ID],
            "artifact_ids": [PRESENTATION_ID],
        },
    }


def _validation_request() -> dict[str, object]:
    return {"selection": _release_request()["selection"]}


def _clean_course(workspace: Path) -> None:
    workspace.mkdir()
    initialize_workspace_history(workspace)
    write_course_plan(
        workspace,
        CoursePlan(
            id=COURSE_ID,
            title="Causal inference",
            audience="Graduate students",
            lectures=[
                Lecture(
                    id=LECTURE_ID,
                    title="Foundations",
                    presentation_id=PRESENTATION_ID,
                )
            ],
        ),
    )
    write_presentation(
        workspace,
        Presentation(
            id=PRESENTATION_ID,
            lecture_id=LECTURE_ID,
            slides=[TitleSlide(id="slide-aaaaaaaaaaaa", title="Foundations")],
        ),
    )
    create_revision(workspace, RevisionCreateRequest(summary="Initial Course"))
    record_app_authored_state(workspace)


def _add_coordinate_citation(
    workspace: Path, *, source_version_id: str, line_start: int, line_end: int | None = None
) -> None:
    write_sources_index(
        workspace,
        SourcesIndex(
            sources=[
                Source(
                    id="source-aaaaaaaaaaaa",
                    resource_id="resource-aaaaaaaaaaaa",
                    source_version_id=source_version_id,
                    label="Foundations",
                    admitted_at="2026-09-15T00:00:00+00:00",
                )
            ]
        ),
    )
    write_presentation(
        workspace,
        Presentation(
            id=PRESENTATION_ID,
            lecture_id=LECTURE_ID,
            slides=[
                TitleSlide(
                    id="slide-aaaaaaaaaaaa",
                    title="Foundations",
                    citations=[
                        SlideCitation(
                            source_id="source-aaaaaaaaaaaa",
                            label="Foundations",
                            line_start=line_start,
                            line_end=line_end,
                        )
                    ],
                )
            ],
        ),
    )
    create_revision(
        workspace,
        RevisionCreateRequest(summary="Add source-grounded citation"),
        expected=capture_canonical_files(
            workspace,
            {
                "course.yaml",
                "sources.yaml",
                f"presentations/{PRESENTATION_ID}.yaml",
            },
        ),
    )
    record_app_authored_state(workspace)


@pytest.mark.anyio
async def test_release_http_validates_publishes_and_serves_immutable_artifacts(
    tmp_path: Path,
) -> None:
    workspace = tmp_path / "course"
    release_data = tmp_path / "release-data"
    _clean_course(workspace)
    app = create_app(
        workspace,
        release_data_path=release_data,
        templates_data_path=tmp_path / "templates",
    )

    transport = httpx2.ASGITransport(app=app)
    async with httpx2.AsyncClient(transport=transport, base_url="http://test") as client:
        validation = await client.post("/api/releases/validate", json=_validation_request())
        published = await client.post("/api/releases", json=_release_request())
        listed = await client.get("/api/releases")
        detail = await client.get("/api/releases/fall-2026")
        artifact = await client.get(f"/api/releases/fall-2026/artifacts/{PRESENTATION_ID}")
        regenerated = await client.post(
            f"/api/releases/fall-2026/artifacts/{PRESENTATION_ID}/regenerate"
        )

    assert validation.status_code == 200
    assert validation.json()["can_publish"] is True
    assert validation.json()["selection"] == _validation_request()["selection"]
    assert published.status_code == 201
    assert published.json()["slug"] == "fall-2026"
    assert listed.status_code == 200
    assert listed.json() == [published.json()]
    assert detail.status_code == 200
    assert detail.json() == published.json()
    assert artifact.status_code == 200
    assert artifact.headers["content-type"].startswith(
        "application/vnd.openxmlformats-officedocument.presentationml.presentation"
    )
    assert f"{PRESENTATION_ID}.pptx" in artifact.headers["content-disposition"]
    assert artifact.content[:2] == b"PK"
    assert regenerated.status_code == 200
    assert regenerated.content == artifact.content

    # Release manifests remain inspectable from their tagged Revision even when
    # mutable Current State later becomes malformed.
    (workspace / "course.yaml").write_text("not: [valid", encoding="utf-8")
    transport = httpx2.ASGITransport(app=app)
    async with httpx2.AsyncClient(transport=transport, base_url="http://test") as client:
        assert (await client.get("/api/releases")).json() == [published.json()]
        assert (await client.get("/api/releases/fall-2026")).json() == published.json()


@pytest.mark.anyio
async def test_release_http_reports_invalid_waivers_and_missing_release_members_stably(
    tmp_path: Path,
) -> None:
    workspace = tmp_path / "course"
    _clean_course(workspace)
    transport = httpx2.ASGITransport(
        app=create_app(
            workspace,
            release_data_path=tmp_path / "release-data",
            templates_data_path=tmp_path / "templates",
        )
    )
    invalid_waiver = {
        **_validation_request(),
        "waivers": [{"finding_id": "warning-that-does-not-exist", "justification": "No."}],
    }

    async with httpx2.AsyncClient(transport=transport, base_url="http://test") as client:
        waiver_response = await client.post("/api/releases/validate", json=invalid_waiver)
        malformed_selection = await client.post(
            "/api/releases/validate",
            json={"selection": {"lecture_ids": ["not-a-lecture"]}},
        )
        missing_release = await client.get("/api/releases/no-such-release")

    assert waiver_response.status_code == 422
    assert waiver_response.json() == {
        "detail": "Waiver warning-that-does-not-exist does not match a current warning."
    }
    assert malformed_selection.status_code == 422
    assert missing_release.status_code == 404
    assert missing_release.json() == {"detail": "Course Release was not found"}


@pytest.mark.anyio
async def test_release_http_requires_initialized_history_for_all_immutable_release_routes(
    tmp_path: Path,
) -> None:
    workspace = tmp_path / "uninitialized"
    workspace.mkdir()
    transport = httpx2.ASGITransport(
        app=create_app(
            workspace,
            release_data_path=tmp_path / "release-data",
            templates_data_path=tmp_path / "templates",
        )
    )

    async with httpx2.AsyncClient(transport=transport, base_url="http://test") as client:
        responses = [
            await client.post("/api/releases", json=_release_request()),
            await client.get("/api/releases"),
            await client.get("/api/releases/fall-2026"),
            await client.get(f"/api/releases/fall-2026/artifacts/{PRESENTATION_ID}"),
            await client.post(f"/api/releases/fall-2026/artifacts/{PRESENTATION_ID}/regenerate"),
        ]

    assert [response.status_code for response in responses] == [404, 404, 404, 404, 404]
    assert all(
        response.json() == {"detail": "Course Workspace history is not initialized"}
        for response in responses
    )


@pytest.mark.anyio
async def test_release_validation_rejects_invalid_canonical_state_but_allows_valid_drift(
    tmp_path: Path,
) -> None:
    workspace = tmp_path / "course"
    _clean_course(workspace)
    app = create_app(
        workspace,
        release_data_path=tmp_path / "release-data",
        templates_data_path=tmp_path / "templates",
    )
    plan = read_course_plan(workspace)
    assert plan is not None
    write_course_plan(workspace, plan.model_copy(update={"title": "Applied causal inference"}))

    transport = httpx2.ASGITransport(app=app)
    async with httpx2.AsyncClient(transport=transport, base_url="http://test") as client:
        valid_drift = await client.post("/api/releases/validate", json=_validation_request())
        (workspace / "sources.yaml").write_text("sources: [", encoding="utf-8")
        invalid_sources = await client.post("/api/releases/validate", json=_validation_request())
        invalid_sources_publish = await client.post("/api/releases", json=_release_request())
        oversized = await client.post(
            "/api/releases/validate",
            json={"selection": {"lecture_ids": [LECTURE_ID] * 1_001}},
        )

    assert valid_drift.status_code == 200
    assert invalid_sources.status_code == 422
    assert "Current State must be structurally valid" in invalid_sources.json()["detail"]
    assert invalid_sources_publish.status_code == 409
    assert oversized.status_code == 422


@pytest.mark.anyio
async def test_release_http_enforces_inclusive_pinned_evidence_coordinates(tmp_path: Path) -> None:
    workspace = tmp_path / "course"
    cache_dir = tmp_path / "cache"
    source_version_id = "a" * 64
    _clean_course(workspace)
    _add_coordinate_citation(
        workspace, source_version_id=source_version_id, line_start=1, line_end=1
    )
    extracted = cache_dir / "derived" / source_version_id / "extracted.md"
    extracted.parent.mkdir(parents=True)
    extracted.write_text("first\nlast", encoding="utf-8")
    transport = httpx2.ASGITransport(
        app=create_app(
            workspace,
            library_cache_path=cache_dir,
            release_data_path=tmp_path / "release-data",
            templates_data_path=tmp_path / "templates",
        )
    )

    async with httpx2.AsyncClient(transport=transport, base_url="http://test") as client:
        validation = await client.post("/api/releases/validate", json=_validation_request())
        published = await client.post("/api/releases", json=_release_request())

    assert validation.status_code == 200
    assert validation.json()["can_publish"] is True
    assert published.status_code == 201


@pytest.mark.anyio
@pytest.mark.parametrize("has_extracted_evidence", [True, False])
async def test_release_http_blocks_out_of_range_or_missing_pinned_evidence(
    tmp_path: Path, has_extracted_evidence: bool
) -> None:
    workspace = tmp_path / "course"
    cache_dir = tmp_path / "cache"
    source_version_id = "a" * 64
    _clean_course(workspace)
    _add_coordinate_citation(workspace, source_version_id=source_version_id, line_start=2)
    if has_extracted_evidence:
        extracted = cache_dir / "derived" / source_version_id / "extracted.md"
        extracted.parent.mkdir(parents=True)
        extracted.write_text("first\nlast", encoding="utf-8")
    transport = httpx2.ASGITransport(
        app=create_app(
            workspace,
            library_cache_path=cache_dir,
            release_data_path=tmp_path / "release-data",
            templates_data_path=tmp_path / "templates",
        )
    )

    async with httpx2.AsyncClient(transport=transport, base_url="http://test") as client:
        validation = await client.post("/api/releases/validate", json=_validation_request())
        published = await client.post("/api/releases", json=_release_request())

    assert validation.status_code == 200
    assert validation.json()["can_publish"] is False
    finding = validation.json()["findings"][0]
    assert finding["code"] == "citation.unresolvable-evidence"
    assert finding["target"]["source_id"] == "source-aaaaaaaaaaaa"
    assert published.status_code == 422
