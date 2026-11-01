"""Publish and inspect immutable Course Releases."""

from __future__ import annotations

import io
import logging
import os
import stat
import tempfile
import zipfile
from collections.abc import Iterable, Mapping
from contextlib import suppress
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from course_harness.core import releases
from course_harness.core.course_plan import InvalidCoursePlan, read_course_plan
from course_harness.core.export import ExportError, export_presentation
from course_harness.core.presentation import InvalidPresentation, Presentation, read_presentation
from course_harness.core.release_files import (
    TEMPLATE_PATH,
    expected_release_files,
    read_release_artifact_file,
    read_release_files,
    release_files_commit,
    release_files_ref,
)
from course_harness.core.release_validation import (
    InvalidWaiver,
    ReleaseSelection,
    Waiver,
    validate_release,
)
from course_harness.core.releases import (
    MAX_ARTIFACT_BYTES,
    MAX_MANIFEST_BYTES,
    OID_PATTERN,
    SLUG_PATTERN,
    CourseRelease,
    ReleaseArtifact,
    ReleaseError,
    ReleaseTemplatePin,
    canonical_json,
    release_quizzes,
    run_git,
    sha256_hex,
    source_pin,
    tag_exists,
    tagged_presentation,
    tagged_quiz,
    verify_release_manifest,
)
from course_harness.core.sources import (
    InvalidSourcesIndex,
    PinnedSource,
    SourceImageResolver,
    SourcesIndex,
    read_sources_index,
)
from course_harness.core.template_profiles import BUILTIN_DEFAULT_ID, TemplateProfile
from course_harness.sources import pinned_image_resolver
from course_harness.template_profiles import profile_dir, resolve_profile
from course_harness.workspace_history import list_revisions, read_current_state

MAX_RELEASE_BYTES = 256_000_000
logger = logging.getLogger("course-harness")


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


def publish_release(
    *,
    workspace: Path,
    release_data_root: Path,
    templates_data_root: Path,
    request: PublishReleaseRequest,
    evidence_line_counts: Mapping[str, int],
    library_data_root: Path | None = None,
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
    # Quizzes are read from the Revision being published; Current State is clean against it.
    quiz_entries = release_quizzes(workspace, revision, list(request.selection.lecture_ids))
    quizzes = [tagged_quiz(workspace, revision, entry.lecture_id) for entry in quiz_entries]
    try:
        validation = validate_release(
            plan=plan,
            presentations=presentations,
            sources=sources,
            selection=request.selection,
            waivers=request.waivers,
            evidence_line_counts=evidence_line_counts,
            quizzes=quizzes,
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
    profile_bytes = canonical_json(profile.model_dump(mode="json"))
    template_bytes = _read_template_bytes(templates_data_root, profile)
    template_pin = ReleaseTemplatePin(
        id=profile.id,
        version=profile.version,
        profile_sha256=sha256_hex(profile_bytes),
        template_sha256=sha256_hex(template_bytes) if template_bytes is not None else None,
        template_storage_path="inputs/template.pptx" if template_bytes is not None else None,
        definition=profile,
    )

    exports: list[tuple[ReleaseArtifact, bytes]] = []
    total_bytes = len(template_bytes or b"")
    try:
        generated = _export_presentations(
            presentations,
            profile,
            template_bytes,
            _image_resolver(sources.sources, library_data_root),
        )
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
                    sha256=sha256_hex(content),
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
        sources=[source_pin(source) for source in sources.sources],
        template_profile=template_pin,
        validation=validation,
        artifacts=[artifact for artifact, _content in exports],
        quizzes=quiz_entries,
    )
    verify_release_manifest(workspace, release)
    manifest = canonical_json(release.model_dump(mode="json"))
    if len(manifest) > MAX_MANIFEST_BYTES:
        raise ReleaseError("Release manifest is too large")
    if tag_exists(workspace, tag):
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
        files_commit = _write_release_files_commit(
            workspace, release, template_bytes, {item.id: content for item, content in exports}
        )
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
    try:
        _point_release_files_ref(workspace, release.slug, files_commit)
    except ReleaseError:
        # The Release is already named; pushing backfills its files ref from Release data.
        logger.warning("Could not record the files of Release %s in Git", release.slug)
    return release


@dataclass(frozen=True)
class ReleaseFilesBackfill:
    """Which Releases gained a files ref, and which have neither one nor local Release data."""

    written: list[str]
    missing: list[str]


def backfill_release_files(*, workspace: Path, release_data_root: Path) -> ReleaseFilesBackfill:
    """Ensure every Release with local Release data has a verified files ref.

    A ref that already verifies against its manifest is kept, and the files commit is derived
    only from the manifest and the files, so a pushed ref never needs a forced update. Releases
    whose files are neither in Git nor in local Release data are reported as missing.
    """
    written: list[str] = []
    missing: list[str] = []
    for release in list_releases(workspace):
        current = release_files_commit(workspace, release.slug)
        if current is not None:
            try:
                read_release_files(workspace, release)
                continue
            except ReleaseError:
                pass
        try:
            template, artifacts = _local_release_files(release_data_root, release)
        except ReleaseError:
            if current is not None:
                raise ReleaseError(
                    f"The files of Release {release.slug} in Git do not match its manifest"
                ) from None
            missing.append(release.slug)
            continue
        commit = _write_release_files_commit(workspace, release, template, artifacts)
        _point_release_files_ref(workspace, release.slug, commit, expected=current)
        written.append(release.slug)
    return ReleaseFilesBackfill(written=written, missing=missing)


def _local_release_files(
    release_data_root: Path, release: CourseRelease
) -> tuple[bytes | None, dict[str, bytes]]:
    """Read a Release's files from local Release data, verified against its manifest."""
    template = None
    pin = release.template_profile
    if pin.template_storage_path is not None:
        template = _read_release_file(
            release_data_root,
            release.course_id,
            release.slug,
            "inputs",
            "template.pptx",
            max_bytes=MAX_ARTIFACT_BYTES,
        )
        if sha256_hex(template) != pin.template_sha256:
            raise ReleaseError("Stored Release template does not match its manifest")
    artifacts: dict[str, bytes] = {}
    for artifact in release.artifacts:
        content = _read_release_file(
            release_data_root,
            release.course_id,
            release.slug,
            "artifacts",
            f"{artifact.id}.pptx",
            max_bytes=MAX_ARTIFACT_BYTES,
        )
        if len(content) != artifact.size or sha256_hex(content) != artifact.sha256:
            raise ReleaseError("Stored Release Artifact does not match its manifest")
        artifacts[artifact.id] = content
    return template, artifacts


def _write_release_files_commit(
    workspace: Path,
    release: CourseRelease,
    template: bytes | None,
    artifacts: Mapping[str, bytes],
) -> str:
    """Write a commit holding exactly the Release's files with plumbing; no index or checkout.

    The commit depends only on the files and the manifest, so writing it again gives the same
    commit.
    """
    files: dict[str, bytes] = {}
    if template is not None:
        files[TEMPLATE_PATH] = template
    for artifact in release.artifacts:
        files[artifact.storage_path] = artifacts[artifact.id]
    if set(files) != set(expected_release_files(release)):
        raise ReleaseError("Release files do not match its manifest")
    directories: dict[str, list[str]] = {}
    for path, content in sorted(files.items()):
        directory, name = path.split("/", 1)
        blob = _git_oid(workspace, "hash-object", "-w", "--stdin", input_data=content)
        directories.setdefault(directory, []).append(f"100644 blob {blob}\t{name}")
    tree = _make_tree(
        workspace,
        [
            f"040000 tree {_make_tree(workspace, entries)}\t{directory}"
            for directory, entries in sorted(directories.items())
        ],
    )
    timestamp = int(releases.published_time(release).timestamp())
    identity = f"Course Harness <course-harness@localhost> {timestamp} +0000"
    commit = (
        f"tree {tree}\nauthor {identity}\ncommitter {identity}\n\n"
        f"Files of Course Release {release.slug}\n"
    ).encode()
    return _git_oid(workspace, "hash-object", "-t", "commit", "-w", "--stdin", input_data=commit)


def _make_tree(workspace: Path, entries: list[str]) -> str:
    listing = "".join(f"{entry}\n" for entry in entries).encode()
    return _git_oid(workspace, "mktree", input_data=listing)


def _git_oid(workspace: Path, *arguments: str, input_data: bytes) -> str:
    oid = run_git(workspace, *arguments, input_data=input_data).decode("ascii").strip()
    if not OID_PATTERN.fullmatch(oid):
        raise ReleaseError("Release files could not be recorded in Git")
    return oid


def _point_release_files_ref(
    workspace: Path, slug: str, commit: str, *, expected: str | None = None
) -> None:
    # An empty expected value makes Git refuse to replace a ref created in the meantime.
    run_git(workspace, "update-ref", release_files_ref(slug), commit, expected or "")


def list_releases(workspace: Path) -> list[CourseRelease]:
    """List valid immutable Release manifests, newest tag first."""
    list_revisions(workspace)
    return releases.list_releases(workspace)


def read_release(workspace: Path, slug: str) -> CourseRelease | None:
    """Read and verify one Release manifest from its annotated tag."""
    if not SLUG_PATTERN.fullmatch(slug):
        raise ReleaseError("Release slug is invalid")
    list_revisions(workspace)
    return releases.read_release(workspace, slug)


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
    try:
        content = _read_release_file(
            release_data_root,
            release.course_id,
            release.slug,
            "artifacts",
            f"{artifact.id}.pptx",
            max_bytes=MAX_ARTIFACT_BYTES,
        )
    except ReleaseError as error:
        # A Workspace cloned from a remote has the Release's files in Git, not in Release data.
        missing = isinstance(error.__cause__, FileNotFoundError)
        if not missing or release_files_commit(workspace, release.slug) is None:
            raise
        return read_release_artifact_file(workspace, release, artifact.id)
    if len(content) != artifact.size or sha256_hex(content) != artifact.sha256:
        raise ReleaseError("Stored Release Artifact does not match its manifest")
    return content


def regenerate_release_artifact(
    *,
    workspace: Path,
    release_data_root: Path,
    slug: str,
    artifact_id: str,
    library_data_root: Path | None = None,
) -> bytes:
    """Regenerate solely from the tagged Revision and manifest-pinned template inputs."""
    release = read_release(workspace, slug)
    if release is None:
        raise ReleaseError("Course Release was not found")
    artifact = next((item for item in release.artifacts if item.id == artifact_id), None)
    if artifact is None:
        raise ReleaseError("Release Artifact was not found")
    presentation = tagged_presentation(workspace, release.commit_oid, artifact.id)
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
        if sha256_hex(template_bytes) != release.template_profile.template_sha256:
            raise ReleaseError("Stored Release template does not match its manifest")
    try:
        regenerated = _export_presentations(
            [presentation],
            release.template_profile.definition,
            template_bytes,
            _image_resolver(release.sources, library_data_root),
        )[0][1]
    except (OSError, ValueError, ExportError) as error:
        raise ReleaseError("Release Artifact could not be regenerated") from error
    if len(regenerated) != artifact.size or sha256_hex(regenerated) != artifact.sha256:
        raise ReleaseError("Regenerated Release Artifact differs from the published Artifact")
    return regenerated


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


def _image_resolver(
    sources: Iterable[PinnedSource], library_data_root: Path | None
) -> SourceImageResolver | None:
    """Resolve Slide images from the Source Versions this Release pins."""
    if library_data_root is None:
        return None
    return pinned_image_resolver(sources, library_data_root)


def _export_presentations(
    presentations: list[Presentation],
    profile: TemplateProfile,
    template_bytes: bytes | None,
    image_resolver: SourceImageResolver | None = None,
) -> list[tuple[Presentation, bytes]]:
    if template_bytes is None:
        return [
            (
                presentation,
                _deterministic_pptx(
                    export_presentation(
                        presentation, profile=profile, image_resolver=image_resolver
                    )
                ),
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
                        image_resolver=image_resolver,
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


def _head_oid(workspace: Path) -> str:
    oid = (
        run_git(workspace, "rev-parse", "--verify", "refs/heads/main^{commit}")
        .decode("ascii")
        .strip()
    )
    if not OID_PATTERN.fullmatch(oid):
        raise ReleaseError("Course Revision identity is invalid")
    return oid


def _create_annotated_tag(workspace: Path, tag: str, revision: str, manifest: bytes) -> None:
    try:
        run_git(
            workspace,
            "-c",
            "tag.gpgSign=false",
            "-c",
            "user.name=Course Harness",
            "-c",
            "user.email=course-harness@localhost",
            "tag",
            "--annotate",
            "--cleanup=verbatim",
            "--file=-",
            tag,
            revision,
            input_data=manifest,
        )
    except ReleaseError as error:
        if tag_exists(workspace, tag):
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
