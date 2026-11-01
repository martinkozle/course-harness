"""Admit Library Resources as Course Sources and resolve their pinned Source Versions."""

import logging
import os
import stat
import uuid
from collections.abc import Iterable
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING

from course_harness.core.canonical_mutation import CanonicalFile, capture_canonical_file
from course_harness.core.sources import (
    InvalidSourcesIndex,
    PinnedSource,
    Source,
    SourceImage,
    SourceImageResolver,
    SourceLocator,
    SourcesIndex,
    public_locator,
    read_sources_index,
    serialize_sources_index,
    with_missing_locators,
    write_sources_index,
)

if TYPE_CHECKING:
    from course_harness.resources import Resource

logger = logging.getLogger("course-harness")

MAX_EXTRACTED_EVIDENCE_BYTES = 8_000_000


def source_image_resolver(workspace: Path, data_dir: Path) -> SourceImageResolver:
    """Resolve image Source IDs against the Workspace's Sources as they are now."""
    try:
        index = read_sources_index(workspace) or SourcesIndex()
    except InvalidSourcesIndex:
        index = SourcesIndex()
    return pinned_image_resolver(index.sources, data_dir)


def pinned_image_resolver(sources: Iterable[PinnedSource], data_dir: Path) -> SourceImageResolver:
    """Resolve image Source IDs to the Source Versions pinned by ``sources``.

    Only Sources whose Resource is an image resolve; anything else, including a
    missing Snapshot, resolves to ``None`` so callers can degrade gracefully.
    """
    from course_harness.library import registry_path, snapshots_dir  # noqa: PLC0415
    from course_harness.resources import IMAGE_MEDIA_TYPES, read_library_index  # noqa: PLC0415

    media_types = {
        resource.id: resource.media_type
        for resource in read_library_index(registry_path(data_dir)).resources
    }
    images: dict[str, SourceImage] = {}
    for source in sources:
        media_type = media_types.get(source.resource_id)
        path = snapshots_dir(data_dir) / source.source_version_id
        if media_type is not None and media_type in IMAGE_MEDIA_TYPES and path.is_file():
            images[source.id] = SourceImage(
                source_id=source.id,
                source_version_id=source.source_version_id,
                media_type=media_type,
                path=path,
            )
    return images.get


def evidence_key(cache_dir: Path, source: PinnedSource) -> str:
    """The derived directory holding the representation a Source's Citations point into."""
    from course_harness.representations import evidence_key as key  # noqa: PLC0415

    return key(cache_dir, source.source_version_id, getattr(source, "representation", None))


def read_pinned_evidence_line_counts(cache_dir: Path, sources: Iterable[Source]) -> dict[str, int]:
    """Return line counts for safely readable extracted Evidence by Source Version.

    A missing, changed, oversized, non-regular, or non-UTF-8 derived file is
    deliberately absent from the result.  Release validation treats that as
    unresolvable Evidence for Citations that name coordinates.
    """
    flags = os.O_RDONLY | os.O_DIRECTORY | getattr(os, "O_NOFOLLOW", 0)
    try:
        derived_fd = os.open(cache_dir / "derived", flags)
    except OSError:
        return {}
    try:
        line_counts: dict[str, int] = {}
        for source in sources:
            content = _read_extracted_evidence(derived_fd, evidence_key(cache_dir, source))
            if content is not None:
                line_counts[source.source_version_id] = len(content.split("\n"))
        return line_counts
    finally:
        os.close(derived_fd)


def _read_extracted_evidence(derived_fd: int, key: str) -> str | None:
    """Read a bounded regular extracted file through no-follow directory FDs."""
    directory_flags = os.O_RDONLY | os.O_DIRECTORY | getattr(os, "O_NOFOLLOW", 0)
    file_flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)
    try:
        version_fd = os.open(key, directory_flags, dir_fd=derived_fd)
    except OSError:
        return None
    try:
        try:
            extracted_fd = os.open("extracted.md", file_flags, dir_fd=version_fd)
        except OSError:
            return None
        try:
            before = os.fstat(extracted_fd)
            if not stat.S_ISREG(before.st_mode) or before.st_size > MAX_EXTRACTED_EVIDENCE_BYTES:
                return None
            remaining = before.st_size
            chunks: list[bytes] = []
            while remaining:
                chunk = os.read(extracted_fd, min(65_536, remaining))
                if not chunk:
                    return None
                chunks.append(chunk)
                remaining -= len(chunk)
            if os.read(extracted_fd, 1):
                return None
            after = os.fstat(extracted_fd)
            if (before.st_dev, before.st_ino, before.st_size, before.st_mtime_ns) != (
                after.st_dev,
                after.st_ino,
                after.st_size,
                after.st_mtime_ns,
            ):
                return None
            try:
                return b"".join(chunks).decode("utf-8")
            except UnicodeDecodeError:
                return None
        finally:
            os.close(extracted_fd)
    finally:
        os.close(version_fd)


def admit_source(
    workspace: Path,
    data_dir: Path,
    cache_dir: Path,
    resource_id: str,
    label: str | None = None,
    *,
    expected: CanonicalFile | None = None,
) -> Source:
    from course_harness.resources import read_library_index  # noqa: PLC0415

    index = read_library_index(data_dir / "registry.json")
    resource = next((r for r in index.resources if r.id == resource_id), None)
    if resource is None:
        raise ValueError(f"Resource {resource_id} was not found in the Library.")

    from course_harness.library import get_resource_state  # noqa: PLC0415

    state = get_resource_state(data_dir, cache_dir, resource_id)
    if resource.snapshot_hash is None or state is None or state.status != "ready":
        raise ValueError(
            f"Resource {resource_id} has not been processed. Process it in the Library first."
        )

    expected = expected or capture_canonical_file(workspace, "sources.yaml")
    existing_index = read_sources_index(workspace) or SourcesIndex()
    if any(s.resource_id == resource_id for s in existing_index.sources):
        raise ValueError(f"Resource {resource_id} is already admitted as a Course Source.")

    representation = _pin_current_representation(data_dir, cache_dir, resource.snapshot_hash)
    source = Source(
        id=_generate_source_id(),
        resource_id=resource_id,
        source_version_id=resource.snapshot_hash,
        label=label or resource.location.rsplit("/", 1)[-1].rsplit("\\", 1)[-1][:200],
        admitted_at=datetime.now(UTC).isoformat(),
        representation=representation,
        locator=resource_locator(resource),
    )
    existing_index.sources.append(source)
    write_sources_index(workspace, existing_index, expected=expected)
    return source


def _pin_current_representation(data_dir: Path, cache_dir: Path, content_hash: str) -> str | None:
    """Pin the current representation of a document Snapshot so reprocessing keeps it."""
    from course_harness import representations  # noqa: PLC0415

    processor = representations.current_processor(cache_dir, content_hash)
    if processor is not None:
        representations.pin(data_dir, content_hash, processor)
    return processor


def _generate_source_id() -> str:
    return f"source-{uuid.uuid4().hex[:12]}"


def adopt_source_version(
    workspace: Path,
    data_dir: Path,
    cache_dir: Path,
    source_id: str,
    *,
    expected: CanonicalFile | None = None,
) -> Source:
    from course_harness.resources import read_library_index  # noqa: PLC0415

    expected = expected or capture_canonical_file(workspace, "sources.yaml")
    existing_index = read_sources_index(workspace)
    if existing_index is None or not existing_index.sources:
        raise ValueError("No Sources exist in this Workspace.")
    source = next((s for s in existing_index.sources if s.id == source_id), None)
    if source is None:
        raise ValueError(f"Source {source_id} was not found in this Workspace.")

    lib_index = read_library_index(data_dir / "registry.json")
    resource = next((r for r in lib_index.resources if r.id == source.resource_id), None)
    if resource is None or resource.snapshot_hash is None:
        raise ValueError("Underlying Resource is no longer available or has no Snapshot.")

    if resource.snapshot_hash == source.source_version_id:
        raise ValueError("Source is already at the latest version.")

    from course_harness.library import get_resource_state  # noqa: PLC0415

    state = get_resource_state(data_dir, cache_dir, resource.id)
    if state is None or state.status != "ready":
        raise ValueError("Latest Resource version has not been processed yet.")

    updated_source = source.model_copy(
        update={
            "source_version_id": resource.snapshot_hash,
            "representation": _pin_current_representation(
                data_dir, cache_dir, resource.snapshot_hash
            ),
            "locator": source.locator or resource_locator(resource),
        }
    )
    existing_index.sources = [
        updated_source if s.id == source_id else s for s in existing_index.sources
    ]
    write_sources_index(workspace, existing_index, expected=expected)
    return updated_source


def resource_locator(resource: Resource) -> SourceLocator | None:
    """The public locator of a Library Resource: only a remote Resource fetched directly has one.

    A local file or upload is named by its label alone, so no local path is ever written. A page
    a Connector captured, or one without a Snapshot of its own, may not be publicly readable.
    """
    if (
        resource.kind != "remote"
        or resource.captured_via is not None
        or resource.snapshot_hash is None
    ):
        return None
    return public_locator(resource.location)


def library_locators(data_dir: Path) -> dict[str, SourceLocator]:
    """Public locators of every Library Resource that has one, by Resource ID."""
    from course_harness.library import registry_path  # noqa: PLC0415
    from course_harness.resources import read_library_index  # noqa: PLC0415

    locators: dict[str, SourceLocator] = {}
    for resource in read_library_index(registry_path(data_dir)).resources:
        locator = resource_locator(resource)
        if locator is not None:
            locators[resource.id] = locator
    return locators


def backfill_source_locators(workspace: Path, data_dir: Path) -> bool:
    """Give existing Sources the public locator their Library Resource has, as app-authored state.

    Runs when a Workspace opens. It changes nothing unless Current State is valid and free of
    Workspace Drift, never replaces a locator already present, and writes nothing when no
    locator is missing; a skipped or failed backfill is retried the next time. Returns whether
    ``sources.yaml`` changed.
    """
    from course_harness.workspace_history import (  # noqa: PLC0415
        WorkspaceHistoryError,
        read_current_state,
        record_app_authored_entries,
    )

    try:
        index = read_sources_index(workspace)
        if index is None or all(source.locator is not None for source in index.sources):
            return False
        locators = library_locators(data_dir)
        # A cheap first look, before reading Current State, so a Workspace with nothing to fill
        # in costs no Git work; the result is computed again below from the guarded bytes.
        if with_missing_locators(index, locators) is None:
            return False
        state = read_current_state(workspace)
        if state.drift != "clean" or not state.validation.valid:
            return False
        # Read again under the captured precondition: reading Current State may have completed
        # an interrupted transaction, and the write must start from exactly these bytes.
        expected = capture_canonical_file(workspace, "sources.yaml")
        index = read_sources_index(workspace)
        updated = with_missing_locators(index, locators) if index is not None else None
        if updated is None:
            return False
        write_sources_index(workspace, updated, expected=expected)
        record_app_authored_entries(workspace, {"sources.yaml": serialize_sources_index(updated)})
    except (OSError, ValueError, WorkspaceHistoryError) as error:
        # Includes an invalid sources.yaml and a file that changed while it was being written.
        logger.warning("Source locators were not filled in: %s", error)
        return False
    return True
