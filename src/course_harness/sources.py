import os
import uuid
from datetime import UTC, datetime
from pathlib import Path
from typing import Literal

import yaml
from pydantic import BaseModel, ConfigDict, Field


class Source(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str = Field(pattern=r"^source-[0-9a-f]{12}$")
    resource_id: str = Field(pattern=r"^resource-[0-9a-f]{12}$")
    source_version_id: str = Field(min_length=64, max_length=64)
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


def read_sources_index(workspace: Path) -> SourcesIndex | None:
    sources_path = workspace / "sources.yaml"
    if not sources_path.exists():
        return None
    try:
        payload = yaml.safe_load(sources_path.read_text(encoding="utf-8"))
        return SourcesIndex.model_validate(payload)
    except (OSError, UnicodeError, yaml.YAMLError, ValueError) as error:
        raise InvalidSourcesIndex(str(error)) from error


def write_sources_index(workspace: Path, index: SourcesIndex) -> None:
    sources_path = workspace / "sources.yaml"
    temporary_path = workspace / f".sources-{uuid.uuid4().hex}.yaml.tmp"
    serialized = yaml.safe_dump(
        index.model_dump(mode="json"), allow_unicode=True, sort_keys=False, width=100
    )
    with temporary_path.open("x", encoding="utf-8") as stream:
        stream.write(serialized)
        stream.flush()
        os.fsync(stream.fileno())
    try:
        temporary_path.replace(sources_path)
    finally:
        temporary_path.unlink(missing_ok=True)


def admit_source(
    workspace: Path,
    data_dir: Path,
    resource_id: str,
    label: str | None = None,
    *,
    cache_dir: Path | None = None,
) -> Source:
    from course_harness.library import derived_dir  # noqa: PLC0415
    from course_harness.resources import read_library_index  # noqa: PLC0415

    index = read_library_index(data_dir / "registry.json")
    resource = next((r for r in index.resources if r.id == resource_id), None)
    if resource is None:
        raise ValueError(f"Resource {resource_id} was not found in the Library.")

    if resource.snapshot_hash is None:
        raise ValueError(
            f"Resource {resource_id} has not been processed. Process it in the Library first."
        )

    effective_cache = cache_dir or derived_dir(None).parent
    extracted = derived_dir(effective_cache) / resource.snapshot_hash / "extracted.md"
    if not extracted.is_file():
        raise ValueError(
            f"Resource {resource_id} has no searchable content. Process it in the Library first."
        )

    existing_index = read_sources_index(workspace) or SourcesIndex()
    if any(s.resource_id == resource_id for s in existing_index.sources):
        raise ValueError(f"Resource {resource_id} is already admitted as a Course Source.")

    source = Source(
        id=_generate_source_id(),
        resource_id=resource_id,
        source_version_id=resource.snapshot_hash,
        label=label or resource.location.rsplit("/", 1)[-1].rsplit("\\", 1)[-1],
        admitted_at=datetime.now(UTC).isoformat(),
    )
    existing_index.sources.append(source)
    write_sources_index(workspace, existing_index)
    return source


def _generate_source_id() -> str:
    return f"source-{uuid.uuid4().hex[:12]}"
