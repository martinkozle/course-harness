import os
import stat
import uuid
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Literal, Protocol

import yaml
from pydantic import BaseModel, ConfigDict, Field

from course_harness.canonical_mutation import (
    CanonicalFile,
    apply_canonical_mutation,
    capture_canonical_file,
)

MAX_EXTRACTED_EVIDENCE_BYTES = 8_000_000


class Source(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str = Field(pattern=r"^source-[0-9a-f]{12}$")
    resource_id: str = Field(pattern=r"^resource-[0-9a-f]{12}$")
    source_version_id: str = Field(pattern=r"^[0-9a-f]{64}$")
    label: str = Field(min_length=1, max_length=200)
    admitted_at: str


class SourcesIndex(BaseModel):
    model_config = ConfigDict(extra="forbid")

    version: Literal[1] = 1
    sources: list[Source] = Field(default_factory=list)


class SourceAdmissionRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    resource_id: str = Field(pattern=r"^resource-[0-9a-f]{12}$")
    label: str | None = Field(default=None, min_length=1, max_length=200)


class InvalidSourcesIndex(ValueError):
    """Canonical Sources state exists but does not satisfy the schema."""


@dataclass(frozen=True)
class SourceImage:
    """The pinned Source Version of an image Source, ready to place on a Slide."""

    source_id: str
    source_version_id: str
    media_type: str
    path: Path


SourceImageResolver = Callable[[str], SourceImage | None]


class PinnedSource(Protocol):
    @property
    def id(self) -> str: ...
    @property
    def resource_id(self) -> str: ...
    @property
    def source_version_id(self) -> str: ...


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
        for source_version_id in {source.source_version_id for source in sources}:
            content = _read_extracted_evidence(derived_fd, source_version_id)
            if content is not None:
                line_counts[source_version_id] = len(content.split("\n"))
        return line_counts
    finally:
        os.close(derived_fd)


def _read_extracted_evidence(derived_fd: int, source_version_id: str) -> str | None:
    """Read a bounded regular extracted file through no-follow directory FDs."""
    directory_flags = os.O_RDONLY | os.O_DIRECTORY | getattr(os, "O_NOFOLLOW", 0)
    file_flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)
    try:
        version_fd = os.open(source_version_id, directory_flags, dir_fd=derived_fd)
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


def read_sources_index(workspace: Path) -> SourcesIndex | None:
    sources_path = workspace / "sources.yaml"
    if not sources_path.exists():
        return None
    try:
        payload = yaml.safe_load(sources_path.read_text(encoding="utf-8"))
        return SourcesIndex.model_validate(payload)
    except (OSError, UnicodeError, yaml.YAMLError, ValueError) as error:
        raise InvalidSourcesIndex(str(error)) from error


def write_sources_index(
    workspace: Path, index: SourcesIndex, *, expected: CanonicalFile | None = None
) -> None:
    if expected is not None:
        apply_canonical_mutation(
            workspace,
            expected={"sources.yaml": expected},
            updates={"sources.yaml": serialize_sources_index(index)},
        )
        return
    sources_path = workspace / "sources.yaml"
    temporary_path = workspace / f".sources-{uuid.uuid4().hex}.yaml.tmp"
    serialized = serialize_sources_index(index).decode("utf-8")
    with temporary_path.open("x", encoding="utf-8") as stream:
        stream.write(serialized)
        stream.flush()
        os.fsync(stream.fileno())
    try:
        temporary_path.replace(sources_path)
    finally:
        temporary_path.unlink(missing_ok=True)


def serialize_sources_index(index: SourcesIndex) -> bytes:
    """Return the exact canonical bytes written for a Sources index."""
    return yaml.safe_dump(
        index.model_dump(mode="json"), allow_unicode=True, sort_keys=False, width=100
    ).encode("utf-8")


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

    source = Source(
        id=_generate_source_id(),
        resource_id=resource_id,
        source_version_id=resource.snapshot_hash,
        label=label or resource.location.rsplit("/", 1)[-1].rsplit("\\", 1)[-1][:200],
        admitted_at=datetime.now(UTC).isoformat(),
    )
    existing_index.sources.append(source)
    write_sources_index(workspace, existing_index, expected=expected)
    return source


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

    updated_source = source.model_copy(update={"source_version_id": resource.snapshot_hash})
    existing_index.sources = [
        updated_source if s.id == source_id else s for s in existing_index.sources
    ]
    write_sources_index(workspace, existing_index, expected=expected)
    return updated_source
