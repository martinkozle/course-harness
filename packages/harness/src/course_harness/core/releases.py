"""Read and verify Course Releases from Git objects.

A Release is an annotated ``course-release/<slug>`` tag whose message is the canonical JSON
manifest and whose commit holds the canonical Course files. Everything here reads Git objects
with ``git cat-file``, ``git ls-tree`` and ``git for-each-ref``: it works the same on a Course
Workspace and on a bare mirror, never checks anything out, and never runs hooks.

A mirror is untrusted. Git runs against the named repository only, without user or system
configuration; every object's size is checked before it is read; canonical YAML may not use
anchors or aliases; reading one Release has a cumulative byte and time budget; and looking for
the newest Release tries a bounded number of tags within a budget of its own.
"""

from __future__ import annotations

import hashlib
import json
import math
import os
import re
import selectors
import subprocess
import sys
import time
from collections.abc import Callable, Iterator
from contextlib import contextmanager, suppress
from contextvars import ContextVar
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Literal, cast

import yaml
from pydantic import BaseModel, ConfigDict, Field, ValidationError

from course_harness.core.canonical_yaml import load_canonical_yaml
from course_harness.core.course_plan import LECTURE_ID_PATTERN, CoursePlan
from course_harness.core.presentation import Presentation
from course_harness.core.quiz import InvalidQuiz, Quiz, parse_quiz, quiz_path
from course_harness.core.release_validation import (
    InvalidWaiver,
    ReleaseValidationResult,
    validate_release,
)
from course_harness.core.sources import Source, SourcesIndex
from course_harness.core.template_profiles import BUILTIN_DEFAULT_ID, TemplateProfile

GIT_TIMEOUT_SECONDS = 5
MAX_GIT_OUTPUT_BYTES = 1_000_000
MAX_MANIFEST_BYTES = 512_000
MAX_ARTIFACT_BYTES = 64_000_000
MAX_RELEASES = 10_000
MAX_CANONICAL_BLOB_BYTES = 1_000_000
MAX_TAG_OBJECT_BYTES = MAX_MANIFEST_BYTES + 16_000
MAX_RELEASE_READ_BYTES = 8_000_000
RELEASE_READ_SECONDS = 30
MAX_RELEASE_CANDIDATES = 20
MAX_RELEASE_SCAN_BYTES = 32_000_000
RELEASE_SCAN_SECONDS = 60
SLUG_PATTERN = re.compile(r"[a-z0-9](?:[a-z0-9-]{0,62}[a-z0-9])?\Z")
OID_PATTERN = re.compile(r"(?:[0-9a-f]{40}|[0-9a-f]{64})\Z")


class ReleaseError(RuntimeError):
    """A Course Release cannot be published or inspected safely."""


class ReleaseSourcePin(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str = Field(pattern=r"^source-[0-9a-f]{12}$")
    resource_id: str = Field(pattern=r"^resource-[0-9a-f]{12}$")
    source_version_id: str = Field(pattern=r"^[0-9a-f]{64}$")
    label: str = Field(min_length=1, max_length=200)
    representation: str | None = Field(default=None, exclude_if=lambda value: value is None)


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
    lecture_id: str = Field(pattern=LECTURE_ID_PATTERN)
    media_type: Literal[
        "application/vnd.openxmlformats-officedocument.presentationml.presentation"
    ] = "application/vnd.openxmlformats-officedocument.presentationml.presentation"
    storage_path: str = Field(pattern=r"^artifacts/presentation-[0-9a-f]{12}\.pptx$", max_length=48)
    size: int = Field(ge=0, le=MAX_ARTIFACT_BYTES)
    sha256: str = Field(pattern=r"^[0-9a-f]{64}$")


class ReleaseQuiz(BaseModel):
    """A Quiz published with an included Lecture, read from the tagged Revision."""

    model_config = ConfigDict(extra="forbid")

    lecture_id: str = Field(pattern=LECTURE_ID_PATTERN)
    path: str = Field(pattern=r"^quizzes/lecture-[0-9a-f]{12}\.yaml$", max_length=40)


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
    # Omitted when empty so manifests of Releases published before Quizzes stay exact.
    quizzes: list[ReleaseQuiz] = Field(
        default_factory=list, max_length=1_000, exclude_if=lambda value: not value
    )


@dataclass(frozen=True)
class RejectedTag:
    """A Release tag that is not a valid Release, and why."""

    name: str
    reason: str


@dataclass(frozen=True)
class ReleaseScan:
    """Valid Releases, newest first, and the Release tags that were rejected."""

    releases: list[CourseRelease]
    rejected: list[RejectedTag]

    @property
    def newest(self) -> CourseRelease | None:
        return self.releases[0] if self.releases else None


@dataclass
class ReadBudget:
    """What a read may still spend; spending also charges every budget it is nested in.

    The outermost budget also keeps the tagged files already parsed, so Release tags that share
    a commit cost one parse.
    """

    deadline: float
    remaining_bytes: int
    outer: ReadBudget | None = None
    tagged_files: dict[tuple[str, str, str], object] = field(default_factory=dict)

    def chain(self) -> list[ReadBudget]:
        budgets = [self]
        while budgets[-1].outer is not None:
            budgets.append(budgets[-1].outer)
        return budgets

    def earliest_deadline(self) -> float:
        return min(budget.deadline for budget in self.chain())

    def allowance(self) -> int:
        return min(budget.remaining_bytes for budget in self.chain())

    def charge(self, size: int) -> None:
        for budget in self.chain():
            budget.remaining_bytes -= size


ACTIVE_READ_BUDGET: ContextVar[ReadBudget | None] = ContextVar("release_read_budget", default=None)


def list_releases(repository: Path) -> list[CourseRelease]:
    """List valid Releases, newest first; raise ``ReleaseError`` if any Release tag is invalid."""
    scan = scan_releases(repository)
    if scan.rejected:
        raise ReleaseError(scan.rejected[0].reason)
    return scan.releases


def scan_releases(repository: Path) -> ReleaseScan:
    """Read every Release tag, keeping valid Releases and why the other tags were rejected.

    One bad tag never hides the others. Releases are ordered newest first by publication time.
    """
    valid: list[CourseRelease] = []
    rejected: list[RejectedTag] = []
    # Unlimited, but shares parsed tagged files between the Releases of one commit.
    with read_budget(seconds=math.inf, max_bytes=sys.maxsize):
        for name in release_tag_names(repository):
            try:
                release = read_release_tag(repository, name)
            except ReleaseError as error:
                rejected.append(RejectedTag(name=display_tag_name(name), reason=str(error)))
                continue
            if release is not None:
                valid.append(release)
    return ReleaseScan(releases=sorted(valid, key=published_time, reverse=True), rejected=rejected)


def newest_release(repository: Path) -> ReleaseScan:
    """Find the newest valid Release, trying Release tags newest first by tag creation date.

    At most ``MAX_RELEASE_CANDIDATES`` tags are tried, and reading stops at the first valid
    Release, so ``releases`` holds at most one; ``rejected`` lists the newer tags that were
    skipped. Raises ``ReleaseError`` when the whole search runs out of its time budget.
    """
    rejected: list[RejectedTag] = []
    with read_budget(seconds=RELEASE_SCAN_SECONDS, max_bytes=MAX_RELEASE_SCAN_BYTES):
        for name in release_tag_names(repository, newest_first=True):
            check_read_deadline()
            try:
                release = read_release_tag(repository, name)
            except ReleaseError as error:
                rejected.append(RejectedTag(name=display_tag_name(name), reason=str(error)))
                continue
            if release is not None:
                return ReleaseScan(releases=[release], rejected=rejected)
    return ReleaseScan(releases=[], rejected=rejected)


def release_tag_names(repository: Path, *, newest_first: bool = False) -> list[bytes]:
    """List raw Release tag names below ``refs/tags/``; Git does not require them to be UTF-8.

    Newest first lists only the ``MAX_RELEASE_CANDIDATES`` most recently created tags.
    """
    order = [f"--count={MAX_RELEASE_CANDIDATES}", "--sort=-creatordate"] if newest_first else []
    output = run_git(
        repository,
        "for-each-ref",
        *order,
        "--format=%(refname:strip=2)",
        "refs/tags/course-release",
    )
    names = [line for line in output.split(b"\n") if line]
    if len(names) > MAX_RELEASES:
        raise ReleaseError("Repository has too many Releases")
    return names


def read_release_tag(repository: Path, name: bytes) -> CourseRelease | None:
    """Read the Release that a raw tag name such as ``course-release/fall-2026`` names."""
    prefix = "course-release/"
    try:
        decoded = name.decode("ascii")
    except UnicodeDecodeError as error:
        raise ReleaseError("Repository contains an unsafe Release tag") from error
    slug = decoded.removeprefix(prefix)
    if not decoded.startswith(prefix) or not SLUG_PATTERN.fullmatch(slug):
        raise ReleaseError("Repository contains an unsafe Release tag")
    return read_release(repository, slug)


def display_tag_name(name: bytes) -> str:
    return name.decode("utf-8", errors="backslashreplace")[:200]


def published_time(release: CourseRelease) -> datetime:
    """Parse a Release's timezone-aware publication time."""
    try:
        published = datetime.fromisoformat(release.published_at)
    except ValueError as error:
        raise ReleaseError("Release publication time is invalid") from error
    if published.utcoffset() is None:
        raise ReleaseError("Release publication time is invalid")
    return published


@contextmanager
def read_budget(*, seconds: float, max_bytes: int) -> Iterator[ReadBudget]:
    """Bound the cumulative Git output and time of the reads inside, within any outer budget."""
    budget = ReadBudget(
        deadline=time.monotonic() + seconds,
        remaining_bytes=max_bytes,
        outer=ACTIVE_READ_BUDGET.get(),
    )
    token = ACTIVE_READ_BUDGET.set(budget)
    try:
        yield budget
    finally:
        ACTIVE_READ_BUDGET.reset(token)


def check_read_deadline() -> None:
    budget = ACTIVE_READ_BUDGET.get()
    if budget is not None and time.monotonic() >= budget.earliest_deadline():
        raise ReleaseError("Release read exceeded its time budget")


def cached_tagged_file[T](
    repository: Path, commit_oid: str, path: str, parse: Callable[[], T]
) -> T:
    """Parse a tagged canonical file once per outermost read budget."""
    budget = ACTIVE_READ_BUDGET.get()
    if budget is None:
        return parse()
    cache = budget.chain()[-1].tagged_files
    key = (str(repository), commit_oid, path)
    if key not in cache:
        cache[key] = parse()
    return cast(T, cache[key])


def read_release(repository: Path, slug: str) -> CourseRelease | None:
    """Read and verify one Release manifest from its annotated tag, within a read budget.

    ``repository`` is a Course Workspace or a bare repository such as a mirror.
    """
    with read_budget(seconds=RELEASE_READ_SECONDS, max_bytes=MAX_RELEASE_READ_BYTES):
        return read_verified_release(repository, slug)


def read_verified_release(repository: Path, slug: str) -> CourseRelease | None:
    if not SLUG_PATTERN.fullmatch(slug):
        raise ReleaseError("Release slug is invalid")
    tag = f"course-release/{slug}"
    if not tag_exists(repository, tag):
        return None
    tag_oid = sized_object_id(repository, f"refs/tags/{tag}", max_bytes=MAX_TAG_OBJECT_BYTES)
    raw = run_git(repository, "cat-file", "tag", tag_oid, max_output_bytes=MAX_TAG_OBJECT_BYTES)
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
    canonical = canonical_json(release.model_dump(mode="json"))
    if len(canonical) > MAX_MANIFEST_BYTES or message != canonical:
        raise ReleaseError("Release tag manifest is not exact canonical JSON")
    verify_release_manifest(repository, release)
    return release


def verify_release_manifest(repository: Path, release: CourseRelease) -> None:
    """Check a manifest against the canonical Course files of its tagged Revision."""
    published_time(release)
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

    plan = tagged_plan(repository, release.commit_oid)
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

    sources = tagged_sources(repository, release.commit_oid)
    if release.sources != [source_pin(source) for source in sources.sources]:
        raise ReleaseError("Release manifest Source Versions do not match its tagged Revision")
    source_ids = [source.id for source in release.sources]
    if len(source_ids) != len(set(source_ids)):
        raise ReleaseError("Release manifest contains duplicate Source identities")
    template = release.template_profile
    if template.id != template.definition.id or template.version != template.definition.version:
        raise ReleaseError("Release manifest Template Profile identity is inconsistent")
    if template.profile_sha256 != sha256_hex(
        canonical_json(template.definition.model_dump(mode="json"))
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
        tagged_presentation(repository, release.commit_oid, artifact_id)
        for artifact_id in selection.artifact_ids
    ]
    if any(
        presentation.lecture_id != artifact.lecture_id
        for presentation, artifact in zip(presentations, release.artifacts, strict=True)
    ):
        raise ReleaseError("Release manifest Artifact metadata does not match its tagged Revision")
    if release.quizzes != release_quizzes(repository, release.commit_oid, selection.lecture_ids):
        raise ReleaseError("Release manifest Quizzes do not match its tagged Revision")
    quizzes = [
        tagged_quiz(repository, release.commit_oid, quiz.lecture_id) for quiz in release.quizzes
    ]
    try:
        expected_validation = validate_release(
            plan=plan,
            presentations=presentations,
            sources=sources,
            selection=selection,
            waivers=release.validation.waivers,
            quizzes=quizzes,
        )
    except InvalidWaiver as error:
        raise ReleaseError("Release manifest contains an invalid Waiver") from error
    check_read_deadline()
    if expected_validation != release.validation or not expected_validation.can_publish:
        raise ReleaseError("Release validation does not match its tagged Revision")


def source_pin(source: Source) -> ReleaseSourcePin:
    return ReleaseSourcePin(
        id=source.id,
        resource_id=source.resource_id,
        source_version_id=source.source_version_id,
        label=source.label,
        representation=source.representation,
    )


def canonical_json(payload: object) -> bytes:
    return (
        json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode(
            "utf-8"
        )
        + b"\n"
    )


def sha256_hex(content: bytes) -> str:
    return hashlib.sha256(content).hexdigest()


def tagged_plan(repository: Path, commit_oid: str) -> CoursePlan:
    return cached_tagged_file(
        repository, commit_oid, "course.yaml", lambda: parse_tagged_plan(repository, commit_oid)
    )


def parse_tagged_plan(repository: Path, commit_oid: str) -> CoursePlan:
    try:
        payload = load_canonical_yaml(
            read_commit_blob(repository, commit_oid, "course.yaml").decode("utf-8")
        )
        check_read_deadline()
        return CoursePlan.model_validate(payload)
    except (UnicodeError, yaml.YAMLError, ValidationError, ValueError) as error:
        raise ReleaseError("Tagged Course Plan is invalid") from error


def tagged_sources(repository: Path, commit_oid: str) -> SourcesIndex:
    return cached_tagged_file(
        repository, commit_oid, "sources.yaml", lambda: parse_tagged_sources(repository, commit_oid)
    )


def parse_tagged_sources(repository: Path, commit_oid: str) -> SourcesIndex:
    names = run_git(repository, "ls-tree", "--name-only", commit_oid, "--", "sources.yaml")
    if names == b"":
        return SourcesIndex()
    if names != b"sources.yaml\n":
        raise ReleaseError("Tagged Sources index cannot be identified safely")
    try:
        payload = load_canonical_yaml(
            read_commit_blob(repository, commit_oid, "sources.yaml").decode("utf-8")
        )
        check_read_deadline()
        return SourcesIndex.model_validate(payload)
    except (UnicodeError, yaml.YAMLError, ValidationError, ValueError) as error:
        raise ReleaseError("Tagged Sources index is invalid") from error


def tagged_presentation(repository: Path, commit_oid: str, presentation_id: str) -> Presentation:
    if re.fullmatch(r"presentation-[0-9a-f]{12}", presentation_id) is None:
        raise ReleaseError("Release Presentation identity is invalid")
    path = f"presentations/{presentation_id}.yaml"
    return cached_tagged_file(
        repository,
        commit_oid,
        path,
        lambda: parse_tagged_presentation(repository, commit_oid, presentation_id, path),
    )


def parse_tagged_presentation(
    repository: Path, commit_oid: str, presentation_id: str, path: str
) -> Presentation:
    try:
        content = read_commit_blob(repository, commit_oid, path)
        payload = load_canonical_yaml(content.decode("utf-8"))
        check_read_deadline()
        presentation = Presentation.model_validate(payload)
    except (UnicodeError, yaml.YAMLError, ValidationError, ValueError) as error:
        raise ReleaseError("Tagged Presentation is invalid") from error
    if presentation.id != presentation_id:
        raise ReleaseError("Tagged Presentation identity is inconsistent")
    return presentation


def release_quizzes(repository: Path, commit_oid: str, lecture_ids: list[str]) -> list[ReleaseQuiz]:
    """The Quizzes a Revision holds for the given Lectures, in Lecture order."""
    if not OID_PATTERN.fullmatch(commit_oid):
        raise ReleaseError("Course Revision identity is invalid")
    listing = run_git(repository, "ls-tree", "-z", "--name-only", commit_oid, "--", "quizzes/")
    try:
        present = set(listing.decode("utf-8").split("\0"))
    except UnicodeError as error:
        raise ReleaseError("Tagged Quizzes cannot be identified safely") from error
    quizzes: list[ReleaseQuiz] = []
    for lecture_id in lecture_ids:
        try:
            path = quiz_path(lecture_id)
        except ValueError as error:
            raise ReleaseError("Release Lecture identity is invalid") from error
        if path in present:
            quizzes.append(ReleaseQuiz(lecture_id=lecture_id, path=path))
    return quizzes


def tagged_quiz(repository: Path, commit_oid: str, lecture_id: str) -> Quiz:
    """Read one Lecture's Quiz from a Revision's Git objects."""
    try:
        path = quiz_path(lecture_id)
    except ValueError as error:
        raise ReleaseError("Release Quiz identity is invalid") from error
    return cached_tagged_file(
        repository,
        commit_oid,
        path,
        lambda: parse_tagged_quiz(repository, commit_oid, lecture_id, path),
    )


def parse_tagged_quiz(repository: Path, commit_oid: str, lecture_id: str, path: str) -> Quiz:
    try:
        quiz = parse_quiz(read_commit_blob(repository, commit_oid, path))
    except InvalidQuiz as error:
        raise ReleaseError("Tagged Quiz is invalid") from error
    check_read_deadline()
    if quiz.lecture_id != lecture_id:
        raise ReleaseError("Tagged Quiz identity is inconsistent")
    return quiz


def read_commit_blob(repository: Path, commit_oid: str, path: str) -> bytes:
    if not OID_PATTERN.fullmatch(commit_oid):
        raise ReleaseError("Course Revision identity is invalid")
    blob_oid = sized_object_id(
        repository, f"{commit_oid}:{path}", max_bytes=MAX_CANONICAL_BLOB_BYTES
    )
    return run_git(
        repository, "cat-file", "blob", blob_oid, max_output_bytes=MAX_CANONICAL_BLOB_BYTES
    )


def sized_object_id(repository: Path, name: str, *, max_bytes: int) -> str:
    """Resolve an object name and check its size before anything reads its content."""
    output = run_git(repository, "cat-file", "--batch-check", input_data=f"{name}\n".encode())
    fields = output.split()
    try:
        oid, size = fields[0].decode("ascii"), int(fields[2])
    except (IndexError, UnicodeError, ValueError) as error:
        raise ReleaseError("Repository object is missing") from error
    if len(fields) != 3 or not OID_PATTERN.fullmatch(oid):
        raise ReleaseError("Repository object is missing")
    if size > max_bytes:
        raise ReleaseError("Repository object is too large")
    budget = ACTIVE_READ_BUDGET.get()
    if budget is not None and size > budget.allowance():
        raise ReleaseError("Release exceeds its read budget")
    return oid


def repository_git_dir(repository: Path) -> Path:
    """Use a Course Workspace's ``.git`` or a bare repository itself, never a parent repository.

    A repository that borrows objects through ``objects/info/alternates`` is refused: its
    content would come from a path the repository names.
    """
    dot_git = repository / ".git"
    git_dir = dot_git if dot_git.exists() else repository
    if (git_dir / "objects" / "info" / "alternates").exists():
        raise ReleaseError("Repository borrows objects from another repository")
    return git_dir


def git_command(repository: Path, *arguments: str) -> list[str]:
    """Run Git against exactly this repository, trusting it whoever owns it."""
    root = repository.resolve()
    git_dir = repository_git_dir(root)
    return [
        "git",
        f"--git-dir={git_dir}",
        "--literal-pathspecs",
        "-c",
        f"safe.directory={root}",
        "-c",
        "core.bigFileThreshold=1m",
        "-c",
        "core.fsmonitor=false",
        "-c",
        "core.hooksPath=/dev/null",
        "-c",
        "core.pager=cat",
        *arguments,
    ]


def git_environment() -> dict[str, str]:
    """Ignore user and system Git configuration."""
    environment = {key: value for key, value in os.environ.items() if not key.startswith("GIT_")}
    environment.update(
        {
            "GIT_CONFIG_GLOBAL": "/dev/null",
            "GIT_CONFIG_NOSYSTEM": "1",
            "GIT_NO_REPLACE_OBJECTS": "1",
            "GIT_OPTIONAL_LOCKS": "0",
            "GIT_TERMINAL_PROMPT": "0",
        }
    )
    return environment


def run_git(
    repository: Path,
    *arguments: str,
    input_data: bytes | None = None,
    max_output_bytes: int = MAX_GIT_OUTPUT_BYTES,
) -> bytes:
    deadline = time.monotonic() + GIT_TIMEOUT_SECONDS
    too_large = "Repository Git output is too large"
    budget = ACTIVE_READ_BUDGET.get()
    if budget is not None:
        deadline = min(deadline, budget.earliest_deadline())
        if budget.allowance() < max_output_bytes:
            max_output_bytes = budget.allowance()
            too_large = "Release exceeds its read budget"
    process: subprocess.Popen[bytes] | None = None
    selector = selectors.DefaultSelector()
    try:
        process = subprocess.Popen(
            git_command(repository, *arguments),
            cwd=repository,
            stdin=subprocess.PIPE if input_data is not None else subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            env=git_environment(),
        )
        if input_data is not None and process.stdin is not None:
            process.stdin.write(input_data)
            process.stdin.close()
        if process.stdout is None:
            raise ReleaseError("Repository Git operation failed")
        os.set_blocking(process.stdout.fileno(), False)
        selector.register(process.stdout, selectors.EVENT_READ)
        output = bytearray()
        while selector.get_map():
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise ReleaseError("Repository Git operation timed out")
            events = selector.select(remaining)
            if not events:
                raise ReleaseError("Repository Git operation timed out")
            for key, _mask in events:
                chunk = os.read(key.fd, min(65_536, max_output_bytes + 1 - len(output)))
                if not chunk:
                    selector.unregister(key.fileobj)
                    continue
                output.extend(chunk)
                if len(output) > max_output_bytes:
                    raise ReleaseError(too_large)
        returncode = process.wait(timeout=max(0.001, deadline - time.monotonic()))
        if returncode != 0:
            raise ReleaseError("Repository Git operation failed")
        if budget is not None:
            budget.charge(len(output))
        return bytes(output)
    except (OSError, subprocess.TimeoutExpired) as error:
        raise ReleaseError("Repository Git operation failed") from error
    finally:
        selector.close()
        if process is not None and process.poll() is None:
            process.kill()
            with suppress(OSError, subprocess.TimeoutExpired):
                process.wait(timeout=1)


def tag_exists(repository: Path, tag: str) -> bool:
    try:
        result = subprocess.run(
            git_command(repository, "show-ref", "--verify", "--quiet", f"refs/tags/{tag}"),
            cwd=repository,
            check=False,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            timeout=GIT_TIMEOUT_SECONDS,
            env=git_environment(),
        )
    except (OSError, subprocess.TimeoutExpired) as error:
        raise ReleaseError("Repository Git operation failed") from error
    if result.returncode not in {0, 1}:
        raise ReleaseError("Repository Git operation failed")
    return result.returncode == 0
