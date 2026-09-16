from pathlib import Path
from unittest.mock import AsyncMock, patch

import httpx2
import pytest

from course_harness import app as app_module
from course_harness import resources as res
from course_harness.app import create_app
from course_harness.library import fetch_remote_resource

FIXTURES = Path(__file__).with_name("fixtures") / "resources"


async def _public_host_resolver(_hostname: str, _port: int) -> set[str]:
    return {"8.8.8.8"}


def _app(workspace: Path, data_dir: Path, cache_dir: Path):
    return create_app(
        workspace,
        library_data_path=data_dir,
        library_cache_path=cache_dir,
        remote_host_resolver=_public_host_resolver,
    )


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
@pytest.mark.parametrize("filename", ["../outside.md", "..\\outside.md", f"{'a' * 256}.md"])
async def test_upload_rejects_unsafe_filenames(tmp_path: Path, filename: str) -> None:
    workspace = tmp_path / "resource-course"
    workspace.mkdir()
    data_dir = tmp_path / "library-data"
    cache_dir = tmp_path / "library-cache"
    transport = httpx2.ASGITransport(app=_app(workspace, data_dir, cache_dir))

    async with httpx2.AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.post(
            "/api/resources/upload",
            files={"file": (filename, b"safe content", "text/markdown")},
        )

    assert response.status_code == 422
    assert response.json()["detail"] == "The upload filename is not safe."
    assert not data_dir.exists()


@pytest.mark.anyio
async def test_upload_rejects_oversized_documents(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    workspace = tmp_path / "resource-course"
    workspace.mkdir()
    data_dir = tmp_path / "library-data"
    cache_dir = tmp_path / "library-cache"
    monkeypatch.setattr(app_module, "MAX_UPLOAD_BYTES", 8)
    transport = httpx2.ASGITransport(app=_app(workspace, data_dir, cache_dir))

    async with httpx2.AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.post(
            "/api/resources/upload",
            files={"file": ("large.md", b"123456789", "text/markdown")},
        )

    assert response.status_code == 413
    assert response.json()["detail"] == "The upload exceeds the 8-byte limit."
    assert not data_dir.exists()


@pytest.mark.anyio
async def test_upload_rejects_large_multipart_request_before_parsing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    workspace = tmp_path / "resource-course"
    workspace.mkdir()
    data_dir = tmp_path / "library-data"
    cache_dir = tmp_path / "library-cache"
    monkeypatch.setattr(app_module, "MAX_UPLOAD_BYTES", 8)
    transport = httpx2.ASGITransport(app=_app(workspace, data_dir, cache_dir))

    async with httpx2.AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.post(
            "/api/resources/upload",
            files={"file": ("large.md", b"x" * (70 * 1024), "text/markdown")},
        )

    assert response.status_code == 413
    assert response.json()["detail"] == "The upload exceeds the 8-byte limit."
    assert not data_dir.exists()


@pytest.mark.anyio
async def test_upload_rejects_chunked_oversized_file_during_multipart_parsing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    workspace = tmp_path / "resource-course"
    workspace.mkdir()
    data_dir = tmp_path / "library-data"
    cache_dir = tmp_path / "library-cache"
    monkeypatch.setattr(app_module, "MAX_UPLOAD_BYTES", 8)
    transport = httpx2.ASGITransport(app=_app(workspace, data_dir, cache_dir))
    boundary = "resource-upload-boundary"

    async def body():
        yield (
            f"--{boundary}\r\n"
            'Content-Disposition: form-data; name="file"; filename="large.md"\r\n'
            "Content-Type: text/markdown\r\n\r\n"
        ).encode()
        yield b"12345"
        yield b"6789\r\n"
        yield f"--{boundary}--\r\n".encode()

    async with httpx2.AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.post(
            "/api/resources/upload",
            headers={"content-type": f"multipart/form-data; boundary={boundary}"},
            content=body(),
        )

    assert response.status_code == 413
    assert response.json()["detail"] == "The upload exceeds the 8-byte limit."
    assert not data_dir.exists()


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


@pytest.mark.anyio
async def test_register_remote_resource(tmp_path: Path) -> None:
    workspace = tmp_path / "remote-course"
    workspace.mkdir()
    data_dir = tmp_path / "library-data"
    cache_dir = tmp_path / "library-cache"

    app = _app(workspace, data_dir, cache_dir)
    transport = httpx2.ASGITransport(app=app)

    def mock_http(request: httpx2.Request) -> httpx2.Response:
        return httpx2.Response(
            200,
            headers={"content-type": "text/markdown"},
            text="# Remote content",
        )

    with patch(
        "course_harness.library.fetch_remote_resource",
        AsyncMock(return_value=(b"# Remote content", "text/markdown")),
    ):
        async with httpx2.AsyncClient(transport=transport, base_url="http://test") as client:
            response = await client.post(
                "/api/resources/remote",
                json={"url": "https://example.com/doc.md"},
            )

    assert response.status_code == 201
    body = response.json()
    assert body["kind"] == "remote"
    assert body["location"] == "https://example.com/doc.md"
    assert body["status"] == "ready"
    assert body["indexed"] is True


@pytest.mark.parametrize(
    "url",
    [
        "file:///etc/passwd",
        "ftp://example.com/resource",
        "https://user:password@example.com/resource",
    ],
)
def test_remote_fetch_request_rejects_unsafe_url_shapes(url: str) -> None:
    with pytest.raises(ValueError):
        res.RemoteFetchRequest(url=url)


@pytest.mark.anyio
async def test_remote_fetch_rejects_private_destinations_before_request() -> None:
    requested: list[str] = []

    async def private_resolver(_hostname: str, _port: int) -> set[str]:
        return {"127.0.0.1"}

    def transport(request: httpx2.Request) -> httpx2.Response:
        requested.append(str(request.url))
        return httpx2.Response(200, text="must not be read")

    async with httpx2.AsyncClient(transport=httpx2.MockTransport(transport)) as client:
        with pytest.raises(ValueError, match="disallowed network address"):
            await fetch_remote_resource(
                "https://example.com/resource",
                http_client=client,
                host_resolver=private_resolver,
            )

    assert requested == []


@pytest.mark.anyio
@pytest.mark.parametrize(
    "address",
    [
        "127.0.0.1",
        "10.0.0.1",
        "169.254.0.1",
        "224.0.0.1",
        "0.0.0.0",
        "240.0.0.1",
        "::1",
        "fd00::1",
        "fe80::1",
        "ff00::1",
        "::",
        "::ffff:127.0.0.1",
    ],
)
async def test_remote_url_rejects_each_disallowed_ip_address_class(address: str) -> None:
    async def resolver(_hostname: str, _port: int) -> set[str]:
        return {address}

    with pytest.raises(ValueError, match="disallowed network address"):
        await res.validate_remote_url("https://example.com/resource", host_resolver=resolver)


@pytest.mark.anyio
async def test_remote_fetch_revalidates_each_redirect_destination() -> None:
    requested: list[tuple[str, str, str]] = []

    async def resolver(hostname: str, _port: int) -> set[str]:
        return {"127.0.0.1"} if hostname == "private.example" else {"8.8.8.8"}

    def transport(request: httpx2.Request) -> httpx2.Response:
        requested.append(
            (str(request.url), request.headers["host"], request.extensions["sni_hostname"])
        )
        return httpx2.Response(302, headers={"location": "https://private.example/metadata"})

    async with httpx2.AsyncClient(transport=httpx2.MockTransport(transport)) as client:
        with pytest.raises(ValueError, match="disallowed network address"):
            await fetch_remote_resource(
                "https://public.example/resource",
                http_client=client,
                host_resolver=resolver,
            )

    assert requested == [("https://8.8.8.8/resource", "public.example", "public.example")]


@pytest.mark.anyio
async def test_remote_fetch_pins_the_validated_address() -> None:
    requested: list[tuple[str, str, str]] = []

    async def resolver(_hostname: str, _port: int) -> set[str]:
        return {"8.8.8.8"}

    def transport(request: httpx2.Request) -> httpx2.Response:
        requested.append(
            (str(request.url), request.headers["host"], request.extensions["sni_hostname"])
        )
        return httpx2.Response(200, headers={"content-type": "text/markdown"}, text="# Remote")

    async with httpx2.AsyncClient(transport=httpx2.MockTransport(transport)) as client:
        content, media_type = await fetch_remote_resource(
            "https://example.com/resource",
            http_client=client,
            host_resolver=resolver,
        )

    assert content == b"# Remote"
    assert media_type == "text/markdown"
    assert requested == [("https://8.8.8.8/resource", "example.com", "example.com")]


@pytest.mark.anyio
async def test_remote_fetch_stops_streaming_when_chunked_content_exceeds_limit(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr("course_harness.library.MAX_FETCH_BYTES", 8)
    chunks_read: list[bytes] = []

    class ChunkedBody(httpx2.AsyncByteStream):
        async def __aiter__(self):
            for chunk in (b"12345", b"6789", b"must-not-be-read"):
                chunks_read.append(chunk)
                yield chunk

        async def aclose(self) -> None:
            pass

    async def resolver(_hostname: str, _port: int) -> set[str]:
        return {"8.8.8.8"}

    def transport(_request: httpx2.Request) -> httpx2.Response:
        return httpx2.Response(200, stream=ChunkedBody())

    async with httpx2.AsyncClient(transport=httpx2.MockTransport(transport)) as client:
        with pytest.raises(ValueError, match="maximum is 8 bytes"):
            await fetch_remote_resource(
                "https://example.com/resource",
                http_client=client,
                host_resolver=resolver,
            )

    assert chunks_read == [b"12345", b"6789"]


@pytest.mark.anyio
async def test_remote_fetch_uses_the_default_resolver_and_pins_its_result(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    requested: list[str] = []

    def getaddrinfo(*_args: object, **_kwargs: object) -> list[tuple[object, ...]]:
        return [(0, 0, 0, "", ("8.8.4.4", 443))]

    def transport(request: httpx2.Request) -> httpx2.Response:
        requested.append(str(request.url))
        return httpx2.Response(200, text="public")

    monkeypatch.setattr(res.socket, "getaddrinfo", getaddrinfo)
    async with httpx2.AsyncClient(transport=httpx2.MockTransport(transport)) as client:
        content, _ = await fetch_remote_resource(
            "https://example.com/resource",
            http_client=client,
        )

    assert content == b"public"
    assert requested == ["https://8.8.4.4/resource"]


@pytest.mark.anyio
async def test_snapshot_history_tracks_versions(tmp_path: Path) -> None:
    workspace = tmp_path / "snap-course"
    workspace.mkdir()
    data_dir = tmp_path / "library-data"
    cache_dir = tmp_path / "library-cache"

    app = _app(workspace, data_dir, cache_dir)
    transport = httpx2.ASGITransport(app=app)

    content_v1 = b"# Version one"
    content_v2 = b"# Version two"

    async with httpx2.AsyncClient(transport=transport, base_url="http://test") as client:
        with patch(
            "course_harness.library.fetch_remote_resource",
            AsyncMock(return_value=(content_v1, "text/markdown")),
        ):
            create = await client.post(
                "/api/resources/remote",
                json={"url": "https://example.com/evolving.md"},
            )
            assert create.status_code == 201
            resource_id = create.json()["resource_id"]

        with patch(
            "course_harness.library.fetch_remote_resource",
            AsyncMock(return_value=(content_v2, "text/markdown")),
        ):
            await client.post(f"/api/resources/{resource_id}/refresh")

        index = res.read_library_index(data_dir / "registry.json")
        resource = next((r for r in index.resources if r.id == resource_id), None)
        assert resource is not None
        assert len(resource.snapshot_history) == 1
        assert resource.snapshot_history[0] != resource.snapshot_hash


@pytest.mark.anyio
async def test_refresh_non_remote_rejected(tmp_path: Path) -> None:
    workspace = tmp_path / "local-course"
    workspace.mkdir()
    data_dir = tmp_path / "library-data"
    cache_dir = tmp_path / "library-cache"

    fixture = FIXTURES / "hello.md"
    app = _app(workspace, data_dir, cache_dir)
    transport = httpx2.ASGITransport(app=app)

    async with httpx2.AsyncClient(transport=transport, base_url="http://test") as client:
        create = await client.post(
            "/api/resources",
            json={
                "kind": "local-file",
                "location": str(fixture),
                "media_type": "text/markdown",
            },
        )
        resource_id = create.json()["id"]

        refresh = await client.post(f"/api/resources/{resource_id}/refresh")

    assert refresh.status_code == 422
    assert "remote" in (refresh.json().get("detail") or "").lower()
