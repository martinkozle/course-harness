from pathlib import Path

import httpx2
import pytest

from course_harness import resources as res
from course_harness.app import create_app

FIXTURES = Path(__file__).with_name("fixtures") / "resources"


def _app(workspace: Path, data_dir: Path, cache_dir: Path):
    return create_app(workspace, library_data_path=data_dir, library_cache_path=cache_dir)


@pytest.mark.anyio
async def test_register_local_file_resource(tmp_path: Path) -> None:
    workspace = tmp_path / "resource-course"
    workspace.mkdir()
    data_dir = tmp_path / "library-data"
    cache_dir = tmp_path / "library-cache"

    fixture = FIXTURES / "hello.md"
    transport = httpx2.ASGITransport(app=_app(workspace, data_dir, cache_dir))

    async with httpx2.AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.post(
            "/api/resources",
            json={"kind": "local-file", "location": str(fixture), "media_type": "text/markdown"},
        )

    assert response.status_code == 201
    body = response.json()
    assert body["id"].startswith("resource-")
    assert body["kind"] == "local-file"
    assert body["media_type"] == "text/markdown"
    assert body["snapshot_hash"] is None

    index = res.read_library_index(data_dir / "registry.json")
    assert len(index.resources) == 1
    assert index.resources[0].id == body["id"]


@pytest.mark.anyio
async def test_register_upload_resource(tmp_path: Path) -> None:
    workspace = tmp_path / "resource-course"
    workspace.mkdir()
    data_dir = tmp_path / "library-data"
    cache_dir = tmp_path / "library-cache"

    fixture = FIXTURES / "hello.md"
    transport = httpx2.ASGITransport(app=_app(workspace, data_dir, cache_dir))

    async with httpx2.AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.post(
            "/api/resources/upload",
            files={"file": ("hello.md", fixture.read_bytes(), "text/markdown")},
        )

    assert response.status_code == 201
    body = response.json()
    assert body["status"] == "ready"
    assert body["snapshot"] is not None
    assert len(body["snapshot"]["content_hash"]) == 64

    index = res.read_library_index(data_dir / "registry.json")
    resource = index.resources[0]
    assert resource.snapshot_hash == body["snapshot"]["content_hash"]


@pytest.mark.anyio
async def test_resource_initial_state_is_unprocessed(tmp_path: Path) -> None:
    workspace = tmp_path / "resource-course"
    workspace.mkdir()
    data_dir = tmp_path / "library-data"
    cache_dir = tmp_path / "library-cache"

    fixture = FIXTURES / "hello.md"
    transport = httpx2.ASGITransport(app=_app(workspace, data_dir, cache_dir))

    async with httpx2.AsyncClient(transport=transport, base_url="http://test") as client:
        create = await client.post(
            "/api/resources",
            json={"kind": "local-file", "location": str(fixture), "media_type": "text/markdown"},
        )
        resource_id = create.json()["id"]

        detail = await client.get(f"/api/resources/{resource_id}")

    assert detail.status_code == 200
    assert detail.json()["status"] == "unprocessed"
    assert detail.json()["snapshot"] is None


@pytest.mark.anyio
async def test_process_markdown_resource(tmp_path: Path) -> None:
    workspace = tmp_path / "resource-course"
    workspace.mkdir()
    data_dir = tmp_path / "library-data"
    cache_dir = tmp_path / "library-cache"

    fixture = FIXTURES / "hello.md"
    transport = httpx2.ASGITransport(app=_app(workspace, data_dir, cache_dir))

    async with httpx2.AsyncClient(transport=transport, base_url="http://test") as client:
        create = await client.post(
            "/api/resources",
            json={"kind": "local-file", "location": str(fixture), "media_type": "text/markdown"},
        )
        resource_id = create.json()["id"]

        process = await client.post(f"/api/resources/{resource_id}/process")

    assert process.status_code == 200
    body = process.json()
    assert body["status"] == "ready"
    assert body["snapshot"] is not None
    assert body["snapshot"]["resource_id"] == resource_id
    assert len(body["snapshot"]["content_hash"]) == 64
    assert body["snapshot"]["byte_count"] > 0

    derived = cache_dir / "derived" / body["snapshot"]["content_hash"] / "extracted.md"
    assert derived.is_file()


@pytest.mark.anyio
async def test_process_code_resource(tmp_path: Path) -> None:
    workspace = tmp_path / "resource-course"
    workspace.mkdir()
    data_dir = tmp_path / "library-data"
    cache_dir = tmp_path / "library-cache"

    fixture = FIXTURES / "sample.py"
    transport = httpx2.ASGITransport(app=_app(workspace, data_dir, cache_dir))

    async with httpx2.AsyncClient(transport=transport, base_url="http://test") as client:
        create = await client.post(
            "/api/resources",
            json={
                "kind": "local-file",
                "location": str(fixture),
                "media_type": "text/x-python",
            },
        )
        resource_id = create.json()["id"]

        process = await client.post(f"/api/resources/{resource_id}/process")

    assert process.status_code == 200
    derived_path = (
        cache_dir / "derived" / process.json()["snapshot"]["content_hash"] / "extracted.md"
    )
    assert derived_path.is_file()
    content = derived_path.read_text(encoding="utf-8")
    assert "```" in content
    assert "def greet" in content


@pytest.mark.anyio
async def test_process_pdf_fallback(tmp_path: Path) -> None:
    workspace = tmp_path / "resource-course"
    workspace.mkdir()
    data_dir = tmp_path / "library-data"
    cache_dir = tmp_path / "library-cache"

    fixture = FIXTURES / "sample.pdf"
    transport = httpx2.ASGITransport(app=_app(workspace, data_dir, cache_dir))

    async with httpx2.AsyncClient(transport=transport, base_url="http://test") as client:
        create = await client.post(
            "/api/resources",
            json={
                "kind": "local-file",
                "location": str(fixture),
                "media_type": "application/pdf",
            },
        )
        resource_id = create.json()["id"]

        process = await client.post(f"/api/resources/{resource_id}/process")

    assert process.status_code == 422
    assert "No processor" in process.json()["detail"]


@pytest.mark.anyio
async def test_content_hash_deduplication(tmp_path: Path) -> None:
    workspace = tmp_path / "resource-course"
    workspace.mkdir()
    data_dir = tmp_path / "library-data"
    cache_dir = tmp_path / "library-cache"

    fixture = FIXTURES / "hello.md"
    transport = httpx2.ASGITransport(app=_app(workspace, data_dir, cache_dir))

    async with httpx2.AsyncClient(transport=transport, base_url="http://test") as client:
        create1 = await client.post(
            "/api/resources",
            json={"kind": "local-file", "location": str(fixture), "media_type": "text/markdown"},
        )
        await client.post(f"/api/resources/{create1.json()['id']}/process")

        create2 = await client.post(
            "/api/resources",
            json={"kind": "local-file", "location": str(fixture), "media_type": "text/markdown"},
        )
        process2 = await client.post(f"/api/resources/{create2.json()['id']}/process")

    process1_hash = res.read_library_index(data_dir / "registry.json").resources[0].snapshot_hash
    process2_hash = process2.json()["snapshot"]["content_hash"]
    assert process1_hash == process2_hash

    snapshot_files = list((data_dir / "snapshots").iterdir())
    assert len(snapshot_files) == 1


@pytest.mark.anyio
async def test_process_empty_resource(tmp_path: Path) -> None:
    workspace = tmp_path / "resource-course"
    workspace.mkdir()
    data_dir = tmp_path / "library-data"
    cache_dir = tmp_path / "library-cache"

    fixture = FIXTURES / "empty.txt"
    transport = httpx2.ASGITransport(app=_app(workspace, data_dir, cache_dir))

    async with httpx2.AsyncClient(transport=transport, base_url="http://test") as client:
        create = await client.post(
            "/api/resources",
            json={"kind": "local-file", "location": str(fixture), "media_type": "text/plain"},
        )
        resource_id = create.json()["id"]

        process = await client.post(f"/api/resources/{resource_id}/process")

    assert process.status_code == 200
    body = process.json()
    assert body["status"] == "ready"
    assert body["snapshot"]["byte_count"] == 0


@pytest.mark.anyio
async def test_delete_cache_allows_regeneration(tmp_path: Path) -> None:
    workspace = tmp_path / "resource-course"
    workspace.mkdir()
    data_dir = tmp_path / "library-data"
    cache_dir = tmp_path / "library-cache"

    fixture = FIXTURES / "hello.md"
    transport = httpx2.ASGITransport(app=_app(workspace, data_dir, cache_dir))

    async with httpx2.AsyncClient(transport=transport, base_url="http://test") as client:
        create = await client.post(
            "/api/resources",
            json={"kind": "local-file", "location": str(fixture), "media_type": "text/markdown"},
        )
        resource_id = create.json()["id"]

        await client.post(f"/api/resources/{resource_id}/process")

        clear = await client.delete("/api/resources/cache")
        assert clear.status_code == 204

        process_again = await client.post(f"/api/resources/{resource_id}/process")

    assert process_again.status_code == 200
    assert process_again.json()["status"] == "ready"


@pytest.mark.anyio
async def test_library_registry_persistence(tmp_path: Path) -> None:
    workspace = tmp_path / "resource-course"
    workspace.mkdir()
    data_dir = tmp_path / "library-data"
    cache_dir = tmp_path / "library-cache"

    fixture = FIXTURES / "hello.md"
    transport = httpx2.ASGITransport(app=_app(workspace, data_dir, cache_dir))

    async with httpx2.AsyncClient(transport=transport, base_url="http://test") as client:
        create = await client.post(
            "/api/resources",
            json={"kind": "local-file", "location": str(fixture), "media_type": "text/markdown"},
        )
        resource_id = create.json()["id"]

    second_app = create_app(workspace, library_data_path=data_dir, library_cache_path=cache_dir)
    second_transport = httpx2.ASGITransport(app=second_app)

    async with httpx2.AsyncClient(transport=second_transport, base_url="http://test") as client:
        detail = await client.get(f"/api/resources/{resource_id}")

    assert detail.status_code == 200
    assert detail.json()["resource_id"] == resource_id


@pytest.mark.anyio
async def test_resource_content_endpoint(tmp_path: Path) -> None:
    workspace = tmp_path / "resource-course"
    workspace.mkdir()
    data_dir = tmp_path / "library-data"
    cache_dir = tmp_path / "library-cache"

    fixture = FIXTURES / "hello.md"
    original = fixture.read_bytes()
    transport = httpx2.ASGITransport(app=_app(workspace, data_dir, cache_dir))

    async with httpx2.AsyncClient(transport=transport, base_url="http://test") as client:
        create = await client.post(
            "/api/resources",
            json={"kind": "local-file", "location": str(fixture), "media_type": "text/markdown"},
        )
        resource_id = create.json()["id"]
        await client.post(f"/api/resources/{resource_id}/process")

        content_response = await client.get(f"/api/resources/{resource_id}/content")

    assert content_response.status_code == 200
    assert content_response.read() == original


@pytest.mark.anyio
async def test_missing_resource_returns_404(tmp_path: Path) -> None:
    workspace = tmp_path / "resource-course"
    workspace.mkdir()
    data_dir = tmp_path / "library-data"
    cache_dir = tmp_path / "library-cache"

    transport = httpx2.ASGITransport(app=_app(workspace, data_dir, cache_dir))

    async with httpx2.AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.get("/api/resources/nonexistent-id")

    assert response.status_code == 404
