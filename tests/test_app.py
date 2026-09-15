from contextlib import suppress
from pathlib import Path

import httpx2
import pytest

import course_harness.app as app_module
from course_harness import canonical_mutation
from course_harness.app import create_app
from course_harness.course_plan import read_course_plan, write_course_plan


@pytest.mark.anyio
async def test_application_reports_health_and_active_workspace(tmp_path: Path) -> None:
    workspace = tmp_path / "statistical-learning"
    workspace.mkdir()

    transport = httpx2.ASGITransport(app=create_app(workspace))
    async with httpx2.AsyncClient(transport=transport, base_url="http://test") as client:
        health_response = await client.get("/api/health")
        workspace_response = await client.get("/api/workspace")

    assert health_response.status_code == 200
    assert health_response.json() == {"status": "ok"}
    assert workspace_response.status_code == 200
    assert workspace_response.json() == {
        "name": "statistical-learning",
        "path": str(workspace),
    }


@pytest.mark.anyio
async def test_application_starts_unbound_and_rejects_workspace_access() -> None:
    transport = httpx2.ASGITransport(app=create_app())
    async with httpx2.AsyncClient(transport=transport, base_url="http://test") as client:
        health_response = await client.get("/api/health")
        workspace_response = await client.get("/api/workspace")

    assert health_response.status_code == 200
    assert workspace_response.status_code == 409
    assert workspace_response.json() == {"detail": "No Course Workspace is active"}


@pytest.mark.anyio
async def test_launcher_opens_one_workspace_through_the_native_picker(tmp_path: Path) -> None:
    workspace = tmp_path / "causal-inference"
    workspace.mkdir()
    transport = httpx2.ASGITransport(
        app=create_app(folder_picker=lambda: workspace, recent_store_path=tmp_path / "recent.json")
    )

    async with httpx2.AsyncClient(transport=transport, base_url="http://test") as client:
        selection_response = await client.post("/api/launcher/open-folder")
        workspace_response = await client.get("/api/workspace")
        second_selection_response = await client.post("/api/launcher/open-folder")

    assert selection_response.status_code == 200
    assert selection_response.json() == {
        "name": "causal-inference",
        "path": str(workspace),
    }
    assert workspace_response.json() == selection_response.json()
    assert second_selection_response.status_code == 409
    assert second_selection_response.json() == {"detail": "A Course Workspace is already active"}


@pytest.mark.anyio
async def test_closing_a_workspace_returns_to_an_unbound_launcher(tmp_path: Path) -> None:
    first_workspace = tmp_path / "first-course"
    first_workspace.mkdir()
    second_workspace = tmp_path / "second-course"
    second_workspace.mkdir()
    transport = httpx2.ASGITransport(
        app=create_app(
            first_workspace,
            folder_picker=lambda: second_workspace,
            recent_store_path=tmp_path / "recent.json",
        )
    )

    async with httpx2.AsyncClient(transport=transport, base_url="http://test") as client:
        close_response = await client.post("/api/workspace/close")
        unbound_response = await client.get("/api/workspace")
        open_response = await client.post("/api/launcher/open-folder")

    assert close_response.status_code == 204
    assert unbound_response.status_code == 409
    assert open_response.status_code == 200
    assert open_response.json()["path"] == str(second_workspace)


@pytest.mark.anyio
async def test_recent_store_failure_leaves_the_launcher_unbound(tmp_path: Path) -> None:
    workspace = tmp_path / "course"
    workspace.mkdir()
    recent_store = tmp_path / "recent.json"
    recent_store.mkdir()
    transport = httpx2.ASGITransport(
        app=create_app(folder_picker=lambda: workspace, recent_store_path=recent_store)
    )

    async with httpx2.AsyncClient(transport=transport, base_url="http://test") as client:
        selection_response = await client.post("/api/launcher/open-folder")
        workspace_response = await client.get("/api/workspace")

    assert selection_response.status_code == 500
    assert selection_response.json() == {
        "detail": "Recent Workspace data could not be saved; the Workspace was not opened"
    }
    assert workspace_response.status_code == 409


@pytest.mark.anyio
async def test_cancelling_folder_selection_leaves_the_launcher_usable(tmp_path: Path) -> None:
    workspace = tmp_path / "later-selection"
    workspace.mkdir()
    selections = iter([None, workspace])
    transport = httpx2.ASGITransport(
        app=create_app(
            folder_picker=lambda: next(selections), recent_store_path=tmp_path / "recent.json"
        )
    )

    async with httpx2.AsyncClient(transport=transport, base_url="http://test") as client:
        cancelled_response = await client.post("/api/launcher/open-folder")
        unbound_response = await client.get("/api/workspace")
        selected_response = await client.post("/api/launcher/open-folder")

    assert cancelled_response.status_code == 204
    assert unbound_response.status_code == 409
    assert selected_response.status_code == 200


@pytest.mark.anyio
async def test_new_course_refuses_an_existing_course_and_leaves_open_available(
    tmp_path: Path,
) -> None:
    workspace = tmp_path / "existing-course"
    workspace.mkdir()
    (workspace / "course.yaml").write_text("schema_version: 1\n", encoding="utf-8")
    selections = iter([workspace, workspace])
    transport = httpx2.ASGITransport(
        app=create_app(
            folder_picker=lambda: next(selections), recent_store_path=tmp_path / "recent.json"
        )
    )

    async with httpx2.AsyncClient(transport=transport, base_url="http://test") as client:
        new_response = await client.post("/api/launcher/new-course")
        unbound_response = await client.get("/api/workspace")
        open_response = await client.post("/api/launcher/open-folder")

    assert new_response.status_code == 409
    assert new_response.json() == {
        "detail": "That folder already contains a Course; use Open Folder instead"
    }
    assert unbound_response.status_code == 409
    assert open_response.status_code == 200


@pytest.mark.anyio
async def test_launcher_reopens_a_recent_workspace_by_identity(tmp_path: Path) -> None:
    workspace = tmp_path / "recent-course"
    workspace.mkdir()
    recent_store = tmp_path / "user-data" / "recent.json"
    first_transport = httpx2.ASGITransport(
        app=create_app(folder_picker=lambda: workspace, recent_store_path=recent_store)
    )
    async with httpx2.AsyncClient(transport=first_transport, base_url="http://test") as client:
        await client.post("/api/launcher/open-folder")

    second_transport = httpx2.ASGITransport(app=create_app(recent_store_path=recent_store))
    async with httpx2.AsyncClient(transport=second_transport, base_url="http://test") as client:
        recent_response = await client.get("/api/launcher/recent")
        recent_workspace = recent_response.json()[0]
        reopen_response = await client.post(f"/api/launcher/recent/{recent_workspace['id']}/open")

    assert recent_response.status_code == 200
    assert recent_workspace == {
        "id": recent_workspace["id"],
        "name": "recent-course",
        "path": str(workspace),
    }
    assert reopen_response.status_code == 200
    assert reopen_response.json()["path"] == str(workspace)

    workspace.rmdir()
    moved_transport = httpx2.ASGITransport(app=create_app(recent_store_path=recent_store))
    async with httpx2.AsyncClient(transport=moved_transport, base_url="http://test") as client:
        missing_response = await client.post(f"/api/launcher/recent/{recent_workspace['id']}/open")
        still_unbound_response = await client.get("/api/workspace")

    assert missing_response.status_code == 410
    assert missing_response.json() == {
        "detail": f"Recent Course Workspace is no longer available: {workspace}"
    }
    assert still_unbound_response.status_code == 409


@pytest.mark.anyio
async def test_course_plan_is_created_once_and_restored_without_a_runtime_cache(
    tmp_path: Path,
) -> None:
    workspace = tmp_path / "course"
    workspace.mkdir()
    request = {
        "title": "Causal Inference in Practice",
        "audience": "Applied researchers who know regression",
        "goals": ["Reason clearly about interventions"],
        "outcomes": ["Draw and critique a causal graph"],
        "lectures": [
            {"title": "From association to intervention"},
            {"title": "Confounding and adjustment"},
        ],
    }
    transport = httpx2.ASGITransport(app=create_app(workspace))
    async with httpx2.AsyncClient(transport=transport, base_url="http://test") as client:
        create_response = await client.post("/api/course", json=request)
        duplicate_response = await client.post("/api/course", json=request)

    assert create_response.status_code == 201
    plan = create_response.json()
    assert plan["id"].startswith("course-")
    assert plan["title"] == request["title"]
    assert [lecture["title"] for lecture in plan["lectures"]] == [
        "From association to intervention",
        "Confounding and adjustment",
    ]
    assert len({lecture["id"] for lecture in plan["lectures"]}) == 2
    assert duplicate_response.status_code == 409
    assert duplicate_response.json() == {"detail": "This Workspace already contains a Course"}

    course_file = workspace / "course.yaml"
    assert course_file.is_file()
    assert (workspace / ".git").is_dir()
    assert "title: Causal Inference in Practice" in course_file.read_text(encoding="utf-8")
    assert not (workspace / ".course-harness").exists()

    reopened_transport = httpx2.ASGITransport(app=create_app(workspace))
    async with httpx2.AsyncClient(transport=reopened_transport, base_url="http://test") as client:
        reopened_response = await client.get("/api/course")

    assert reopened_response.status_code == 200
    assert reopened_response.json() == plan


@pytest.mark.anyio
async def test_course_can_be_created_without_goals_or_outcomes(tmp_path: Path) -> None:
    workspace = tmp_path / "course"
    workspace.mkdir()
    transport = httpx2.ASGITransport(app=create_app(workspace))

    async with httpx2.AsyncClient(transport=transport, base_url="http://test") as client:
        create_response = await client.post(
            "/api/course",
            json={
                "title": "Practical Statistics",
                "audience": "Working analysts",
                "lectures": [{"title": "Reasoning with variation"}],
            },
        )

    assert create_response.status_code == 201
    assert create_response.json()["goals"] == []
    assert create_response.json()["outcomes"] == []


@pytest.mark.anyio
async def test_renaming_a_lecture_preserves_its_identity(tmp_path: Path) -> None:
    workspace = tmp_path / "course"
    workspace.mkdir()
    transport = httpx2.ASGITransport(app=create_app(workspace))
    async with httpx2.AsyncClient(transport=transport, base_url="http://test") as client:
        created = await client.post(
            "/api/course",
            json={
                "title": "Data Ethics",
                "audience": "Data practitioners",
                "goals": ["Recognize ethical risks"],
                "outcomes": ["Review a data project"],
                "lectures": [{"title": "Fairness"}, {"title": "Privacy"}],
            },
        )
        lecture_id = created.json()["lectures"][0]["id"]
        renamed = await client.patch(
            f"/api/course/lectures/{lecture_id}", json={"title": "Fairness in context"}
        )

    assert renamed.status_code == 200
    assert renamed.json()["lectures"][0] == {
        "id": lecture_id,
        "title": "Fairness in context",
        "group": None,
        "source_focus": None,
        "presentation_id": None,
    }

    reopened_transport = httpx2.ASGITransport(app=create_app(workspace))
    async with httpx2.AsyncClient(transport=reopened_transport, base_url="http://test") as client:
        reopened = await client.get("/api/course")
    assert reopened.json() == renamed.json()


@pytest.mark.anyio
async def test_course_endpoint_preserves_a_same_path_external_edit_during_save(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    workspace = tmp_path / "course"
    workspace.mkdir()
    transport = httpx2.ASGITransport(app=create_app(workspace))
    async with httpx2.AsyncClient(transport=transport, base_url="http://test") as client:
        created = await client.post(
            "/api/course",
            json={
                "title": "Data Ethics",
                "audience": "Data practitioners",
                "lectures": [{"title": "Fairness"}],
            },
        )
        lecture_id = created.json()["lectures"][0]["id"]

        def external_edit(path: Path, _expected: object) -> None:
            monkeypatch.setattr(canonical_mutation, "after_precondition_check", None)
            plan = read_course_plan(path)
            assert plan is not None
            write_course_plan(path, plan.model_copy(update={"title": "External edit"}))

        monkeypatch.setattr(canonical_mutation, "after_precondition_check", external_edit)
        response = await client.patch(
            f"/api/course/lectures/{lecture_id}", json={"title": "Application edit"}
        )

    assert response.status_code == 409
    plan = read_course_plan(workspace)
    assert plan is not None
    assert plan.title == "External edit"


@pytest.mark.anyio
async def test_course_endpoint_does_not_adopt_an_edit_between_capture_and_drift_guard(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    workspace = tmp_path / "course"
    workspace.mkdir()
    transport = httpx2.ASGITransport(app=create_app(workspace))
    async with httpx2.AsyncClient(transport=transport, base_url="http://test") as client:
        created = await client.post(
            "/api/course",
            json={
                "title": "Original",
                "audience": "Researchers",
                "lectures": [{"title": "Foundations"}],
            },
        )
        lecture_id = created.json()["lectures"][0]["id"]
        original_read_current_state = app_module.read_current_state

        def edit_before_guard(path: Path) -> object:
            monkeypatch.setattr(app_module, "read_current_state", original_read_current_state)
            plan = read_course_plan(path)
            assert plan is not None
            write_course_plan(path, plan.model_copy(update={"title": "External edit"}))
            return original_read_current_state(path)

        monkeypatch.setattr(app_module, "read_current_state", edit_before_guard)
        response = await client.patch(
            f"/api/course/lectures/{lecture_id}", json={"title": "Application edit"}
        )

    assert response.status_code == 409
    plan = read_course_plan(workspace)
    assert plan is not None
    assert plan.title == "External edit"


@pytest.mark.anyio
async def test_reordering_lectures_requires_an_exact_identity_permutation(tmp_path: Path) -> None:
    workspace = tmp_path / "course"
    workspace.mkdir()
    transport = httpx2.ASGITransport(app=create_app(workspace))
    async with httpx2.AsyncClient(transport=transport, base_url="http://test") as client:
        created = await client.post(
            "/api/course",
            json={
                "title": "Field Methods",
                "audience": "New researchers",
                "goals": ["Plan robust fieldwork"],
                "outcomes": ["Design an interview study"],
                "lectures": [
                    {"title": "Questions"},
                    {"title": "Sampling"},
                    {"title": "Analysis"},
                ],
            },
        )
        ids = [lecture["id"] for lecture in created.json()["lectures"]]
        reordered = await client.put(
            "/api/course/lectures/order", json={"lecture_ids": [ids[2], ids[0], ids[1]]}
        )
        invalid = await client.put(
            "/api/course/lectures/order", json={"lecture_ids": [ids[0], ids[0], ids[1]]}
        )
        after_invalid = await client.get("/api/course")

    assert reordered.status_code == 200
    assert [lecture["id"] for lecture in reordered.json()["lectures"]] == [
        ids[2],
        ids[0],
        ids[1],
    ]
    assert invalid.status_code == 422
    assert invalid.json() == {"detail": "Lecture order must contain every Lecture ID exactly once"}
    assert after_invalid.json() == reordered.json()


@pytest.mark.anyio
async def test_workspace_explorer_is_bounded_to_safe_relative_entries(tmp_path: Path) -> None:
    workspace = tmp_path / "course"
    (workspace / "notes").mkdir(parents=True)
    (workspace / "notes" / "welcome.md").write_text("# Welcome\n", encoding="utf-8")
    (workspace / ".course-harness").mkdir()
    (workspace / ".course-harness" / "cache.bin").write_bytes(b"derived")
    outside = tmp_path / "outside.txt"
    outside.write_text("not part of the Workspace", encoding="utf-8")
    with suppress(OSError):
        (workspace / "outside-link").symlink_to(outside)

    transport = httpx2.ASGITransport(app=create_app(workspace))
    async with httpx2.AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.get("/api/workspace/files")

    assert response.status_code == 200
    assert response.json() == [
        {"path": "notes", "kind": "directory"},
        {"path": "notes/welcome.md", "kind": "file"},
    ]


@pytest.mark.anyio
async def test_validation_failure_does_not_change_canonical_course_state(tmp_path: Path) -> None:
    workspace = tmp_path / "course"
    workspace.mkdir()
    transport = httpx2.ASGITransport(app=create_app(workspace))
    async with httpx2.AsyncClient(transport=transport, base_url="http://test") as client:
        created = await client.post(
            "/api/course",
            json={
                "title": "Writing for Engineers",
                "audience": "Software engineers",
                "goals": ["Write for a reader"],
                "outcomes": ["Revise a design note"],
                "lectures": [{"title": "Structure"}],
            },
        )
        lecture_id = created.json()["lectures"][0]["id"]
        before = (workspace / "course.yaml").read_bytes()
        invalid = await client.patch(f"/api/course/lectures/{lecture_id}", json={"title": "   "})

    assert invalid.status_code == 422
    assert (workspace / "course.yaml").read_bytes() == before


@pytest.mark.anyio
async def test_reopening_invalid_utf8_course_state_reports_validation_failure(
    tmp_path: Path,
) -> None:
    workspace = tmp_path / "course"
    workspace.mkdir()
    (workspace / "course.yaml").write_bytes(b"\xff\xfe invalid course state")
    transport = httpx2.ASGITransport(app=create_app(workspace))

    async with httpx2.AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.get("/api/course")

    assert response.status_code == 422
    assert response.json()["detail"].startswith("course.yaml is invalid:")
