"""Publish and inspect immutable Course Releases."""

from __future__ import annotations

import hashlib
import io
import json
import os
import re
import selectors
import stat
import subprocess
import tempfile
import time
import zipfile
from contextlib import suppress
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Literal

import yaml
from pydantic import BaseModel, ConfigDict, Field, ValidationError

from course_harness.course_plan import CoursePlan, InvalidCoursePlan, read_course_plan
from course_harness.export import ExportError, export_presentation
from course_harness.presentation import InvalidPresentation, Presentation, read_presentation
from course_harness.release_validation import (
    InvalidWaiver,
    ReleaseSelection,
    ReleaseValidationResult,
    Waiver,
    validate_release,
)
from course_harness.sources import (
    InvalidSourcesIndex,
    Source,
    SourcesIndex,
    read_sources_index,
)
from course_harness.template_profiles import (
    BUILTIN_DEFAULT_ID,
    TemplateProfile,
    profile_dir,
    resolve_profile,
)
from course_harness.workspace_history import list_revisions, read_current_state

GIT_TIMEOUT_SECONDS = 5
MAX_GIT_OUTPUT_BYTES = 1_000_000
MAX_MANIFEST_BYTES = 512_000
MAX_ARTIFACT_BYTES = 64_000_000
MAX_RELEASE_BYTES = 256_000_000
MAX_RELEASES = 10_000
MAX_CANONICAL_BLOB_BYTES = 8_000_000
SLUG_PATTERN = re.compile(r"[a-z0-9](?:[a-z0-9-]{0,62}[a-z0-9])?\Z")
OID_PATTERN = re.compile(r"(?:[0-9a-f]{40}|[0-9a-f]{64})\Z")


class ReleaseError(RuntimeError):
    """A Course Release cannot be published or inspected safely."""


class ReleaseConflict(ReleaseError):
    """The immutable Release name or storage identity already exists."""


class ReleaseValidationError(ReleaseError):
    """A Course Author can correct the Release request or canonical Course state."""


class PublishReleaseRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    slug: str = Field(
        min_length=1, max_length=64, pattern=r"^[a-z0-9](?:[a-z0-9-]{0,62}[a-z0-9])?$"
    )
    name: str = Field(min_length=1, max_length=200)
    selection: ReleaseSelection
    waivers: list[Waiver] = Field(default_factory=list, max_length=1_000)


class ReleaseSourcePin(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str = Field(pattern=r"^source-[0-9a-f]{12}$")
    resource_id: str = Field(pattern=r"^resource-[0-9a-f]{12}$")
    source_version_id: str = Field(pattern=r"^[0-9a-f]{64}$")
    label: str = Field(min_length=1, max_length=200)


class ReleaseTemplatePin(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str = Field(pattern=r"^(?:tpl-[0-9a-f]{12}|_builtin-default)$")
    version: int = Field(ge=1)
    profile_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    template_sha256: str | None = Field(default=None, pattern=r"^[0-9a-f]{64}$")
    template_storage_path: Literal["inputs/template.pptx"] | None = None
    definition: TemplateProfile


class ReleaseArtifact(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str = Field(pattern=r"^presentation-[0-9a-f]{12}$")
    lecture_id: str = Field(pattern=r"^lecture-[0-9a-f]{12}$")
    media_type: Literal[
        "application/vnd.openxmlformats-officedocument.presentationml.presentation"
    ] = "application/vnd.openxmlformats-officedocument.presentationml.presentation"
    storage_path: str = Field(pattern=r"^artifacts/presentation-[0-9a-f]{12}\.pptx$", max_length=48)
    size: int = Field(ge=0, le=MAX_ARTIFACT_BYTES)
    sha256: str = Field(pattern=r"^[0-9a-f]{64}$")


class CourseRelease(BaseModel):
    """The canonical, bounded manifest stored in an annotated Git tag."""

    model_config = ConfigDict(extra="forbid")

    schema_version: Literal[1] = 1
    slug: str = Field(
        min_length=1, max_length=64, pattern=r"^[a-z0-9](?:[a-z0-9-]{0,62}[a-z0-9])?$"
    )
    name: str = Field(min_length=1, max_length=200)
    tag: str = Field(min_length=16, max_length=79, pattern=r"^course-release/[a-z0-9-]+$")
    course_id: str = Field(pattern=r"^course-[0-9a-f]{12}$")
    revision_id: str = Field(pattern=r"^revision-(?:[0-9a-f]{40}|[0-9a-f]{64})$")
    commit_oid: str = Field(pattern=r"^(?:[0-9a-f]{40}|[0-9a-f]{64})$")
    published_at: str = Field(min_length=20, max_length=64)
    included_lecture_ids: list[str] = Field(min_length=1, max_length=1_000)
    planned_unpublished_lecture_ids: list[str] = Field(default_factory=list, max_length=1_000)
    sources: list[ReleaseSourcePin] = Field(default_factory=list, max_length=2_000)
    template_profile: ReleaseTemplatePin
    validation: ReleaseValidationResult
    artifacts: list[ReleaseArtifact] = Field(default_factory=list, max_length=1_000)


def publish_release(
    *,
    workspace: Path,
    release_data_root: Path,
    templates_data_root: Path,
    request: PublishReleaseRequest,
) -> CourseRelease:
    """Validate, export, persist, and atomically name one immutable Release."""
    if len(request.selection.lecture_ids) > 1_000 or len(request.selection.artifact_ids) > 1_000:
        raise ReleaseError("Release selection is too large")
    state = read_current_state(workspace)
    if not state.clean or not state.validation.valid or state.drift != "clean":
        raise ReleaseError(
            "Publication requires valid, provenance-clean Current State at a clean Course Revision"
        )
    revision = _head_oid(workspace)
    try:
        plan = read_course_plan(workspace)
    except InvalidCoursePlan as error:
        raise ReleaseValidationError("Course Plan is invalid") from error
    if plan is None:
        raise ReleaseValidationError("A Course Plan is required for publication")
    try:
        sources = read_sources_index(workspace) or SourcesIndex()
    except InvalidSourcesIndex as error:
        raise ReleaseValidationError("Sources index is invalid") from error
    presentations = []
    for artifact_id in request.selection.artifact_ids:
        try:
            presentation = read_presentation(workspace, artifact_id)
        except InvalidPresentation as error:
            raise ReleaseValidationError(
                f"Selected Presentation {artifact_id} is invalid"
            ) from error
        if presentation is not None:
            presentations.append(presentation)
    try:
        validation = validate_release(
            plan=plan,
            presentations=presentations,
            sources=sources,
            selection=request.selection,
            waivers=request.waivers,
        )
    except InvalidWaiver as error:
        raise ReleaseValidationError(str(error)) from error
    if not validation.can_publish:
        raise ReleaseValidationError(
            "Release validation must pass or have explicit warning Waivers"
        )

    try:
        profile = resolve_profile(
            templates_data_root, plan.template_profile_id, plan.template_profile_version
        )
    except (OSError, UnicodeError, ValueError) as error:
        raise ReleaseError("The pinned Template Profile is unavailable or invalid") from error
    profile_bytes = _canonical_json(profile.model_dump(mode="json"))
    template_bytes = _read_template_bytes(templates_data_root, profile)
    template_pin = ReleaseTemplatePin(
        id=profile.id,
        version=profile.version,
        profile_sha256=_sha256(profile_bytes),
        template_sha256=_sha256(template_bytes) if template_bytes is not None else None,
        template_storage_path="inputs/template.pptx" if template_bytes is not None else None,
        definition=profile,
    )

    exports: list[tuple[ReleaseArtifact, bytes]] = []
    total_bytes = len(template_bytes or b"")
    try:
        generated = _export_presentations(presentations, profile, template_bytes)
    except (OSError, ValueError, ExportError) as error:
        raise ReleaseError("A selected Presentation could not be exported") from error
    for presentation, content in generated:
        total_bytes += len(content)
        if len(content) > MAX_ARTIFACT_BYTES or total_bytes > MAX_RELEASE_BYTES:
            raise ReleaseError("Generated Release artifacts are too large")
        relative_path = f"artifacts/{presentation.id}.pptx"
        exports.append(
            (
                ReleaseArtifact(
                    id=presentation.id,
                    lecture_id=presentation.lecture_id,
                    storage_path=relative_path,
                    size=len(content),
                    sha256=_sha256(content),
                ),
                content,
            )
        )

    # A concurrent edit cannot be accidentally published under the Revision we inspected.
    final_state = read_current_state(workspace)
    if (
        not final_state.clean
        or not final_state.validation.valid
        or final_state.drift != "clean"
        or _head_oid(workspace) != revision
    ):
        raise ReleaseError("Current State changed while the Release was being prepared")

    tag = f"course-release/{request.slug}"
    release = CourseRelease(
        slug=request.slug,
        name=request.name,
        tag=tag,
        course_id=plan.id,
        revision_id=f"revision-{revision}",
        commit_oid=revision,
        published_at=datetime.now(UTC).isoformat(),
        included_lecture_ids=list(request.selection.lecture_ids),
        planned_unpublished_lecture_ids=[
            lecture.id
            for lecture in plan.lectures
            if lecture.id not in request.selection.lecture_ids
        ],
        sources=[_source_pin(source) for source in sources.sources],
        template_profile=template_pin,
        validation=validation,
        artifacts=[artifact for artifact, _content in exports],
    )
    _verify_release_manifest(workspace, release)
    manifest = _canonical_json(release.model_dump(mode="json"))
    if len(manifest) > MAX_MANIFEST_BYTES:
        raise ReleaseError("Release manifest is too large")
    if _tag_exists(workspace, tag):
        raise ReleaseConflict(f"Release {request.slug} already exists")

    storage = _create_release_directory(
        release_data_root, plan.id, request.slug, workspace=workspace
    )
    try:
        if template_bytes is not None:
            inputs_fd = _create_child_directory(storage.release_fd, "inputs")
            try:
                _write_new_file_at(inputs_fd, "template.pptx", template_bytes)
            finally:
                os.close(inputs_fd)
        if exports:
            artifacts_fd = _create_child_directory(storage.release_fd, "artifacts")
            try:
                for artifact, content in exports:
                    _write_new_file_at(artifacts_fd, f"{artifact.id}.pptx", content)
            finally:
                os.close(artifacts_fd)
        os.fsync(storage.release_fd)
        _create_annotated_tag(workspace, tag, revision, manifest)
    except Exception:
        _cleanup_unpublished_release(
            storage,
            has_template=template_bytes is not None,
            artifact_ids=[artifact.id for artifact, _content in exports],
        )
        raise
    else:
        storage.close(sync=False)
    return release


def list_releases(workspace: Path) -> list[CourseRelease]:
    """List valid immutable Release manifests, newest tag first."""
    list_revisions(workspace)
    result = _git(
        workspace, "for-each-ref", "--format=%(refname:strip=2)", "refs/tags/course-release"
    )
    names = [line for line in result.decode("utf-8").splitlines() if line]
    if len(names) > MAX_RELEASES:
        raise ReleaseError("Course Workspace has too many Releases")
    slugs = []
    for name in names:
        prefix = "course-release/"
        slug = name.removeprefix(prefix)
        if not name.startswith(prefix) or not SLUG_PATTERN.fullmatch(slug):
            raise ReleaseError("Course Workspace contains an unsafe Release tag")
        slugs.append(slug)
    releases = [read_release(workspace, slug) for slug in slugs]
    return sorted(
        [release for release in releases if release is not None],
        key=lambda release: release.published_at,
        reverse=True,
    )


def read_release(workspace: Path, slug: str) -> CourseRelease | None:
    """Read and verify one Release manifest from its annotated tag."""
    if not SLUG_PATTERN.fullmatch(slug):
        raise ReleaseError("Release slug is invalid")
    list_revisions(workspace)
    tag = f"course-release/{slug}"
    if not _tag_exists(workspace, tag):
        return None
    raw = _git(workspace, "cat-file", "tag", f"refs/tags/{tag}")
    try:
        header, message = raw.split(b"\n\n", 1)
        headers = dict(line.split(b" ", 1) for line in header.splitlines() if b" " in line)
        revision = headers[b"object"].decode("ascii")
        internal_tag = headers[b"tag"].decode("utf-8")
        if headers.get(b"type") != b"commit" or not OID_PATTERN.fullmatch(revision):
            raise ValueError
        release = CourseRelease.model_validate_json(message)
    except (KeyError, UnicodeError, ValidationError, ValueError) as error:
        raise ReleaseError("Release tag does not contain a valid manifest") from error
    if (
        internal_tag != tag
        or release.slug != slug
        or release.tag != tag
        or release.revision_id != f"revision-{revision}"
        or release.commit_oid != revision
    ):
        raise ReleaseError("Release manifest identity does not match its annotated tag")
    canonical = _canonical_json(release.model_dump(mode="json"))
    if len(canonical) > MAX_MANIFEST_BYTES or message != canonical:
        raise ReleaseError("Release tag manifest is not exact canonical JSON")
    _verify_release_manifest(workspace, release)
    return release


def read_release_artifact(
    *, workspace: Path, release_data_root: Path, slug: str, artifact_id: str
) -> bytes:
    """Read one immutable Artifact while verifying its recorded identity and bytes."""
    release = read_release(workspace, slug)
    if release is None:
        raise ReleaseError("Course Release was not found")
    artifact = next((item for item in release.artifacts if item.id == artifact_id), None)
    if artifact is None:
        raise ReleaseError("Release Artifact was not found")
    content = _read_release_file(
        release_data_root,
        release.course_id,
        release.slug,
        "artifacts",
        f"{artifact.id}.pptx",
        max_bytes=MAX_ARTIFACT_BYTES,
    )
    if len(content) != artifact.size or _sha256(content) != artifact.sha256:
        raise ReleaseError("Stored Release Artifact does not match its manifest")
    return content


def regenerate_release_artifact(
    *, workspace: Path, release_data_root: Path, slug: str, artifact_id: str
) -> bytes:
    """Regenerate solely from the tagged Revision and manifest-pinned template inputs."""
    release = read_release(workspace, slug)
    if release is None:
        raise ReleaseError("Course Release was not found")
    artifact = next((item for item in release.artifacts if item.id == artifact_id), None)
    if artifact is None:
        raise ReleaseError("Release Artifact was not found")
    presentation = _tagged_presentation(workspace, release.commit_oid, artifact.id)
    template_bytes = None
    if release.template_profile.id != BUILTIN_DEFAULT_ID:
        template_bytes = _read_release_file(
            release_data_root,
            release.course_id,
            release.slug,
            "inputs",
            "template.pptx",
            max_bytes=MAX_ARTIFACT_BYTES,
        )
        if _sha256(template_bytes) != release.template_profile.template_sha256:
            raise ReleaseError("Stored Release template does not match its manifest")
    try:
        regenerated = _export_presentations(
            [presentation], release.template_profile.definition, template_bytes
        )[0][1]
    except (OSError, ValueError, ExportError) as error:
        raise ReleaseError("Release Artifact could not be regenerated") from error
    if len(regenerated) != artifact.size or _sha256(regenerated) != artifact.sha256:
        raise ReleaseError("Regenerated Release Artifact differs from the published Artifact")
    return regenerated


def _verify_release_manifest(workspace: Path, release: CourseRelease) -> None:
    try:
        datetime.fromisoformat(release.published_at)
    except ValueError as error:
        raise ReleaseError("Release publication time is invalid") from error
    selection = release.validation.selection
    if (
        release.tag != f"course-release/{release.slug}"
        or release.revision_id != f"revision-{release.commit_oid}"
        or release.included_lecture_ids != selection.lecture_ids
    ):
        raise ReleaseError("Release manifest contains inconsistent identities")
    artifact_ids = [artifact.id for artifact in release.artifacts]
    if artifact_ids != selection.artifact_ids or len(artifact_ids) != len(set(artifact_ids)):
        raise ReleaseError("Release manifest Artifact selection is inconsistent")
    if any(
        artifact.storage_path != f"artifacts/{artifact.id}.pptx"
        or artifact.lecture_id not in release.included_lecture_ids
        for artifact in release.artifacts
    ):
        raise ReleaseError("Release manifest contains an invalid Artifact path or Lecture")

    plan = _tagged_plan(workspace, release.commit_oid)
    if plan.id != release.course_id:
        raise ReleaseError("Release manifest does not match its tagged Course")
    planned_ids = [lecture.id for lecture in plan.lectures]
    if any(lecture_id not in planned_ids for lecture_id in release.included_lecture_ids):
        raise ReleaseError("Release manifest includes an unknown Lecture")
    expected_unpublished = [
        lecture_id for lecture_id in planned_ids if lecture_id not in release.included_lecture_ids
    ]
    if release.planned_unpublished_lecture_ids != expected_unpublished:
        raise ReleaseError("Release manifest planned coverage is inconsistent")

    sources = _tagged_sources(workspace, release.commit_oid)
    if release.sources != [_source_pin(source) for source in sources.sources]:
        raise ReleaseError("Release manifest Source Versions do not match its tagged Revision")
    source_ids = [source.id for source in release.sources]
    if len(source_ids) != len(set(source_ids)):
        raise ReleaseError("Release manifest contains duplicate Source identities")
    template = release.template_profile
    if template.id != template.definition.id or template.version != template.definition.version:
        raise ReleaseError("Release manifest Template Profile identity is inconsistent")
    if template.profile_sha256 != _sha256(
        _canonical_json(template.definition.model_dump(mode="json"))
    ):
        raise ReleaseError("Release manifest Template Profile hash is invalid")
    expected_profile_id = plan.template_profile_id or BUILTIN_DEFAULT_ID
    expected_profile_version = plan.template_profile_version or 1
    if (template.id, template.version) != (expected_profile_id, expected_profile_version):
        raise ReleaseError("Release manifest Template Profile does not match its tagged Course")
    if template.id == BUILTIN_DEFAULT_ID:
        if template.template_sha256 is not None or template.template_storage_path is not None:
            raise ReleaseError("Built-in Template Profile must not name stored template bytes")
    elif (
        template.template_sha256 is None or template.template_storage_path != "inputs/template.pptx"
    ):
        raise ReleaseError("Custom Template Profile must pin stored template bytes")

    presentations = [
        _tagged_presentation(workspace, release.commit_oid, artifact_id)
        for artifact_id in selection.artifact_ids
    ]
    if any(
        presentation.lecture_id != artifact.lecture_id
        for presentation, artifact in zip(presentations, release.artifacts, strict=True)
    ):
        raise ReleaseError("Release manifest Artifact metadata does not match its tagged Revision")
    try:
        expected_validation = validate_release(
            plan=plan,
            presentations=presentations,
            sources=sources,
            selection=selection,
            waivers=release.validation.waivers,
        )
    except InvalidWaiver as error:
        raise ReleaseError("Release manifest contains an invalid Waiver") from error
    if expected_validation != release.validation or not expected_validation.can_publish:
        raise ReleaseError("Release validation does not match its tagged Revision")


def _source_pin(source: Source) -> ReleaseSourcePin:
    return ReleaseSourcePin(
        id=source.id,
        resource_id=source.resource_id,
        source_version_id=source.source_version_id,
        label=source.label,
    )


def _canonical_json(payload: object) -> bytes:
    return (
        json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode(
            "utf-8"
        )
        + b"\n"
    )


def _sha256(content: bytes) -> str:
    return hashlib.sha256(content).hexdigest()


def _tagged_plan(workspace: Path, commit_oid: str) -> CoursePlan:
    try:
        payload = yaml.safe_load(
            _read_commit_blob(workspace, commit_oid, "course.yaml").decode("utf-8")
        )
        return CoursePlan.model_validate(payload)
    except (UnicodeError, yaml.YAMLError, ValidationError, ValueError) as error:
        raise ReleaseError("Tagged Course Plan is invalid") from error


def _tagged_sources(workspace: Path, commit_oid: str) -> SourcesIndex:
    names = _git(workspace, "ls-tree", "--name-only", commit_oid, "--", "sources.yaml")
    if names == b"":
        return SourcesIndex()
    if names != b"sources.yaml\n":
        raise ReleaseError("Tagged Sources index cannot be identified safely")
    try:
        payload = yaml.safe_load(
            _read_commit_blob(workspace, commit_oid, "sources.yaml").decode("utf-8")
        )
        return SourcesIndex.model_validate(payload)
    except (UnicodeError, yaml.YAMLError, ValidationError, ValueError) as error:
        raise ReleaseError("Tagged Sources index is invalid") from error


def _tagged_presentation(workspace: Path, commit_oid: str, presentation_id: str) -> Presentation:
    if re.fullmatch(r"presentation-[0-9a-f]{12}", presentation_id) is None:
        raise ReleaseError("Release Presentation identity is invalid")
    path = f"presentations/{presentation_id}.yaml"
    try:
        payload = yaml.safe_load(_read_commit_blob(workspace, commit_oid, path).decode("utf-8"))
        presentation = Presentation.model_validate(payload)
    except (UnicodeError, yaml.YAMLError, ValidationError, ValueError) as error:
        raise ReleaseError("Tagged Presentation is invalid") from error
    if presentation.id != presentation_id:
        raise ReleaseError("Tagged Presentation identity is inconsistent")
    return presentation


def _read_commit_blob(workspace: Path, commit_oid: str, path: str) -> bytes:
    if not OID_PATTERN.fullmatch(commit_oid):
        raise ReleaseError("Course Revision identity is invalid")
    return _git(
        workspace,
        "cat-file",
        "blob",
        f"{commit_oid}:{path}",
        max_output_bytes=MAX_CANONICAL_BLOB_BYTES,
    )


def _read_template_bytes(templates_data_root: Path, profile: TemplateProfile) -> bytes | None:
    if profile.id == BUILTIN_DEFAULT_ID:
        return None
    path = profile_dir(templates_data_root, profile.id) / "template.pptx"
    try:
        descriptor = os.open(path, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0))
    except OSError as error:
        raise ReleaseError("Pinned template cannot be read safely") from error
    try:
        metadata = os.fstat(descriptor)
        if not stat.S_ISREG(metadata.st_mode) or metadata.st_size > MAX_ARTIFACT_BYTES:
            raise ReleaseError("Pinned template cannot be read safely")
        chunks: list[bytes] = []
        remaining = metadata.st_size
        while remaining:
            chunk = os.read(descriptor, min(65_536, remaining))
            if not chunk:
                raise ReleaseError("Pinned template changed while it was being read")
            chunks.append(chunk)
            remaining -= len(chunk)
        if os.read(descriptor, 1) or not _same_file_identity(metadata, os.fstat(descriptor)):
            raise ReleaseError("Pinned template changed while it was being read")
        return b"".join(chunks)
    finally:
        os.close(descriptor)


def _export_presentations(
    presentations: list[Presentation], profile: TemplateProfile, template_bytes: bytes | None
) -> list[tuple[Presentation, bytes]]:
    if template_bytes is None:
        return [
            (
                presentation,
                _deterministic_pptx(export_presentation(presentation, profile=profile)),
            )
            for presentation in presentations
        ]
    with tempfile.TemporaryDirectory(prefix="course-harness-release-") as directory:
        template_path = Path(directory) / "template.pptx"
        _write_path_new_file(template_path, template_bytes)
        return [
            (
                presentation,
                _deterministic_pptx(
                    export_presentation(
                        presentation,
                        profile=profile,
                        template_path=template_path,
                    )
                ),
            )
            for presentation in presentations
        ]


def _deterministic_pptx(content: bytes) -> bytes:
    source = io.BytesIO(content)
    target = io.BytesIO()
    try:
        with (
            zipfile.ZipFile(source, "r") as archive,
            zipfile.ZipFile(
                target, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=9
            ) as output,
        ):
            if len(archive.infolist()) > 10_000:
                raise ReleaseError("Generated Presentation contains too many package entries")
            total = 0
            for entry in sorted(archive.infolist(), key=lambda item: item.filename):
                total += entry.file_size
                if total > MAX_ARTIFACT_BYTES:
                    raise ReleaseError("Generated Presentation package is too large")
                data = archive.read(entry)
                normalized = zipfile.ZipInfo(entry.filename, date_time=(1980, 1, 1, 0, 0, 0))
                normalized.compress_type = zipfile.ZIP_DEFLATED
                normalized.external_attr = entry.external_attr
                normalized.create_system = entry.create_system
                output.writestr(normalized, data)
    except (OSError, zipfile.BadZipFile) as error:
        raise ReleaseError("Generated Presentation package is invalid") from error
    return target.getvalue()


def _git_environment() -> dict[str, str]:
    environment = {key: value for key, value in os.environ.items() if not key.startswith("GIT_")}
    environment.update(
        {
            "GIT_CONFIG_NOSYSTEM": "1",
            "GIT_NO_REPLACE_OBJECTS": "1",
            "GIT_OPTIONAL_LOCKS": "0",
            "GIT_TERMINAL_PROMPT": "0",
        }
    )
    return environment


def _git(
    workspace: Path,
    *arguments: str,
    input_data: bytes | None = None,
    max_output_bytes: int = MAX_GIT_OUTPUT_BYTES,
) -> bytes:
    command = [
        "git",
        "--literal-pathspecs",
        "-c",
        "core.fsmonitor=false",
        "-c",
        "core.hooksPath=/dev/null",
        "-c",
        "core.pager=cat",
        "-c",
        "tag.gpgSign=false",
        "-c",
        "user.name=Course Harness",
        "-c",
        "user.email=course-harness@localhost",
        *arguments,
    ]
    process: subprocess.Popen[bytes] | None = None
    selector = selectors.DefaultSelector()
    try:
        process = subprocess.Popen(
            command,
            cwd=workspace,
            stdin=subprocess.PIPE if input_data is not None else subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            env=_git_environment(),
        )
        if input_data is not None and process.stdin is not None:
            process.stdin.write(input_data)
            process.stdin.close()
        if process.stdout is None:
            raise ReleaseError("Course Release Git operation failed")
        os.set_blocking(process.stdout.fileno(), False)
        selector.register(process.stdout, selectors.EVENT_READ)
        deadline = time.monotonic() + GIT_TIMEOUT_SECONDS
        output = bytearray()
        while selector.get_map():
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise ReleaseError("Course Release Git operation timed out")
            events = selector.select(remaining)
            if not events:
                raise ReleaseError("Course Release Git operation timed out")
            for key, _mask in events:
                chunk = os.read(key.fd, min(65_536, max_output_bytes + 1 - len(output)))
                if not chunk:
                    selector.unregister(key.fileobj)
                    continue
                output.extend(chunk)
                if len(output) > max_output_bytes:
                    raise ReleaseError("Course Release Git output is too large")
        returncode = process.wait(timeout=max(0.001, deadline - time.monotonic()))
        if returncode != 0:
            raise ReleaseError("Course Release Git operation failed")
        return bytes(output)
    except (OSError, subprocess.TimeoutExpired) as error:
        raise ReleaseError("Course Release Git operation failed") from error
    finally:
        selector.close()
        if process is not None and process.poll() is None:
            process.kill()
            with suppress(OSError, subprocess.TimeoutExpired):
                process.wait(timeout=1)


def _head_oid(workspace: Path) -> str:
    oid = (
        _git(workspace, "rev-parse", "--verify", "refs/heads/main^{commit}").decode("ascii").strip()
    )
    if not OID_PATTERN.fullmatch(oid):
        raise ReleaseError("Course Revision identity is invalid")
    return oid


def _tag_exists(workspace: Path, tag: str) -> bool:
    command = [
        "git",
        "--literal-pathspecs",
        "-c",
        "core.hooksPath=/dev/null",
        "show-ref",
        "--verify",
        "--quiet",
        f"refs/tags/{tag}",
    ]
    try:
        result = subprocess.run(
            command,
            cwd=workspace,
            check=False,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            timeout=GIT_TIMEOUT_SECONDS,
            env=_git_environment(),
        )
    except (OSError, subprocess.TimeoutExpired) as error:
        raise ReleaseError("Course Release Git operation failed") from error
    if result.returncode not in {0, 1}:
        raise ReleaseError("Course Release Git operation failed")
    return result.returncode == 0


def _create_annotated_tag(workspace: Path, tag: str, revision: str, manifest: bytes) -> None:
    try:
        _git(
            workspace,
            "tag",
            "--annotate",
            "--cleanup=verbatim",
            "--file=-",
            tag,
            revision,
            input_data=manifest,
        )
    except ReleaseError as error:
        if _tag_exists(workspace, tag):
            raise ReleaseConflict(
                f"Release {tag.removeprefix('course-release/')} already exists"
            ) from error
        raise


@dataclass
class _ReleaseDirectory:
    root_fd: int
    course_fd: int
    release_fd: int
    slug: str

    def close(self, *, sync: bool = False) -> None:
        if sync:
            os.fsync(self.release_fd)
            os.fsync(self.course_fd)
            os.fsync(self.root_fd)
        os.close(self.release_fd)
        os.close(self.course_fd)
        os.close(self.root_fd)


def _directory_flags() -> int:
    return os.O_RDONLY | os.O_DIRECTORY | getattr(os, "O_NOFOLLOW", 0)


def _create_release_directory(
    root: Path, course_id: str, slug: str, *, workspace: Path
) -> _ReleaseDirectory:
    prospective = root.resolve(strict=False) / course_id / slug
    if prospective.is_relative_to(workspace.resolve()):
        raise ReleaseError("Release storage must be outside the Course Workspace")
    root.mkdir(parents=True, exist_ok=True, mode=0o700)
    try:
        root_fd = os.open(root, _directory_flags())
    except OSError as error:
        raise ReleaseError("Release storage is unsafe") from error
    try:
        try:
            os.mkdir(course_id, mode=0o700, dir_fd=root_fd)
            os.fsync(root_fd)
        except FileExistsError:
            pass
        course_fd = os.open(course_id, _directory_flags(), dir_fd=root_fd)
        try:
            try:
                os.mkdir(slug, mode=0o700, dir_fd=course_fd)
            except FileExistsError as error:
                raise ReleaseConflict(f"Release {slug} already exists") from error
            release_fd = os.open(slug, _directory_flags(), dir_fd=course_fd)
            os.fsync(course_fd)
            return _ReleaseDirectory(root_fd, course_fd, release_fd, slug)
        except Exception:
            os.close(course_fd)
            raise
    except Exception:
        os.close(root_fd)
        raise


def _write_path_new_file(path: Path, content: bytes) -> None:
    with path.open("xb") as stream:
        stream.write(content)
        stream.flush()
        os.fsync(stream.fileno())


def _create_child_directory(parent_fd: int, name: Literal["inputs", "artifacts"]) -> int:
    os.mkdir(name, mode=0o700, dir_fd=parent_fd)
    os.fsync(parent_fd)
    return os.open(name, _directory_flags(), dir_fd=parent_fd)


def _write_new_file_at(directory_fd: int, name: str, content: bytes) -> None:
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0)
    descriptor = os.open(name, flags, 0o600, dir_fd=directory_fd)
    try:
        view = memoryview(content)
        while view:
            written = os.write(descriptor, view)
            if written <= 0:
                raise ReleaseError("Release artifact could not be persisted")
            view = view[written:]
        os.fsync(descriptor)
    finally:
        os.close(descriptor)
    os.fsync(directory_fd)


def _read_release_file(
    root: Path,
    course_id: str,
    slug: str,
    child: Literal["inputs", "artifacts"],
    filename: str,
    *,
    max_bytes: int,
) -> bytes:
    descriptors: list[int] = []
    try:
        descriptor = os.open(root, _directory_flags())
        descriptors.append(descriptor)
        for component in (course_id, slug, child):
            descriptor = os.open(component, _directory_flags(), dir_fd=descriptor)
            descriptors.append(descriptor)
        file_fd = os.open(
            filename,
            os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0),
            dir_fd=descriptor,
        )
        descriptors.append(file_fd)
        before = os.fstat(file_fd)
        if not stat.S_ISREG(before.st_mode) or before.st_size > max_bytes:
            raise ReleaseError("Stored Release data cannot be read safely")
        chunks: list[bytes] = []
        remaining = before.st_size
        while remaining:
            chunk_bytes = os.read(file_fd, min(65_536, remaining))
            if not chunk_bytes:
                raise ReleaseError("Stored Release data changed while being read")
            chunks.append(chunk_bytes)
            remaining -= len(chunk_bytes)
        after = os.fstat(file_fd)
        if os.read(file_fd, 1) or not _same_file_identity(before, after):
            raise ReleaseError("Stored Release data changed while being read")
        return b"".join(chunks)
    except OSError as error:
        raise ReleaseError("Stored Release data cannot be read safely") from error
    finally:
        for descriptor in reversed(descriptors):
            with suppress(OSError):
                os.close(descriptor)


def _same_file_identity(before: os.stat_result, after: os.stat_result) -> bool:
    return (
        before.st_dev,
        before.st_ino,
        before.st_mode,
        before.st_size,
        before.st_mtime_ns,
        before.st_ctime_ns,
    ) == (
        after.st_dev,
        after.st_ino,
        after.st_mode,
        after.st_size,
        after.st_mtime_ns,
        after.st_ctime_ns,
    )


def _cleanup_unpublished_release(
    storage: _ReleaseDirectory, *, has_template: bool, artifact_ids: list[str]
) -> None:
    if artifact_ids:
        with suppress(OSError):
            artifacts_fd = os.open("artifacts", _directory_flags(), dir_fd=storage.release_fd)
            try:
                for artifact_id in artifact_ids:
                    with suppress(OSError):
                        os.unlink(f"{artifact_id}.pptx", dir_fd=artifacts_fd)
                os.fsync(artifacts_fd)
            finally:
                os.close(artifacts_fd)
            os.rmdir("artifacts", dir_fd=storage.release_fd)
    if has_template:
        with suppress(OSError):
            inputs_fd = os.open("inputs", _directory_flags(), dir_fd=storage.release_fd)
            try:
                with suppress(OSError):
                    os.unlink("template.pptx", dir_fd=inputs_fd)
                os.fsync(inputs_fd)
            finally:
                os.close(inputs_fd)
            os.rmdir("inputs", dir_fd=storage.release_fd)
    with suppress(OSError):
        os.fsync(storage.release_fd)
        os.rmdir(storage.slug, dir_fd=storage.course_fd)
        os.fsync(storage.course_fd)
    storage.close(sync=False)
