"""Canonical Course Sources: the Source schema and the ``sources.yaml`` index."""

import os
import uuid
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Literal, Protocol

import yaml
from pydantic import BaseModel, ConfigDict, Field

from course_harness.core.canonical_mutation import (
    CanonicalFile,
    apply_canonical_mutation,
)
from course_harness.core.canonical_yaml import load_canonical_yaml


class Source(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str = Field(pattern=r"^source-[0-9a-f]{12}$")
    resource_id: str = Field(pattern=r"^resource-[0-9a-f]{12}$")
    source_version_id: str = Field(pattern=r"^[0-9a-f]{64}$")
    label: str = Field(min_length=1, max_length=200)
    admitted_at: str
    # The Document Processor whose representation Citations of this Source point into.
    representation: str | None = Field(default=None, exclude_if=lambda value: value is None)


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


def read_sources_index(workspace: Path) -> SourcesIndex | None:
    sources_path = workspace / "sources.yaml"
    if not sources_path.exists():
        return None
    try:
        payload = load_canonical_yaml(sources_path.read_text(encoding="utf-8"))
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
