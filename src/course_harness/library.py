import os
import sys
from pathlib import Path

from course_harness.resources import (
    Resource,
    ResourceRegistrationRequest,
    ResourceState,
    Snapshot,
    content_hash,
    create_snapshot,
    process_snapshot,
    read_library_index,
    register_resource,
    update_resource_snapshot,
)


def library_data_dir() -> Path:
    if sys.platform == "win32":
        root = Path(os.environ.get("LOCALAPPDATA", Path.home() / "AppData" / "Local"))
        return root / "Course Harness" / "library"
    if sys.platform == "darwin":
        return Path.home() / "Library" / "Application Support" / "Course Harness" / "library"
    root = Path(os.environ.get("XDG_DATA_HOME", Path.home() / ".local" / "share"))
    return root / "course-harness" / "library"


def library_cache_dir() -> Path:
    if sys.platform == "win32":
        root = Path(os.environ.get("LOCALAPPDATA", Path.home() / "AppData" / "Local"))
        return root / "Course Harness" / "cache"
    if sys.platform == "darwin":
        return Path.home() / "Library" / "Caches" / "Course Harness"
    root = Path(os.environ.get("XDG_CACHE_HOME", Path.home() / ".cache"))
    return root / "course-harness"


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
    registry = registry_path(data_dir)
    resource = register_resource(registry, request)
    snapshot = create_snapshot(snapshots_dir(data_dir), resource.id, content)
    state = process_snapshot(cache_dir, snapshot.content_hash, resource.media_type, content)
    update_resource_snapshot(registry, resource.id, snapshot.content_hash)
    indexed = False
    if state.status == "ready":
        indexed = _index_if_ready(cache_dir, snapshot.content_hash)
    return (
        resource,
        snapshot,
        ResourceState(
            resource_id=resource.id,
            kind=resource.kind,
            location=resource.location,
            status=state.status,
            indexed=indexed,
            error=state.error,
            snapshot=snapshot,
        ),
    )


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
    index = read_library_index(registry_path(data_dir))
    resource = next((r for r in index.resources if r.id == resource_id), None)
    if resource is None:
        return None
    snapshot = create_snapshot(snapshots_dir(data_dir), resource.id, content)
    state = process_snapshot(cache_dir, snapshot.content_hash, resource.media_type, content)
    update_resource_snapshot(registry_path(data_dir), resource.id, snapshot.content_hash)
    indexed = False
    if state.status == "ready":
        indexed = _index_if_ready(cache_dir, snapshot.content_hash)
    return ResourceState(
        resource_id=resource.id,
        kind=resource.kind,
        location=resource.location,
        status=state.status,
        indexed=indexed,
        error=state.error,
        snapshot=snapshot,
    )


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
            status="ready" if snapshot is not None else "unprocessed",
            indexed=indexed,
            snapshot=snapshot,
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
        status="ready" if snapshot is not None else "unprocessed",
        indexed=indexed,
        snapshot=snapshot,
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
        status=state.status,
        indexed=indexed,
        error=state.error,
        snapshot=Snapshot(
            resource_id=resource.id,
            content_hash=resource.snapshot_hash,
            byte_count=snapshot_path.stat().st_size,
            captured_at=resource.registered_at,
        ),
    )


MAX_FETCH_BYTES = 100 * 1024 * 1024  # 100 MiB


async def fetch_remote_resource(
    url: str,
    media_type: str | None = None,
) -> tuple[bytes, str]:
    import httpx2

    resolved_type = media_type or "application/octet-stream"
    async with httpx2.AsyncClient(timeout=30, follow_redirects=True, max_redirects=5) as client:
        response = await client.get(url)
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
        if not media_type and header_type:
            resolved_type = header_type
        content = await response.aread()
        if len(content) > MAX_FETCH_BYTES:
            raise ValueError(f"Fetched {len(content)} bytes; maximum is {MAX_FETCH_BYTES} bytes.")
        return content, resolved_type


async def register_remote_resource(
    data_dir: Path,
    cache_dir: Path,
    url: str,
    media_type: str | None = None,
) -> ResourceState:
    content, resolved_type = await fetch_remote_resource(url, media_type)
    request = ResourceRegistrationRequest(
        kind="remote",
        location=url,
        media_type=resolved_type,
    )
    _, _, state = register_and_snapshot(data_dir, cache_dir, request, content)
    return state


async def refresh_remote_resource(
    data_dir: Path,
    cache_dir: Path,
    resource_id: str,
) -> ResourceState:
    index = read_library_index(registry_path(data_dir))
    resource = next((r for r in index.resources if r.id == resource_id), None)
    if resource is None:
        raise ValueError(f"Resource {resource_id} was not found in the Library.")
    if resource.kind != "remote":
        raise ValueError("Only remote resources can be refreshed.")

    content, _ = await fetch_remote_resource(resource.location, resource.media_type)
    new_hash = content_hash(content)

    if new_hash == resource.snapshot_hash:
        state = get_resource_state(data_dir, cache_dir, resource_id)
        assert state is not None
        return state

    snapshot = create_snapshot(snapshots_dir(data_dir), resource.id, content)
    state = process_snapshot(cache_dir, snapshot.content_hash, resource.media_type, content)
    update_resource_snapshot(registry_path(data_dir), resource.id, snapshot.content_hash)
    indexed = (
        _index_if_ready(cache_dir, snapshot.content_hash) if state.status == "ready" else False
    )
    return ResourceState(
        resource_id=resource.id,
        kind=resource.kind,
        location=resource.location,
        status=state.status,
        indexed=indexed,
        error=state.error,
        snapshot=snapshot,
    )
