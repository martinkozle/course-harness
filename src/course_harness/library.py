from pathlib import Path

import httpx2

from course_harness.product_connectors import product_connector_client
from course_harness.resources import (
    MAX_REMOTE_REDIRECTS,
    RemoteHostResolver,
    Resource,
    ResourceRegistrationRequest,
    ResourceState,
    Snapshot,
    create_snapshot,
    pin_remote_url,
    process_snapshot,
    read_library_index,
    redirect_target,
    register_resource,
    resolve_remote_host,
    update_resource_snapshot,
    write_library_index,
)
from course_harness.runtime_paths import RuntimePaths


def library_data_dir() -> Path:
    return RuntimePaths.platform().library_data_path


def library_cache_dir() -> Path:
    return RuntimePaths.platform().library_cache_path


def registry_path(data_dir: Path | None = None) -> Path:
    base = data_dir or library_data_dir()
    return base / "registry.json"


def snapshots_dir(data_dir: Path | None = None) -> Path:
    base = data_dir or library_data_dir()
    return base / "snapshots"


def derived_dir(cache_dir: Path | None = None) -> Path:
    base = cache_dir or library_cache_dir()
    return base / "derived"


def register_and_snapshot(
    data_dir: Path,
    cache_dir: Path,
    request: ResourceRegistrationRequest,
    content: bytes,
) -> tuple[Resource, Snapshot, ResourceState]:
    resource, snapshot = register_upload(data_dir, request, content)
    return resource, snapshot, process_upload(cache_dir, resource, snapshot, content)


def register_upload(
    data_dir: Path, request: ResourceRegistrationRequest, content: bytes
) -> tuple[Resource, Snapshot]:
    """Record an upload and its Snapshot; the only step that writes the registry.

    The Snapshot is recorded before processing, so a slow or interrupted conversion
    leaves an unprocessed Resource that can be reprocessed instead of losing the upload.
    """
    registry = registry_path(data_dir)
    resource = register_resource(registry, request)
    snapshot = create_snapshot(snapshots_dir(data_dir), resource.id, content)
    update_resource_snapshot(registry, resource.id, snapshot.content_hash)
    return resource, snapshot


def process_upload(
    cache_dir: Path, resource: Resource, snapshot: Snapshot, content: bytes
) -> ResourceState:
    """Convert and index a recorded Snapshot without touching the registry."""
    state = process_snapshot(cache_dir, snapshot.content_hash, resource.media_type, content)
    indexed = False
    if state.status == "ready":
        indexed = _index_if_ready(cache_dir, snapshot.content_hash)
    return ResourceState(
        resource_id=resource.id,
        kind=resource.kind,
        location=resource.location,
        media_type=resource.media_type,
        status=state.status,
        indexed=indexed,
        error=state.error,
        snapshot=snapshot,
    )


def register_remote_reference(
    data_dir: Path, url: str, media_type: str | None = None
) -> ResourceState:
    """Make a remote Resource visible before its content has been fetched."""
    resource = register_resource(
        registry_path(data_dir),
        ResourceRegistrationRequest(
            kind="remote",
            location=url,
            media_type=media_type or "application/octet-stream",
        ),
    )
    return ResourceState(
        resource_id=resource.id,
        kind=resource.kind,
        location=resource.location,
        media_type=resource.media_type,
        status="unprocessed",
    )


def save_remote_snapshot(
    data_dir: Path, resource_id: str, content: bytes, media_type: str
) -> Snapshot | None:
    """Attach fetched content to a Resource that still exists."""
    registry = registry_path(data_dir)
    index = read_library_index(registry)
    if not any(resource.id == resource_id for resource in index.resources):
        return None
    snapshot = create_snapshot(snapshots_dir(data_dir), resource_id, content)
    if update_resource_snapshot(registry, resource_id, snapshot.content_hash, media_type) is None:
        return None
    return snapshot


def save_connector_snapshot(
    data_dir: Path, resource_id: str, markdown: str, connector: str
) -> Snapshot | None:
    """Attach page text a Connector read, recording which Connector read it."""
    registry = registry_path(data_dir)
    index = read_library_index(registry)
    if not any(resource.id == resource_id for resource in index.resources):
        return None
    snapshot = create_snapshot(snapshots_dir(data_dir), resource_id, markdown.encode("utf-8"))
    updated = update_resource_snapshot(
        registry, resource_id, snapshot.content_hash, "text/markdown", captured_via=connector
    )
    return snapshot if updated is not None else None


def _index_if_ready(cache_dir: Path, content_hash: str) -> bool:
    from course_harness.search import index_resource  # noqa: PLC0415

    extracted = derived_dir(cache_dir) / content_hash / "extracted.md"
    if extracted.is_file():
        try:
            index_resource(cache_dir, content_hash, extracted.read_text(encoding="utf-8"))
            return True
        except OSError, UnicodeError:
            pass
    return False


def process_existing_resource(
    data_dir: Path,
    cache_dir: Path,
    resource_id: str,
    content: bytes,
) -> ResourceState | None:
    recorded = snapshot_existing_resource(data_dir, resource_id, content)
    if recorded is None:
        return None
    return process_upload(cache_dir, *recorded, content)


def snapshot_existing_resource(
    data_dir: Path, resource_id: str, content: bytes
) -> tuple[Resource, Snapshot] | None:
    index = read_library_index(registry_path(data_dir))
    resource = next((r for r in index.resources if r.id == resource_id), None)
    if resource is None:
        return None
    snapshot = create_snapshot(snapshots_dir(data_dir), resource.id, content)
    update_resource_snapshot(registry_path(data_dir), resource.id, snapshot.content_hash)
    return resource, snapshot


def remove_resource(
    data_dir: Path,
    cache_dir: Path,
    resource_id: str,
) -> bool:
    index = read_library_index(registry_path(data_dir))
    resource = next((r for r in index.resources if r.id == resource_id), None)
    if resource is None:
        return False

    if resource.snapshot_hash is not None:
        from course_harness.search import deindex_resource  # noqa: PLC0415

        deindex_resource(cache_dir, resource.snapshot_hash)

    index.resources = [r for r in index.resources if r.id != resource_id]
    write_library_index(registry_path(data_dir), index)
    return True


def list_resources_with_state(data_dir: Path, cache_dir: Path) -> list[ResourceState]:
    index = read_library_index(registry_path(data_dir))
    results: list[ResourceState] = []
    for resource in index.resources:
        snapshot = _read_snapshot(snapshots_dir(data_dir), resource)
        indexed = _is_indexed(cache_dir, resource.snapshot_hash)
        result = ResourceState(
            resource_id=resource.id,
            kind=resource.kind,
            location=resource.location,
            media_type=resource.media_type,
            status=(
                "ready"
                if snapshot is not None and _has_representation(cache_dir, resource.snapshot_hash)
                else "unprocessed"
            ),
            indexed=indexed,
            snapshot=snapshot,
            captured_via=resource.captured_via,
        )
        results.append(result)
    return results


def get_resource_state(data_dir: Path, cache_dir: Path, resource_id: str) -> ResourceState | None:
    index = read_library_index(registry_path(data_dir))
    resource = next((r for r in index.resources if r.id == resource_id), None)
    if resource is None:
        return None
    snapshot = _read_snapshot(snapshots_dir(data_dir), resource)
    indexed = _is_indexed(cache_dir, resource.snapshot_hash)
    return ResourceState(
        resource_id=resource.id,
        kind=resource.kind,
        location=resource.location,
        media_type=resource.media_type,
        status=(
            "ready"
            if snapshot is not None and _has_representation(cache_dir, resource.snapshot_hash)
            else "unprocessed"
        ),
        indexed=indexed,
        snapshot=snapshot,
        captured_via=resource.captured_via,
    )


def clear_processing_cache(cache_dir: Path) -> None:
    import shutil

    derived = derived_dir(cache_dir)
    if derived.is_dir():
        shutil.rmtree(derived)
    from course_harness.search import search_db_path  # noqa: PLC0415

    db_path = search_db_path(cache_dir)
    db_path.unlink(missing_ok=True)
    for wal_path in sorted(cache_dir.glob("fts/search.db-*")):
        wal_path.unlink(missing_ok=True)


def _read_snapshot(snapshots_base: Path, resource: Resource) -> Snapshot | None:
    if resource.snapshot_hash is None:
        return None
    snapshot_path = snapshots_base / resource.snapshot_hash
    if not snapshot_path.is_file():
        return None
    return Snapshot(
        resource_id=resource.id,
        content_hash=resource.snapshot_hash,
        byte_count=snapshot_path.stat().st_size,
        captured_at=resource.registered_at,
    )


def _has_representation(cache_dir: Path, content_hash: str | None) -> bool:
    return (
        content_hash is not None
        and (derived_dir(cache_dir) / content_hash / "extracted.md").is_file()
    )


def _is_indexed(cache_dir: Path, content_hash: str | None) -> bool:
    if content_hash is None:
        return False
    from contextlib import suppress

    from course_harness.search import search_db_path  # noqa: PLC0415

    extracted = derived_dir(cache_dir) / content_hash / "extracted.md"
    if not extracted.is_file():
        return False
    db_path = search_db_path(cache_dir)
    if not db_path.is_file():
        return False
    with suppress(Exception):
        import sqlite3

        conn = sqlite3.connect(str(db_path))
        try:
            cursor = conn.execute(
                "SELECT 1 FROM source_content WHERE content_hash = ? LIMIT 1",
                (content_hash,),
            )
            return cursor.fetchone() is not None
        finally:
            conn.close()
    return False


def reprocess_resource(
    data_dir: Path,
    cache_dir: Path,
    resource_id: str,
) -> ResourceState | None:
    index = read_library_index(registry_path(data_dir))
    resource = next((r for r in index.resources if r.id == resource_id), None)
    if resource is None or resource.snapshot_hash is None:
        return None

    from course_harness.search import deindex_resource  # noqa: PLC0415

    deindex_resource(cache_dir, resource.snapshot_hash)
    snapshot_path = snapshots_dir(data_dir) / resource.snapshot_hash
    if snapshot_path.is_file():
        content = snapshot_path.read_bytes()
    else:
        return None

    state = process_snapshot(cache_dir, resource.snapshot_hash, resource.media_type, content)
    indexed = False
    if state.status == "ready":
        indexed = _index_if_ready(cache_dir, resource.snapshot_hash)

    return ResourceState(
        resource_id=resource.id,
        kind=resource.kind,
        location=resource.location,
        media_type=resource.media_type,
        status=state.status,
        indexed=indexed,
        error=state.error,
        snapshot=Snapshot(
            resource_id=resource.id,
            content_hash=resource.snapshot_hash,
            byte_count=snapshot_path.stat().st_size,
            captured_at=resource.registered_at,
        ),
        captured_via=resource.captured_via,
    )


MAX_FETCH_BYTES = 100 * 1024 * 1024  # 100 MiB


async def fetch_remote_resource(
    url: str,
    media_type: str | None = None,
    *,
    http_client: httpx2.AsyncClient | None = None,
    host_resolver: RemoteHostResolver | None = None,
) -> tuple[bytes, str]:
    resolver = host_resolver or resolve_remote_host
    resolved_type = media_type or "application/octet-stream"
    if http_client is None:
        async with product_connector_client(timeout=30) as owned_client:
            return await _fetch_remote_resource(
                owned_client,
                url,
                resolved_type,
                media_type,
                resolver,
            )
    return await _fetch_remote_resource(
        http_client,
        url,
        resolved_type,
        media_type,
        resolver,
    )


async def _fetch_remote_resource(
    client: httpx2.AsyncClient,
    url: str,
    resolved_type: str,
    requested_media_type: str | None,
    host_resolver: RemoteHostResolver,
) -> tuple[bytes, str]:
    current_url = url
    for redirect_count in range(MAX_REMOTE_REDIRECTS + 1):
        target = await pin_remote_url(current_url, host_resolver=host_resolver)
        async with client.stream(
            "GET",
            target.url,
            headers={"Host": target.host_header},
            extensions={"sni_hostname": target.sni_hostname},
            follow_redirects=False,
        ) as response:
            if response.is_redirect:
                if redirect_count == MAX_REMOTE_REDIRECTS:
                    raise ValueError("Remote URL exceeded the redirect limit")
                current_url = redirect_target(current_url, response.headers.get("location"))
                continue
            response.raise_for_status()
            content_length = response.headers.get("content-length")
            if (
                content_length is not None
                and content_length.isdigit()
                and int(content_length) > MAX_FETCH_BYTES
            ):
                raise ValueError(
                    f"Remote content is {int(content_length)} bytes; "
                    f"maximum is {MAX_FETCH_BYTES} bytes."
                )
            header_type = response.headers.get("content-type", "").split(";")[0].strip()
            if not requested_media_type and header_type:
                resolved_type = header_type

            content = bytearray()
            async for chunk in response.aiter_bytes():
                if len(chunk) > MAX_FETCH_BYTES - len(content):
                    raise ValueError(
                        f"Fetched more than {MAX_FETCH_BYTES} bytes; maximum is "
                        f"{MAX_FETCH_BYTES} bytes."
                    )
                content.extend(chunk)
            return bytes(content), resolved_type
    raise AssertionError("Remote redirect loop did not return or raise")
