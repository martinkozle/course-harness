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

PP_MIME = "application/vnd.openxmlformats-officedocument.presentationml.presentation"
POTX_MIME = "application/vnd.openxmlformats-officedocument.presentationml.template"


def _make_test_pptx() -> bytes:
    prs = PPTXPresentation()
    buffer = __import__("io").BytesIO()
    prs.save(buffer)
    return buffer.getvalue()


def _app(workspace: Path, templates_data: Path, templates_cache: Path):
    return create_app(
        workspace,
        templates_data_path=templates_data,
        templates_cache_path=templates_cache,
    )


def _upload_pptx(client, pptx_bytes, filename="test.pptx"):
    return client.post(
        "/api/templates/upload",
        files={"file": (filename, pptx_bytes, PP_MIME)},
    )


@pytest.mark.anyio
async def test_list_templates_includes_builtin(tmp_path: Path) -> None:
    workspace = tmp_path / "ws"
    workspace.mkdir()
    templates_data = tmp_path / "tpl-data"
    templates_cache = tmp_path / "tpl-cache"
    transport = httpx2.ASGITransport(app=_app(workspace, templates_data, templates_cache))
    async with httpx2.AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.get("/api/templates")
        assert response.status_code == 200
        body = response.json()
        assert len(body) >= 1
        assert body[0]["id"] == "_builtin-default"


@pytest.mark.anyio
async def test_upload_template_returns_profile(tmp_path: Path) -> None:
    workspace = tmp_path / "ws"
    workspace.mkdir()
    templates_data = tmp_path / "tpl-data"
    templates_cache = tmp_path / "tpl-cache"
    transport = httpx2.ASGITransport(app=_app(workspace, templates_data, templates_cache))
    async with httpx2.AsyncClient(transport=transport, base_url="http://test") as client:
        pptx_bytes = _make_test_pptx()
        response = await _upload_pptx(client, pptx_bytes)
        assert response.status_code == 200
        body = response.json()
        profile = body["profile"]
        assert profile["id"].startswith("tpl-")
        assert profile["version"] == 1
        assert len(profile["layouts"]) == 9
        assert body["inspection"]["slide_count"] >= 9


@pytest.mark.anyio
async def test_upload_accepts_standard_potx_media_type(tmp_path: Path) -> None:
    workspace = tmp_path / "ws"
    workspace.mkdir()
    transport = httpx2.ASGITransport(
        app=_app(workspace, tmp_path / "tpl-data", tmp_path / "tpl-cache")
    )
    async with httpx2.AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.post(
            "/api/templates/upload",
            files={"file": ("test.potx", _make_test_pptx(), POTX_MIME)},
        )

    assert response.status_code == 200
    assert response.json()["profile"]["template_filename"] == "test.potx"


@pytest.mark.anyio
async def test_upload_and_get_template(tmp_path: Path) -> None:
    workspace = tmp_path / "ws"
    workspace.mkdir()
    templates_data = tmp_path / "tpl-data"
    templates_cache = tmp_path / "tpl-cache"
    transport = httpx2.ASGITransport(app=_app(workspace, templates_data, templates_cache))
    async with httpx2.AsyncClient(transport=transport, base_url="http://test") as client:
        pptx_bytes = _make_test_pptx()
        upload = await _upload_pptx(client, pptx_bytes)
        profile_id = upload.json()["profile"]["id"]

        response = await client.get(f"/api/templates/{profile_id}")
        assert response.status_code == 200
        body = response.json()
        assert body["id"] == profile_id
        assert body["name"] == "test"


@pytest.mark.anyio
async def test_duplicate_upload_names_are_disambiguated(tmp_path: Path) -> None:
    workspace = tmp_path / "ws"
    workspace.mkdir()
    transport = httpx2.ASGITransport(
        app=_app(workspace, tmp_path / "tpl-data", tmp_path / "tpl-cache")
    )
    async with httpx2.AsyncClient(transport=transport, base_url="http://test") as client:
        first = await _upload_pptx(client, _make_test_pptx(), "Corporate.pptx")
        second = await _upload_pptx(client, _make_test_pptx(), "corporate.pptx")

    assert first.json()["profile"]["name"] == "Corporate"
    assert second.json()["profile"]["name"] == "corporate (2)"


@pytest.mark.anyio
async def test_rename_template_updates_display_name_without_bumping_version(
    tmp_path: Path,
) -> None:
    workspace = tmp_path / "ws"
    workspace.mkdir()
    transport = httpx2.ASGITransport(
        app=_app(workspace, tmp_path / "tpl-data", tmp_path / "tpl-cache")
    )
    async with httpx2.AsyncClient(transport=transport, base_url="http://test") as client:
        upload = await _upload_pptx(client, _make_test_pptx())
        profile_id = upload.json()["profile"]["id"]
        renamed = await client.patch(f"/api/templates/{profile_id}", json={"name": "Faculty Brand"})
        listed = await client.get("/api/templates")

    assert renamed.status_code == 200
    assert renamed.json()["name"] == "Faculty Brand"
    assert renamed.json()["version"] == 1
    summary = next(item for item in listed.json() if item["id"] == profile_id)
    assert summary["name"] == "Faculty Brand"
    assert summary["version"] == 1


@pytest.mark.anyio
async def test_rename_template_rejects_duplicate_display_name(tmp_path: Path) -> None:
    workspace = tmp_path / "ws"
    workspace.mkdir()
    transport = httpx2.ASGITransport(
        app=_app(workspace, tmp_path / "tpl-data", tmp_path / "tpl-cache")
    )
    async with httpx2.AsyncClient(transport=transport, base_url="http://test") as client:
        first = await _upload_pptx(client, _make_test_pptx(), "Faculty.pptx")
        second = await _upload_pptx(client, _make_test_pptx(), "Workshop.pptx")
        duplicate = await client.patch(
            f"/api/templates/{second.json()['profile']['id']}",
            json={"name": "faculty"},
        )
        builtin = await client.patch(
            f"/api/templates/{first.json()['profile']['id']}",
            json={"name": "Built-in default"},
        )

    assert duplicate.status_code == 409
    assert builtin.status_code == 409


@pytest.mark.anyio
async def test_update_mapping_bumps_version(tmp_path: Path) -> None:
    workspace = tmp_path / "ws"
    workspace.mkdir()
    templates_data = tmp_path / "tpl-data"
    templates_cache = tmp_path / "tpl-cache"
    transport = httpx2.ASGITransport(app=_app(workspace, templates_data, templates_cache))
    async with httpx2.AsyncClient(transport=transport, base_url="http://test") as client:
        pptx_bytes = _make_test_pptx()
        upload = await _upload_pptx(client, pptx_bytes)
        profile_id = upload.json()["profile"]["id"]

        update = await client.put(
            f"/api/templates/{profile_id}",
            json={
                "mappings": [
                    {"semantic_layout": "title", "template_layout_index": 0},
                    {"semantic_layout": "bullets", "template_layout_index": 0},
                ]
            },
        )
        assert update.status_code == 200
        body = update.json()
        assert body["version"] == 2
        bullets_layout = next(m for m in body["layouts"] if m["semantic_layout"] == "bullets")
        assert bullets_layout["confidence"] == 1.0
        assert "corrected" in bullets_layout["rationale"].lower()


@pytest.mark.anyio
async def test_course_pin_uses_an_existing_profile_version(tmp_path: Path) -> None:
    workspace = tmp_path / "ws"
    workspace.mkdir()
    plan = create_course_plan(
        CoursePlanInput(
            title="Pinned",
            audience="Test",
            lectures=[LectureInput(title="L1")],
        )
    )
    initialize_workspace_history(workspace)
    create_course_plan_file(workspace, plan)
    transport = httpx2.ASGITransport(
        app=_app(workspace, tmp_path / "tpl-data", tmp_path / "tpl-cache")
    )
    async with httpx2.AsyncClient(transport=transport, base_url="http://test") as client:
        upload = await _upload_pptx(client, _make_test_pptx())
        profile_id = upload.json()["profile"]["id"]
        pinned = await client.patch(
            "/api/course/profile",
            json={"template_profile_id": profile_id, "template_profile_version": 1},
        )
        missing = await client.patch(
            "/api/course/profile",
            json={"template_profile_id": profile_id, "template_profile_version": 99},
        )

    assert pinned.status_code == 200
    assert pinned.json()["template_profile_id"] == profile_id
    assert pinned.json()["template_profile_version"] == 1
    assert missing.status_code == 422


@pytest.mark.anyio
async def test_delete_template(tmp_path: Path) -> None:
    workspace = tmp_path / "ws"
    workspace.mkdir()
    templates_data = tmp_path / "tpl-data"
    templates_cache = tmp_path / "tpl-cache"
    transport = httpx2.ASGITransport(app=_app(workspace, templates_data, templates_cache))
    async with httpx2.AsyncClient(transport=transport, base_url="http://test") as client:
        pptx_bytes = _make_test_pptx()
        upload = await _upload_pptx(client, pptx_bytes)
        profile_id = upload.json()["profile"]["id"]

        delete = await client.delete(f"/api/templates/{profile_id}")
        assert delete.status_code == 204

        get = await client.get(f"/api/templates/{profile_id}")
        assert get.status_code == 404

        list_resp = await client.get("/api/templates")
        for p in list_resp.json():
            assert p["id"] != profile_id


@pytest.mark.anyio
async def test_cannot_delete_builtin(tmp_path: Path) -> None:
    workspace = tmp_path / "ws"
    workspace.mkdir()
    templates_data = tmp_path / "tpl-data"
    templates_cache = tmp_path / "tpl-cache"
    transport = httpx2.ASGITransport(app=_app(workspace, templates_data, templates_cache))
    async with httpx2.AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.delete("/api/templates/_builtin-default")
        assert response.status_code == 400


@pytest.mark.anyio
async def test_reject_non_pptx_upload(tmp_path: Path) -> None:
    workspace = tmp_path / "ws"
    workspace.mkdir()
    templates_data = tmp_path / "tpl-data"
    templates_cache = tmp_path / "tpl-cache"
    transport = httpx2.ASGITransport(app=_app(workspace, templates_data, templates_cache))
    async with httpx2.AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.post(
            "/api/templates/upload",
            files={"file": ("test.pdf", b"not-a-pptx", "application/pdf")},
        )
        assert response.status_code == 422


@pytest.mark.anyio
async def test_get_builtin_template(tmp_path: Path) -> None:
    workspace = tmp_path / "ws"
    workspace.mkdir()
    templates_data = tmp_path / "tpl-data"
    templates_cache = tmp_path / "tpl-cache"
    transport = httpx2.ASGITransport(app=_app(workspace, templates_data, templates_cache))
    async with httpx2.AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.get("/api/templates/_builtin-default")
        assert response.status_code == 200
        body = response.json()
        assert body["id"] == "_builtin-default"
        assert len(body["layouts"]) == 9


@pytest.mark.anyio
async def test_cannot_update_builtin(tmp_path: Path) -> None:
    workspace = tmp_path / "ws"
    workspace.mkdir()
    templates_data = tmp_path / "tpl-data"
    templates_cache = tmp_path / "tpl-cache"
    transport = httpx2.ASGITransport(app=_app(workspace, templates_data, templates_cache))
    async with httpx2.AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.put(
            "/api/templates/_builtin-default",
            json={"mappings": [{"semantic_layout": "title", "template_layout_index": 0}]},
        )
        assert response.status_code == 400
