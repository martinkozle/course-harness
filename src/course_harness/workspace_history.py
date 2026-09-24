"""Read-only, constrained Git views of canonical Course Workspace state."""

import difflib
import hashlib
import json
import os
import re
import selectors
import stat
import subprocess
import tempfile
import threading
import time
from collections.abc import Callable, Mapping
from contextlib import suppress
from dataclasses import dataclass
from datetime import UTC, datetime
from functools import wraps
from pathlib import Path
from typing import Literal, cast

from pydantic import BaseModel, ConfigDict, Field, field_validator

from course_harness.canonical_mutation import (
    CanonicalFile,
    apply_canonical_mutation,
    restore_canonical_mutation,
)
from course_harness.course_plan import CoursePlan, InvalidCoursePlan, read_course_plan
from course_harness.presentation import InvalidPresentation, Presentation, read_presentation
from course_harness.sources import InvalidSourcesIndex, SourcesIndex, read_sources_index

GIT_TIMEOUT_SECONDS = 5
MAX_GIT_METADATA_ENTRIES = 10_000
MAX_PRESENTATIONS_DIRECTORY_ENTRIES = 10_000
MAX_CANONICAL_ENTRIES = 2_000
MAX_REVISION_SUMMARY_CHARACTERS = 240
MAX_REVISION_METADATA_BYTES = 1_024
MAX_CANONICAL_FILE_BYTES = 8_000_000
MAX_CANONICAL_TOTAL_BYTES = 64_000_000
MAX_CURRENT_STATE_DIFF_LINES = 200
MAX_CURRENT_STATE_DIFF_CHARS = 24_000
CANONICAL_TOP_LEVEL_FILES = ("course.yaml", "sources.yaml")
HISTORY_NOT_INITIALIZED = "Course Workspace history is not initialized"
HISTORY_UNSAFE = "Course Workspace history cannot be read safely"
TRANSACTION_FILE = "course-harness-transaction"
RUN_BOUNDARY_FILE = "course-harness-run-boundary"
PROVENANCE_FILE = "course-harness-provenance.json"
PROVENANCE_PENDING_FILE = "course-harness-provenance-pending.json"
MAX_RECONCILIATION_FILE_BYTES = 128_000
MAX_RECONCILIATION_TOTAL_BYTES = 512_000
OID_PATTERN = re.compile(r"(?:[0-9a-f]{40}|[0-9a-f]{64})\Z")
_HISTORY_LOCK = threading.RLock()
_ACTIVE_RUNS: dict[Path, tuple[str, bool, bool, str]] = {}
_RECOVERY_REF_PATTERN = re.compile(r"refs/course-harness/recovery/[0-9a-f]{24}\Z")


def _serialized[**P, R](function: Callable[P, R]) -> Callable[P, R]:
    @wraps(function)
    def locked(*args: P.args, **kwargs: P.kwargs) -> R:
        with _HISTORY_LOCK:
            return function(*args, **kwargs)

    return locked


class WorkspaceHistoryError(RuntimeError):
    """The selected Workspace does not have a usable, private Git repository."""


class WorkspaceHistoryNotInitializedError(WorkspaceHistoryError):
    """The selected Workspace does not yet contain a Git repository."""


class WorkspaceDriftChangedError(ValueError):
    """The reviewed Workspace Drift identity no longer matches Current State."""


class CurrentStateEntry(BaseModel):
    """One changed canonical file, identified by a safe Workspace-relative path."""

    model_config = ConfigDict(extra="forbid")

    path: str
    status: Literal["added", "modified", "deleted"]
    line_count: int | None = Field(default=None, ge=0)
    diff_lines: list[str] = Field(default_factory=list)
    diff_truncated: bool = False


class CurrentStateValidation(BaseModel):
    model_config = ConfigDict(extra="forbid")

    valid: bool
    findings: list[str] = Field(default_factory=list)


class CurrentState(BaseModel):
    model_config = ConfigDict(extra="forbid")

    clean: bool
    changes: list[CurrentStateEntry] = Field(default_factory=list)
    validation: CurrentStateValidation
    drift: Literal["unknown", "clean", "drift"] = "unknown"
    drift_id: str | None = None
    drift_changes: list[CurrentStateEntry] = Field(default_factory=list)


class RevisionCreateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    summary: str = Field(min_length=1, max_length=MAX_REVISION_SUMMARY_CHARACTERS)

    @field_validator("summary")
    @classmethod
    def summary_is_one_line(cls, value: str) -> str:
        if any(ord(character) < 32 or ord(character) == 127 for character in value):
            raise ValueError("Revision summary must be one line")
        return value


class DriftAcceptRequest(RevisionCreateRequest):
    drift_id: str = Field(min_length=64, max_length=64, pattern=r"^[0-9a-f]{64}$")


class SelectiveRevertRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    path: str


class CourseRevision(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str
    summary: str = Field(max_length=MAX_REVISION_SUMMARY_CHARACTERS)
    created_at: str


class ReconciliationFile(BaseModel):
    """A bounded, UTF-8 canonical file supplied to or returned from a reconciler."""

    model_config = ConfigDict(extra="forbid")

    path: str
    content: str | None = Field(default=None, max_length=MAX_RECONCILIATION_FILE_BYTES)


class ReconciliationContext(BaseModel):
    model_config = ConfigDict(extra="forbid")

    drift_id: str
    files: list[ReconciliationFile]
    baseline_files: list[ReconciliationFile] = Field(default_factory=list)
    missing_paths: list[str] = Field(default_factory=list)
    findings: list[str]


class ReconciliationApplyRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    drift_id: str = Field(min_length=64, max_length=64)
    summary: str = Field(min_length=1, max_length=MAX_REVISION_SUMMARY_CHARACTERS)
    entries: list[ReconciliationFile] = Field(min_length=1)

    @field_validator("summary")
    @classmethod
    def summary_is_one_line(cls, value: str) -> str:
        if any(ord(character) < 32 or ord(character) == 127 for character in value):
            raise ValueError("Revision summary must be one line")
        return value


@dataclass(frozen=True)
class _GitResult:
    returncode: int
    stdout: bytes


@dataclass(frozen=True)
class _CapturedEntry:
    mode: Literal["100644", "100755", "120000"]
    content: bytes


def _canonical_file(entry: _CapturedEntry | None) -> CanonicalFile:
    if entry is None:
        return CanonicalFile.missing()
    mode = 0o120000 if entry.mode == "120000" else int(entry.mode[-3:], 8)
    return CanonicalFile(content=entry.content, mode=mode)


def _git_environment() -> dict[str, str]:
    """Remove Git injection variables and disable Git features that can execute code."""
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


def _git_result(workspace: Path, *arguments: str) -> _GitResult:
    """Run a fixed Git command without client-provided refs, pathspecs, or shell syntax."""
    command = [
        "git",
        "--literal-pathspecs",
        "-c",
        "core.fsmonitor=false",
        "-c",
        "core.useBuiltinFSMonitor=false",
        "-c",
        "core.hooksPath=/dev/null",
        "-c",
        "diff.external=false",
        "-c",
        "core.pager=cat",
        *arguments,
    ]
    try:
        result = subprocess.run(
            command,
            cwd=workspace,
            check=False,
            capture_output=True,
            text=False,
            timeout=GIT_TIMEOUT_SECONDS,
            env=_git_environment(),
        )
    except (OSError, subprocess.TimeoutExpired) as error:
        raise WorkspaceHistoryError(HISTORY_UNSAFE) from error
    return _GitResult(returncode=result.returncode, stdout=result.stdout)


def _git_result_bounded(workspace: Path, max_bytes: int, *arguments: str) -> _GitResult:
    """Run Git while rejecting output before it can grow without bound in memory."""
    command = [
        "git",
        "--literal-pathspecs",
        "-c",
        "core.fsmonitor=false",
        "-c",
        "core.useBuiltinFSMonitor=false",
        "-c",
        "core.hooksPath=/dev/null",
        "-c",
        "diff.external=false",
        "-c",
        "core.pager=cat",
        *arguments,
    ]
    process: subprocess.Popen[bytes] | None = None
    selector = selectors.DefaultSelector()
    try:
        process = subprocess.Popen(
            command,
            cwd=workspace,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            env=_git_environment(),
        )
        if process.stdout is None:
            raise WorkspaceHistoryError(HISTORY_UNSAFE)
        os.set_blocking(process.stdout.fileno(), False)
        selector.register(process.stdout, selectors.EVENT_READ)
        deadline = time.monotonic() + GIT_TIMEOUT_SECONDS
        output = bytearray()
        while selector.get_map():
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise subprocess.TimeoutExpired(command, GIT_TIMEOUT_SECONDS)
            events = selector.select(remaining)
            if not events:
                raise subprocess.TimeoutExpired(command, GIT_TIMEOUT_SECONDS)
            for key, _mask in events:
                chunk = os.read(key.fd, min(65_536, max_bytes + 1 - len(output)))
                if not chunk:
                    selector.unregister(key.fileobj)
                    continue
                output.extend(chunk)
                if len(output) > max_bytes:
                    raise WorkspaceHistoryError("Canonical Course history has too many files")
        remaining = max(0.001, deadline - time.monotonic())
        returncode = process.wait(timeout=remaining)
        return _GitResult(returncode=returncode, stdout=bytes(output))
    except (OSError, subprocess.TimeoutExpired) as error:
        raise WorkspaceHistoryError(HISTORY_UNSAFE) from error
    finally:
        selector.close()
        if process is not None and process.poll() is None:
            process.kill()
            with suppress(OSError, subprocess.TimeoutExpired):
                process.wait(timeout=1)


def _git(workspace: Path, *arguments: str) -> str:
    result = _git_result(workspace, *arguments)
    if result.returncode != 0:
        raise WorkspaceHistoryError(HISTORY_UNSAFE)
    return result.stdout.decode("utf-8", errors="surrogateescape")


def _git_with_index(
    workspace: Path, index: Path, *arguments: str, input_data: bytes = b""
) -> _GitResult:
    """Run fixed plumbing against a server-owned temporary index only."""
    environment = _git_environment()
    environment["GIT_INDEX_FILE"] = str(index)
    command = [
        "git",
        "--literal-pathspecs",
        "-c",
        "core.fsmonitor=false",
        "-c",
        "core.hooksPath=/dev/null",
        "-c",
        "diff.external=false",
        "-c",
        "user.name=Course Harness",
        "-c",
        "user.email=course-harness@localhost",
        *arguments,
    ]
    try:
        result = subprocess.run(
            command,
            cwd=workspace,
            check=False,
            capture_output=True,
            text=False,
            timeout=GIT_TIMEOUT_SECONDS,
            env=environment,
            input=input_data,
        )
    except (OSError, subprocess.TimeoutExpired) as error:
        raise WorkspaceHistoryError(HISTORY_UNSAFE) from error
    if result.returncode != 0:
        raise WorkspaceHistoryError(HISTORY_UNSAFE)
    return _GitResult(returncode=result.returncode, stdout=result.stdout)


def _require_private_workspace_repository(workspace: Path) -> None:
    workspace = workspace.resolve()
    dot_git = workspace / ".git"
    if not _path_exists_or_symlink(dot_git):
        raise WorkspaceHistoryNotInitializedError(HISTORY_NOT_INITIALIZED)
    if not _is_ordinary_directory(dot_git):
        raise WorkspaceHistoryError(HISTORY_UNSAFE)
    _require_contained_git_metadata(dot_git)
    try:
        git_dir = Path(_git(workspace, "rev-parse", "--absolute-git-dir").strip()).resolve(
            strict=True
        )
    except (OSError, WorkspaceHistoryError) as error:
        raise WorkspaceHistoryError(HISTORY_UNSAFE) from error
    if git_dir != dot_git.resolve() or not git_dir.is_relative_to(workspace):
        raise WorkspaceHistoryError(HISTORY_UNSAFE)
    if _git(workspace, "rev-parse", "--is-inside-work-tree").strip() != "true":
        raise WorkspaceHistoryError(HISTORY_UNSAFE)
    head_ref = _git_result(workspace, "symbolic-ref", "-q", "HEAD")
    if head_ref.returncode != 0 or head_ref.stdout.strip() != b"refs/heads/main":
        raise WorkspaceHistoryError(HISTORY_UNSAFE)
    top_level = Path(_git(workspace, "rev-parse", "--show-toplevel").strip()).resolve()
    if top_level != workspace:
        raise WorkspaceHistoryError(HISTORY_UNSAFE)
    presentations = workspace / "presentations"
    if _is_ordinary_directory(presentations):
        presentations_git = presentations / ".git"
        if _path_exists_or_symlink(presentations_git):
            raise WorkspaceHistoryError(HISTORY_UNSAFE)


def _lstat_mode(path: Path) -> int | None:
    try:
        return path.lstat().st_mode
    except OSError:
        return None


def _is_ordinary_directory(path: Path) -> bool:
    mode = _lstat_mode(path)
    return mode is not None and stat.S_ISDIR(mode)


def _is_regular_file(path: Path) -> bool:
    mode = _lstat_mode(path)
    return mode is not None and stat.S_ISREG(mode)


def _path_exists_or_symlink(path: Path) -> bool:
    return _lstat_mode(path) is not None


def _require_contained_git_metadata(dot_git: Path) -> None:
    for name in ("objects", "refs"):
        path = dot_git / name
        if not _is_ordinary_directory(path):
            raise WorkspaceHistoryError(HISTORY_UNSAFE)
        try:
            if not path.resolve(strict=True).is_relative_to(dot_git):
                raise WorkspaceHistoryError(HISTORY_UNSAFE)
        except OSError as error:
            raise WorkspaceHistoryError(HISTORY_UNSAFE) from error
    if _path_exists_or_symlink(dot_git / "commondir"):
        raise WorkspaceHistoryError(HISTORY_UNSAFE)
    for name in ("HEAD", "config"):
        if not _is_regular_file(dot_git / name):
            raise WorkspaceHistoryError(HISTORY_UNSAFE)
    for name in ("index", "packed-refs"):
        path = dot_git / name
        if _path_exists_or_symlink(path) and not _is_regular_file(path):
            raise WorkspaceHistoryError(HISTORY_UNSAFE)
    for name in ("alternates", "http-alternates"):
        if _path_exists_or_symlink(dot_git / "objects" / "info" / name):
            raise WorkspaceHistoryError(HISTORY_UNSAFE)
    for name in ("objects", "refs"):
        if _contains_symlink(dot_git / name):
            raise WorkspaceHistoryError(HISTORY_UNSAFE)


def _contains_symlink(root: Path) -> bool:
    """Bounded non-following traversal of the Git metadata that controls object lookup."""
    pending = [root]
    entries_seen = 0
    try:
        while pending:
            directory = pending.pop()
            with os.scandir(directory) as entries:
                for entry in entries:
                    entries_seen += 1
                    if entries_seen > MAX_GIT_METADATA_ENTRIES:
                        raise WorkspaceHistoryError(HISTORY_UNSAFE)
                    if entry.is_symlink():
                        return True
                    if entry.is_dir(follow_symlinks=False):
                        pending.append(Path(entry.path))
    except OSError as error:
        raise WorkspaceHistoryError(HISTORY_UNSAFE) from error
    return False


def _is_canonical_path(path: str) -> bool:
    if not _is_safe_path_text(path):
        return False
    if path in CANONICAL_TOP_LEVEL_FILES:
        return True
    candidate = Path(path)
    return (
        candidate.parent == Path("presentations")
        and candidate.suffix == ".yaml"
        and not candidate.is_absolute()
    )


def _is_safe_path_text(path: str) -> bool:
    return not any("\udc80" <= character <= "\udcff" for character in path)


def _current_canonical_paths(workspace: Path) -> set[str]:
    """Enumerate the current canonical file tree without following symlinks."""
    paths: set[str] = set()
    for name in CANONICAL_TOP_LEVEL_FILES:
        candidate = workspace / name
        if _path_exists_or_symlink(candidate):
            paths.add(name)
            if len(paths) > MAX_CANONICAL_ENTRIES:
                raise WorkspaceHistoryError("Canonical Course state has too many files")
    presentations = workspace / "presentations"
    if _is_ordinary_directory(presentations):
        for candidate in _presentation_yaml_paths(workspace):
            path = candidate.relative_to(workspace).as_posix()
            mode = _lstat_mode(candidate)
            if (
                _is_safe_path_text(path)
                and mode is not None
                and (stat.S_ISREG(mode) or stat.S_ISLNK(mode))
            ):
                paths.add(candidate.relative_to(workspace).as_posix())
                if len(paths) > MAX_CANONICAL_ENTRIES:
                    raise WorkspaceHistoryError("Canonical Course state has too many files")
    return paths


def _presentation_yaml_paths(workspace: Path) -> list[Path]:
    """List presentation candidates without allowing arbitrary directory scans."""
    presentations = workspace / "presentations"
    candidates: list[Path] = []
    entries_seen = 0
    try:
        with os.scandir(presentations) as entries:
            for entry in entries:
                entries_seen += 1
                if entries_seen > MAX_PRESENTATIONS_DIRECTORY_ENTRIES:
                    raise WorkspaceHistoryError("Course presentations directory is too large")
                if entry.name.endswith(".yaml"):
                    candidates.append(Path(entry.path))
    except OSError as error:
        raise WorkspaceHistoryError(HISTORY_UNSAFE) from error
    return sorted(candidates)


def _canonical_structure_findings(workspace: Path) -> list[str]:
    """Describe canonical-path shapes that cannot be represented as captured blobs."""
    findings: list[str] = []
    for name in CANONICAL_TOP_LEVEL_FILES:
        candidate = workspace / name
        mode = _lstat_mode(candidate)
        if mode is not None and not (stat.S_ISREG(mode) or stat.S_ISLNK(mode)):
            findings.append(f"{name} must be a regular file when it exists")
    presentations = workspace / "presentations"
    if presentations.is_symlink():
        return [*findings, "presentations is a symbolic link and is not canonical Course state"]
    if _path_exists_or_symlink(presentations) and not _is_ordinary_directory(presentations):
        return [*findings, "presentations must be a directory when it exists"]
    if not _is_ordinary_directory(presentations):
        return findings
    for candidate in _presentation_yaml_paths(workspace):
        path = candidate.relative_to(workspace).as_posix()
        if not _is_safe_path_text(path):
            findings.append("A Presentation filename is not valid UTF-8")
    return findings


def _head_canonical_paths(workspace: Path) -> set[str]:
    """List canonical paths in HEAD; an unborn repository has no such paths."""
    head = _head_oid(workspace)
    return set(_tree_blobs(workspace, head)) if head is not None else set()


def _staged_canonical_paths(workspace: Path) -> set[str]:
    result = _git_result_bounded(
        workspace, MAX_CANONICAL_ENTRIES * 512, "diff", "--cached", "--name-only", "-z"
    )
    if result.returncode != 0:
        raise WorkspaceHistoryError(HISTORY_UNSAFE)
    output = result.stdout.decode("utf-8", errors="surrogateescape")
    return {path for path in output.split("\0") if _is_canonical_path(path)}


def _line_count(entry: _CapturedEntry) -> int | None:
    if entry.mode == "120000":
        return None
    try:
        text = entry.content.decode("utf-8")
    except UnicodeError:
        return None
    return 0 if not text else text.count("\n") + (not text.endswith("\n"))


def _diff_preview(
    path: str, before: _CapturedEntry | None, after: _CapturedEntry | None
) -> tuple[list[str], bool]:
    """Return a bounded unified diff suitable for Course Author inspection."""

    def content_lines(entry: _CapturedEntry | None) -> list[str]:
        if entry is None:
            return []
        if entry.mode == "120000":
            return [f"symbolic link → {os.fsdecode(entry.content)}"]
        try:
            return entry.content.decode("utf-8").splitlines()
        except UnicodeDecodeError:
            return ["[Content is not valid UTF-8 and cannot be previewed]"]

    generated = difflib.unified_diff(
        content_lines(before),
        content_lines(after),
        fromfile=f"{path} · saved",
        tofile=f"{path} · current",
        lineterm="",
    )
    preview: list[str] = []
    characters = 0
    for line in generated:
        if len(preview) >= MAX_CURRENT_STATE_DIFF_LINES:
            return preview, True
        remaining = MAX_CURRENT_STATE_DIFF_CHARS - characters
        if remaining <= 0:
            return preview, True
        if len(line) > remaining:
            preview.append(f"{line[: max(remaining - 1, 0)]}…")
            return preview, True
        preview.append(line)
        characters += len(line)
    return preview, False


def _changes_against_head(
    current_blobs: dict[str, _CapturedEntry],
    head_blobs: dict[str, _CapturedEntry],
) -> list[CurrentStateEntry]:
    current_paths = set(current_blobs)
    head_paths = set(head_blobs)
    changes: list[CurrentStateEntry] = []
    for path in sorted(current_paths | head_paths):
        if path not in current_paths:
            diff_lines, diff_truncated = _diff_preview(path, head_blobs[path], None)
            changes.append(
                CurrentStateEntry(
                    path=path,
                    status="deleted",
                    diff_lines=diff_lines,
                    diff_truncated=diff_truncated,
                )
            )
        elif path not in head_paths:
            diff_lines, diff_truncated = _diff_preview(path, None, current_blobs[path])
            changes.append(
                CurrentStateEntry(
                    path=path,
                    status="added",
                    line_count=_line_count(current_blobs[path]),
                    diff_lines=diff_lines,
                    diff_truncated=diff_truncated,
                )
            )
        elif current_blobs[path] != head_blobs[path]:
            diff_lines, diff_truncated = _diff_preview(path, head_blobs[path], current_blobs[path])
            changes.append(
                CurrentStateEntry(
                    path=path,
                    status="modified",
                    line_count=_line_count(current_blobs[path]),
                    diff_lines=diff_lines,
                    diff_truncated=diff_truncated,
                )
            )
    return changes


def _validate_complete_state(
    workspace: Path,
    current_paths: set[str],
    head_paths: set[str],
) -> CurrentStateValidation:
    findings: list[str] = []
    course: CoursePlan | None = None
    sources: SourcesIndex | None = None
    course_path = workspace / "course.yaml"
    sources_path = workspace / "sources.yaml"
    if course_path.is_symlink():
        findings.append("course.yaml is a symbolic link and is not canonical Course state")
    elif _path_exists_or_symlink(course_path):
        try:
            course = read_course_plan(workspace)
        except InvalidCoursePlan as error:
            findings.append(f"course.yaml is invalid: {error}")
    elif "course.yaml" in head_paths or current_paths - {"course.yaml"}:
        findings.append("course.yaml is missing from established Course state")

    if sources_path.is_symlink():
        findings.append("sources.yaml is a symbolic link and is not canonical Course state")
    else:
        try:
            sources = read_sources_index(workspace)
        except InvalidSourcesIndex as error:
            findings.append(f"sources.yaml is invalid: {error}")

    presentations = workspace / "presentations"
    if presentations.is_symlink():
        findings.append("presentations is a symbolic link and is not canonical Course state")
    elif _path_exists_or_symlink(presentations) and not _is_ordinary_directory(presentations):
        findings.append("presentations must be a directory when it exists")
    loaded_presentations: list[tuple[str, Presentation]] = []
    if _is_ordinary_directory(presentations):
        for candidate in _presentation_yaml_paths(workspace):
            path = candidate.relative_to(workspace).as_posix()
            if not _is_safe_path_text(path):
                findings.append("A Presentation filename is not valid UTF-8")
                continue
            if candidate.is_symlink():
                findings.append(f"{path} is a symbolic link and is not canonical Course state")
                continue
            try:
                presentation = read_presentation(workspace, candidate.stem)
            except InvalidPresentation as error:
                findings.append(f"{path} is invalid: {error}")
                continue
            if presentation is None:
                continue
            if candidate.name != f"{presentation.id}.yaml":
                findings.append(f"{path} filename does not match Presentation ID {presentation.id}")
            loaded_presentations.append((path, presentation))

    if course is not None:
        lectures = {lecture.id: lecture for lecture in course.lectures}
        source_ids = {source.id for source in sources.sources} if sources is not None else set()
        if sources is not None:
            seen_source_ids: set[str] = set()
            seen_resource_ids: set[str] = set()
            for source in sources.sources:
                if source.id in seen_source_ids:
                    findings.append(f"Source ID {source.id} appears more than once")
                seen_source_ids.add(source.id)
                if source.resource_id in seen_resource_ids:
                    findings.append(f"Resource ID {source.resource_id} is admitted more than once")
                seen_resource_ids.add(source.resource_id)
        for lecture in course.lectures:
            for source_id in lecture.source_focus or []:
                if source_id not in source_ids:
                    findings.append(
                        f"Lecture {lecture.id} Source Focus references missing Source {source_id}"
                    )

        by_id: dict[str, list[Presentation]] = {}
        by_lecture: dict[str, list[Presentation]] = {}
        for _, presentation in loaded_presentations:
            by_id.setdefault(presentation.id, []).append(presentation)
            by_lecture.setdefault(presentation.lecture_id, []).append(presentation)
            if presentation.lecture_id not in lectures:
                findings.append(
                    f"Presentation {presentation.id} references missing Lecture "
                    f"{presentation.lecture_id}"
                )
            for slide in presentation.slides:
                for citation in slide.citations:
                    if citation.source_id not in source_ids:
                        findings.append(
                            f"Slide {slide.id} Citation references missing Source "
                            f"{citation.source_id}"
                        )
        for presentation_id, matches in by_id.items():
            if len(matches) > 1:
                findings.append(f"Presentation ID {presentation_id} appears in multiple files")
        for lecture_id, matches in by_lecture.items():
            if len(matches) > 1:
                findings.append(f"Lecture {lecture_id} has multiple Presentations")
        for lecture in course.lectures:
            if lecture.presentation_id is None:
                if by_lecture.get(lecture.id):
                    findings.append(f"Lecture {lecture.id} does not link to its Presentation")
                continue
            matches = by_id.get(lecture.presentation_id, [])
            if not matches:
                findings.append(
                    f"Lecture {lecture.id} links to missing Presentation {lecture.presentation_id}"
                )
            elif len(matches) == 1 and matches[0].lecture_id != lecture.id:
                findings.append(
                    f"Lecture {lecture.id} links to Presentation {lecture.presentation_id} "
                    "for another Lecture"
                )

    return CurrentStateValidation(valid=not findings, findings=findings)


@_serialized
def read_current_state(workspace: Path) -> CurrentState:
    """Read the final canonical working tree relative to HEAD and validate its graph."""
    _require_private_workspace_repository(workspace)
    _recover_pending_transaction(workspace)
    _recover_abandoned_run(workspace)
    _recover_pending_provenance(workspace)
    structure_findings = _canonical_structure_findings(workspace)
    blobs = _capture_canonical_blobs(workspace)
    state = _read_current_state(workspace, blobs, structure_findings)
    baseline = _read_provenance(workspace)
    if baseline is None:
        current = _fingerprints(blobs)
        return state.model_copy(
            update={
                "drift_id": _drift_id(None, current),
                "drift_changes": state.changes,
            }
        )
    current = _fingerprints(blobs)
    changes = _fingerprint_changes(baseline, current)
    return state.model_copy(
        update={
            "drift": "clean" if not changes else "drift",
            "drift_id": _drift_id(baseline, current) if changes else None,
            "drift_changes": changes,
        }
    )


def _fingerprints(blobs: dict[str, _CapturedEntry]) -> dict[str, dict[str, str]]:
    return {
        path: {"mode": entry.mode, "sha256": hashlib.sha256(entry.content).hexdigest()}
        for path, entry in blobs.items()
    }


def _drift_id(
    baseline: dict[str, dict[str, str]] | None, current: dict[str, dict[str, str]]
) -> str:
    """Opaque identity for precisely the baseline/current capture being reconciled."""
    material = json.dumps(
        {"baseline": baseline, "current": current}, sort_keys=True, separators=(",", ":")
    ).encode("ascii")
    return hashlib.sha256(material).hexdigest()


def _read_provenance(workspace: Path) -> dict[str, dict[str, str]] | None:
    record = _read_private_record(workspace, PROVENANCE_FILE, max_bytes=1_000_000)
    if record is None:
        return None
    try:
        payload = json.loads(record)
        fingerprints = payload["canonical"]
        if payload.get("version") != 1 or not isinstance(fingerprints, dict):
            raise ValueError
        if any(
            not _is_canonical_path(path) or not _is_fingerprint(value)
            for path, value in fingerprints.items()
        ):
            raise ValueError
        return cast(dict[str, dict[str, str]], fingerprints)
    except KeyError, TypeError, ValueError, json.JSONDecodeError:
        raise WorkspaceHistoryError(HISTORY_UNSAFE) from None


def _is_fingerprint(value: object) -> bool:
    if not isinstance(value, dict):
        return False
    mode = value.get("mode")
    digest = value.get("sha256")
    return (
        set(value) == {"mode", "sha256"}
        and mode in {"100644", "100755", "120000"}
        and isinstance(digest, str)
        and re.fullmatch(r"[0-9a-f]{64}", digest) is not None
    )


def _fingerprint_changes(
    before: dict[str, dict[str, str]], after: dict[str, dict[str, str]]
) -> list[CurrentStateEntry]:
    changes: list[CurrentStateEntry] = []
    for path in sorted(before.keys() | after.keys()):
        status: Literal["added", "modified", "deleted"]
        if path not in before:
            status = "added"
        elif path not in after:
            status = "deleted"
        elif before[path] != after[path]:
            status = "modified"
        else:
            continue
        changes.append(CurrentStateEntry(path=path, status=status))
    return changes


@_serialized
def capture_reconciliation_context(workspace: Path) -> ReconciliationContext:
    """Return only bounded canonical UTF-8 text and validation findings to a reconciler."""
    _require_private_workspace_repository(workspace)
    _recover_pending_transaction(workspace)
    _recover_abandoned_run(workspace)
    _recover_pending_provenance(workspace)
    context, _blobs, _baseline = _capture_reconciliation_snapshot(workspace)
    return context


def _capture_reconciliation_snapshot(
    workspace: Path,
) -> tuple[
    ReconciliationContext,
    dict[str, _CapturedEntry],
    dict[str, dict[str, str]] | None,
]:
    """Capture one exact drift view plus bounded recovery material from trusted history."""
    blobs = _capture_canonical_blobs(workspace)
    baseline = _read_provenance(workspace)
    head = _head_oid(workspace)
    head_blobs = _tree_blobs(workspace, head) if head else {}
    head_paths = set(head_blobs)
    validation = _validate_captured_blobs(workspace, blobs, head_paths)
    current = _fingerprints(blobs)
    if baseline is None:
        head_fingerprints = _fingerprints(head_blobs)
        unresolved = current != head_fingerprints
        trusted_baseline = head_fingerprints
    else:
        unresolved = baseline != current
        trusted_baseline = baseline
    if not unresolved:
        raise ValueError("Workspace Drift has no changes to reconcile")
    total = 0
    files: list[ReconciliationFile] = []
    for path, entry in sorted(blobs.items()):
        if entry.mode == "120000" or len(entry.content) > MAX_RECONCILIATION_FILE_BYTES:
            raise ValueError("Workspace Drift contains a file that cannot be reconciled safely")
        total += len(entry.content)
        if total > MAX_RECONCILIATION_TOTAL_BYTES:
            raise ValueError("Workspace Drift is too large to reconcile safely")
        try:
            files.append(ReconciliationFile(path=path, content=entry.content.decode("utf-8")))
        except UnicodeDecodeError as error:
            raise ValueError("Workspace Drift contains non-UTF-8 canonical text") from error
    baseline_files: list[ReconciliationFile] = []
    missing_paths = sorted(trusted_baseline.keys() - current.keys())
    for path in missing_paths:
        entry = head_blobs.get(path)
        if entry is None or _fingerprints({path: entry})[path] != trusted_baseline[path]:
            continue
        if entry.mode == "120000" or len(entry.content) > MAX_RECONCILIATION_FILE_BYTES:
            raise ValueError("Workspace Drift contains a file that cannot be reconciled safely")
        total += len(entry.content)
        if total > MAX_RECONCILIATION_TOTAL_BYTES:
            raise ValueError("Workspace Drift is too large to reconcile safely")
        try:
            baseline_files.append(
                ReconciliationFile(path=path, content=entry.content.decode("utf-8"))
            )
        except UnicodeDecodeError as error:
            raise ValueError("Workspace Drift contains non-UTF-8 canonical text") from error
    if _capture_canonical_blobs(workspace) != blobs:
        raise ValueError("Workspace Drift changed while reconciliation was being captured")
    return (
        ReconciliationContext(
            drift_id=_drift_id(baseline, current),
            files=files,
            baseline_files=baseline_files,
            missing_paths=missing_paths,
            findings=validation.findings,
        ),
        blobs,
        baseline,
    )


@_serialized
def apply_reconciliation(workspace: Path, request: ReconciliationApplyRequest) -> CourseRevision:
    """Atomically apply a server-validated canonical repair and record a Revision."""
    _require_private_workspace_repository(workspace)
    context, reviewed_blobs, baseline = _capture_reconciliation_snapshot(workspace)
    if request.drift_id != context.drift_id:
        raise WorkspaceDriftChangedError(
            "Workspace Drift changed; capture a fresh reconciliation context"
        )
    paths = [entry.path for entry in request.entries]
    if len(paths) != len(set(paths)) or any(not _is_canonical_path(path) for path in paths):
        raise ValueError("Reconciliation entries must use unique canonical paths")
    allowed_paths = set(reviewed_blobs)
    if baseline is not None:
        allowed_paths.update(baseline)
    else:
        allowed_paths.update(context.missing_paths)
    if any(path not in allowed_paths for path in paths):
        raise ValueError("Reconciliation entries must use paths from the reviewed context")
    total = 0
    for entry in request.entries:
        if entry.content is not None:
            encoded = entry.content.encode("utf-8")
            total += len(encoded)
            if len(encoded) > MAX_RECONCILIATION_FILE_BYTES:
                raise ValueError("Reconciliation entry is too large")
    if total > MAX_RECONCILIATION_TOTAL_BYTES:
        raise ValueError("Reconciliation is too large")
    recovery_ref, snapshot = _begin_transaction(workspace)
    committed = False
    expected_blobs = dict(reviewed_blobs)
    try:
        if _tree_blobs(workspace, snapshot) != reviewed_blobs:
            raise WorkspaceDriftChangedError(
                "Workspace Drift changed; capture a fresh reconciliation context"
            )
        updates: dict[str, bytes | None] = {}
        for entry in request.entries:
            if entry.content is None:
                expected_blobs.pop(entry.path, None)
                updates[entry.path] = None
            else:
                encoded = entry.content.encode("utf-8")
                expected_blobs[entry.path] = _CapturedEntry(mode="100644", content=encoded)
                updates[entry.path] = encoded
        apply_canonical_mutation(
            workspace,
            expected={path: _canonical_file(reviewed_blobs.get(path)) for path in updates},
            updates=updates,
        )
        blobs = _capture_canonical_blobs(workspace)
        if blobs != expected_blobs:
            raise WorkspaceDriftChangedError(
                "Workspace Drift changed while reconciliation was being applied"
            )
        validation = _validate_captured_blobs(workspace, blobs, _head_canonical_paths(workspace))
        if not validation.valid:
            raise ValueError("Reconciliation would leave Current State invalid")
        expected = _fingerprints(blobs)
        revision = _commit_current(
            workspace,
            request.summary,
            "refs/heads/main",
            blobs=blobs,
            recover=False,
            before_update=lambda commit, previous: _write_pending_provenance(
                workspace, "prepared", expected, commit, previous
            ),
        )
        committed = True
        # From this point main is durable.  Recovery must retain the committed tree,
        # while the provenance pending record completes any interrupted metadata write.
        _write_transaction(workspace, "completed", snapshot, recovery_ref)
        _persist_committed_provenance(workspace, revision, expected)
        _complete_transaction(workspace, snapshot)
        return CourseRevision(
            id=_revision_id(revision),
            summary=request.summary,
            created_at=_commit_created_at(workspace, revision),
        )
    except Exception:
        if not committed:
            # A reconciliation may fail after its replacement has reached disk.
            # Restore the transaction snapshot only while every changed path is
            # still exactly the repair's output; a later Course Author edit is
            # never an acceptable rollback target.
            snapshot_blobs = _tree_blobs(workspace, snapshot)
            changed_paths = {
                path
                for path in snapshot_blobs.keys() | expected_blobs.keys()
                if snapshot_blobs.get(path) != expected_blobs.get(path)
            }
            if changed_paths and all(
                entry.mode != "120000"
                for entry in [
                    *[expected_blobs[path] for path in changed_paths if path in expected_blobs],
                    *[snapshot_blobs[path] for path in changed_paths if path in snapshot_blobs],
                ]
            ):
                restore_canonical_mutation(
                    workspace,
                    expected_current={
                        path: _canonical_file(expected_blobs.get(path)) for path in changed_paths
                    },
                    restore={
                        path: _canonical_file(snapshot_blobs.get(path)) for path in changed_paths
                    },
                )
            _complete_transaction(workspace, snapshot)
        raise


@_serialized
def record_app_authored_state(workspace: Path) -> None:
    _require_private_workspace_repository(workspace)
    _recover_pending_provenance(workspace)
    blobs = _capture_canonical_blobs(workspace)
    if not _validate_captured_blobs(workspace, blobs, _head_canonical_paths(workspace)).valid:
        raise ValueError("Current State must be valid before recording provenance")
    if _capture_canonical_blobs(workspace) != blobs:
        raise ValueError("Current State changed while provenance was being recorded")
    payload = json.dumps({"version": 1, "canonical": _fingerprints(blobs)}, separators=(",", ":"))
    _write_blob(workspace / ".git", PROVENANCE_FILE, payload.encode("ascii"))


@_serialized
def record_app_authored_entries(workspace: Path, expected_entries: dict[str, bytes | None]) -> None:
    """Trust only exact canonical bytes produced by a successful app mutation."""
    _require_private_workspace_repository(workspace)
    _recover_pending_provenance(workspace)
    if not expected_entries or any(not _is_canonical_path(path) for path in expected_entries):
        raise ValueError("App-authored entries must use canonical Course paths")
    if any(
        content is not None and len(content) > MAX_CANONICAL_FILE_BYTES
        for content in expected_entries.values()
    ):
        raise ValueError("An app-authored canonical file is too large")
    baseline = _read_provenance(workspace)
    blobs = _capture_canonical_blobs(workspace)
    for path, expected in expected_entries.items():
        actual = blobs.get(path)
        if expected is None:
            if actual is not None:
                raise ValueError("Canonical Course state changed after the app mutation")
        elif actual is None or actual.mode != "100644" or actual.content != expected:
            raise ValueError("Canonical Course state changed after the app mutation")
    if baseline is None and set(blobs) != {
        path for path, content in expected_entries.items() if content is not None
    }:
        raise ValueError("Existing canonical state has no trusted provenance")
    if _capture_canonical_blobs(workspace) != blobs:
        raise ValueError("Current State changed while provenance was being recorded")
    updated = {} if baseline is None else dict(baseline)
    current = _fingerprints(blobs)
    for path, expected in expected_entries.items():
        if expected is None:
            updated.pop(path, None)
        else:
            updated[path] = current[path]
    payload = json.dumps({"version": 1, "canonical": updated}, separators=(",", ":"))
    _write_blob(workspace / ".git", PROVENANCE_FILE, payload.encode("ascii"))


@_serialized
def record_app_authored_paths(workspace: Path, paths: set[str]) -> None:
    """Advance provenance only for successful app-owned canonical path mutations."""
    _require_private_workspace_repository(workspace)
    _recover_pending_provenance(workspace)
    if not paths or any(not _is_canonical_path(path) for path in paths):
        raise ValueError("App-authored paths must be canonical Course paths")
    baseline = _read_provenance(workspace)
    if baseline is None:
        raise ValueError("Workspace Drift provenance has not been established")
    blobs = _capture_canonical_blobs(workspace)
    head = _head_oid(workspace)
    head_blobs = _tree_blobs(workspace, head) if head is not None else {}
    for path in paths:
        if blobs.get(path) != head_blobs.get(path):
            raise ValueError("Reverted Course state changed before provenance was recorded")
    if _capture_canonical_blobs(workspace) != blobs:
        raise ValueError("Current State changed while provenance was being recorded")
    updated = dict(baseline)
    current = _fingerprints(blobs)
    for path in paths:
        if path in current:
            updated[path] = current[path]
        else:
            updated.pop(path, None)
    payload = json.dumps({"version": 1, "canonical": updated}, separators=(",", ":"))
    _write_blob(workspace / ".git", PROVENANCE_FILE, payload.encode("ascii"))


@_serialized
def record_restored_revision(workspace: Path, revision_id: str) -> None:
    """Record provenance only if Current State still equals the restored Revision tree."""
    _require_private_workspace_repository(workspace)
    revisions = {revision.id for revision in list_revisions(workspace)}
    if revision_id not in revisions:
        raise ValueError("Course Revision was not found")
    oid = revision_id.removeprefix("revision-")
    expected = _tree_blobs(workspace, oid)
    current = _capture_canonical_blobs(workspace)
    if current != expected:
        raise ValueError("Restored Course state changed before provenance was recorded")
    if not _validate_captured_blobs(workspace, current, _head_canonical_paths(workspace)).valid:
        raise ValueError("Restored Course state must be valid before recording provenance")
    payload = json.dumps(
        {"version": 1, "canonical": _fingerprints(expected)}, separators=(",", ":")
    )
    _write_blob(workspace / ".git", PROVENANCE_FILE, payload.encode("ascii"))


@_serialized
def accept_workspace_drift(workspace: Path, request: DriftAcceptRequest) -> CourseRevision:
    _require_private_workspace_repository(workspace)
    _recover_pending_transaction(workspace)
    _recover_abandoned_run(workspace)
    _recover_pending_provenance(workspace)
    blobs = _capture_canonical_blobs(workspace)
    baseline = _read_provenance(workspace)
    current = _fingerprints(blobs)
    head = _head_oid(workspace)
    head_blobs = _tree_blobs(workspace, head) if head else {}
    comparison = _fingerprints(head_blobs) if baseline is None else baseline
    if baseline is not None and comparison == current:
        raise ValueError("Workspace Drift has no changes to accept")
    if _drift_id(baseline, current) != request.drift_id:
        raise WorkspaceDriftChangedError(
            "Workspace Drift changed; review it again before acceptance"
        )
    if not _validate_captured_blobs(workspace, blobs, set(head_blobs)).valid:
        raise ValueError("Workspace Drift must be valid before acceptance")
    expected = _fingerprints(blobs)
    revision = _commit_current(
        workspace,
        request.summary,
        "refs/heads/main",
        blobs=blobs,
        before_update=lambda commit, previous: _write_pending_provenance(
            workspace, "prepared", expected, commit, previous
        ),
    )
    _persist_committed_provenance(workspace, revision, expected)
    return CourseRevision(
        id=_revision_id(revision),
        summary=request.summary,
        created_at=_commit_created_at(workspace, revision),
    )


@_serialized
def create_recovery_snapshot(workspace: Path) -> str:
    """Capture hidden raw canonical state without advancing visible Course history."""
    _require_private_workspace_repository(workspace)
    _recover_pending_transaction(workspace)
    _recover_abandoned_run(workspace)
    _ref, snapshot = _recovery_snapshot(workspace)
    return snapshot


@_serialized
def begin_run_boundary(workspace: Path) -> str:
    """Durably mark a run boundary and retain its hidden pre-run snapshot."""
    _require_private_workspace_repository(workspace)
    _recover_pending_transaction(workspace)
    _recover_abandoned_run(workspace)
    baseline_valid = _read_current_state(workspace).validation.valid
    ref, snapshot = _recovery_snapshot(workspace)
    _write_run_boundary(workspace, "active", snapshot, baseline_valid, False, ref)
    _ACTIVE_RUNS[workspace.resolve()] = (snapshot, baseline_valid, False, ref)
    return snapshot


@_serialized
def mark_run_mutation(workspace: Path, snapshot: str) -> None:
    """Durably attribute an imminent mutation to the active Course Agent run."""
    active = _parse_run_boundary(_read_private_record(workspace, RUN_BOUNDARY_FILE))
    if active is None or active[0] != "active" or active[1] != snapshot:
        raise WorkspaceHistoryError(HISTORY_UNSAFE)
    _phase, _snapshot, baseline_valid, _mutating, ref = active
    _write_run_boundary(workspace, "active", snapshot, baseline_valid, True, ref)
    _ACTIVE_RUNS[workspace.resolve()] = (snapshot, baseline_valid, True, ref)


@_serialized
def checkpoint_run_mutation(workspace: Path, snapshot: str) -> str:
    """Record the exact valid tree reached by a completed agent mutation."""
    active = _parse_run_boundary(_read_private_record(workspace, RUN_BOUNDARY_FILE))
    if active is None or active[0] != "active" or active[1] != snapshot or not active[3]:
        raise WorkspaceHistoryError(HISTORY_UNSAFE)
    _phase, _snapshot, baseline_valid, _mutating, previous_ref = active
    if not _read_current_state(workspace).validation.valid:
        raise ValueError("Course Agent mutation did not produce valid Current State")
    ref, checkpoint = _recovery_snapshot(workspace)
    _write_run_boundary(workspace, "active", checkpoint, baseline_valid, False, ref)
    _ACTIVE_RUNS[workspace.resolve()] = (checkpoint, baseline_valid, False, ref)
    _delete_recovery_ref(workspace, previous_ref, snapshot)
    return checkpoint


@_serialized
def finish_run_boundary(workspace: Path, snapshot: str) -> CurrentState:
    """Close a run boundary without overwriting unverified working-tree bytes."""
    _require_private_workspace_repository(workspace)
    if not OID_PATTERN.fullmatch(snapshot):
        raise WorkspaceHistoryError(HISTORY_UNSAFE)
    active = _parse_run_boundary(_read_private_record(workspace, RUN_BOUNDARY_FILE))
    if active is None or active[0] != "active" or active[1] != snapshot:
        raise WorkspaceHistoryError(HISTORY_UNSAFE)
    _phase, _snapshot, baseline_valid, agent_mutating, ref = active
    try:
        _read_current_state(workspace)
    finally:
        _ACTIVE_RUNS.pop(workspace.resolve(), None)
    _write_run_boundary(workspace, "completed", snapshot, baseline_valid, agent_mutating, ref)
    _delete_recovery_ref(workspace, ref, snapshot)
    _clear_private_record(workspace, RUN_BOUNDARY_FILE)
    return _read_current_state(workspace)


@_serialized
def abandon_run_boundary(workspace: Path, snapshot: str) -> None:
    """Leave the durable marker for recovery after an in-process finalization failure."""
    active = _ACTIVE_RUNS.get(workspace.resolve())
    if active is not None and active[0] == snapshot:
        _ACTIVE_RUNS.pop(workspace.resolve(), None)


def _read_current_state(
    workspace: Path,
    current_blobs: dict[str, _CapturedEntry] | None = None,
    structure_findings: list[str] | None = None,
) -> CurrentState:
    """Read state after repository checks and pending recovery have completed."""
    current_blobs = _capture_canonical_blobs(workspace) if current_blobs is None else current_blobs
    structure_findings = (
        _canonical_structure_findings(workspace)
        if structure_findings is None
        else structure_findings
    )
    head = _head_oid(workspace)
    head_blobs = _tree_blobs(workspace, head) if head is not None else {}
    changes = _changes_against_head(current_blobs, head_blobs)
    validation = _validate_captured_blobs(workspace, current_blobs, set(head_blobs))
    if structure_findings:
        findings = [*validation.findings, *structure_findings]
        validation = CurrentStateValidation(valid=False, findings=list(dict.fromkeys(findings)))
    return CurrentState(
        clean=not changes,
        changes=changes,
        validation=validation,
    )


def _head_oid(workspace: Path) -> str | None:
    result = _git_result(workspace, "rev-parse", "--verify", "--quiet", "refs/heads/main")
    if result.returncode != 0:
        return None
    try:
        oid = result.stdout.decode("ascii", errors="strict").strip()
    except UnicodeError as error:
        raise WorkspaceHistoryError(HISTORY_UNSAFE) from error
    if not OID_PATTERN.fullmatch(oid):
        raise WorkspaceHistoryError(HISTORY_UNSAFE)
    return oid


def _capture_canonical_blobs(workspace: Path) -> dict[str, _CapturedEntry]:
    blobs: dict[str, _CapturedEntry] = {}
    total_bytes = 0
    for path in sorted(_current_canonical_paths(workspace)):
        candidate = workspace / path
        directory_flags = os.O_RDONLY | getattr(os, "O_DIRECTORY", 0) | getattr(os, "O_NOFOLLOW", 0)
        try:
            directory_fd = os.open(candidate.parent, directory_flags)
        except OSError as error:
            raise WorkspaceHistoryError(HISTORY_UNSAFE) from error
        try:
            metadata = os.stat(candidate.name, dir_fd=directory_fd, follow_symlinks=False)
            if stat.S_ISLNK(metadata.st_mode):
                target = os.readlink(candidate.name, dir_fd=directory_fd)
                content = os.fsencode(target)
                total_bytes += len(content)
                if total_bytes > MAX_CANONICAL_TOTAL_BYTES:
                    raise WorkspaceHistoryError("Canonical Course state is too large to inspect")
                blobs[path] = _CapturedEntry(mode="120000", content=content)
                continue
            if not stat.S_ISREG(metadata.st_mode):
                continue
            if metadata.st_size > MAX_CANONICAL_FILE_BYTES:
                raise WorkspaceHistoryError("A canonical Course file is too large to inspect")
            descriptor = os.open(
                candidate.name,
                os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0),
                dir_fd=directory_fd,
            )
            with os.fdopen(descriptor, "rb", closefd=True) as stream:
                content = stream.read(MAX_CANONICAL_FILE_BYTES + 1)
            if len(content) > MAX_CANONICAL_FILE_BYTES:
                raise WorkspaceHistoryError("A canonical Course file is too large to inspect")
            total_bytes += len(content)
            if total_bytes > MAX_CANONICAL_TOTAL_BYTES:
                raise WorkspaceHistoryError("Canonical Course state is too large to inspect")
            mode: Literal["100644", "100755"] = (
                "100755" if metadata.st_mode & stat.S_IXUSR else "100644"
            )
            blobs[path] = _CapturedEntry(mode=mode, content=content)
        except OSError as error:
            raise WorkspaceHistoryError(HISTORY_UNSAFE) from error
        finally:
            os.close(directory_fd)
    return blobs


def _validate_captured_blobs(
    workspace: Path, blobs: dict[str, _CapturedEntry], head_paths: set[str]
) -> CurrentStateValidation:
    """Validate the immutable bytes that will be written to a commit."""
    dot_git = workspace / ".git"
    with tempfile.TemporaryDirectory(dir=dot_git, prefix="course-harness-validate-") as directory:
        shadow = Path(directory)
        for path, entry in blobs.items():
            target = shadow / path
            target.parent.mkdir(parents=True, exist_ok=True)
            if entry.mode == "120000":
                target.symlink_to(os.fsdecode(entry.content))
            else:
                target.write_bytes(entry.content)
        return _validate_complete_state(shadow, set(blobs), head_paths)


def _commit_current(
    workspace: Path,
    summary: str,
    ref: str,
    *,
    require_valid: bool = True,
    blobs: dict[str, _CapturedEntry] | None = None,
    recover: bool = True,
    before_update: Callable[[str, str | None], None] | None = None,
) -> str:
    """Commit a stable capture of canonical files through an isolated index."""
    _require_private_workspace_repository(workspace)
    if recover:
        _recover_pending_transaction(workspace)
        _recover_abandoned_run(workspace)
    head_paths = _head_canonical_paths(workspace)
    blobs = _capture_canonical_blobs(workspace) if blobs is None else blobs
    validation = _validate_captured_blobs(workspace, blobs, head_paths)
    if require_valid and not validation.valid:
        raise ValueError("Current State must be valid before creating a Revision")
    dot_git = workspace / ".git"
    head = _head_oid(workspace)
    with tempfile.TemporaryDirectory(dir=dot_git, prefix="course-harness-index-") as directory:
        index = Path(directory) / "index"
        if head is None:
            _git_with_index(workspace, index, "read-tree", "--empty")
        else:
            _git_with_index(workspace, index, "read-tree", "HEAD")
        for path in sorted(head_paths - set(blobs)):
            _git_with_index(workspace, index, "update-index", "--force-remove", "--", path)
        for path, entry in sorted(blobs.items()):
            oid = (
                _git_with_index(
                    workspace,
                    index,
                    "hash-object",
                    "-w",
                    "--stdin",
                    input_data=entry.content,
                )
                .stdout.decode()
                .strip()
            )
            record = f"{entry.mode} {oid}\t{path}\0".encode()
            _git_with_index(
                workspace, index, "update-index", "-z", "--index-info", input_data=record
            )
        tree = _git_with_index(workspace, index, "write-tree").stdout.decode().strip()
        arguments = ["commit-tree", tree]
        if head is not None:
            arguments.extend(["-p", head])
        commit = (
            _git_with_index(workspace, index, *arguments, input_data=(summary + "\n").encode())
            .stdout.decode()
            .strip()
        )
    if _capture_canonical_blobs(workspace) != blobs:
        raise ValueError("Current State changed while the Course Revision was being created")
    if before_update is not None:
        before_update(commit, head)
    expected = head if ref == "refs/heads/main" and head is not None else "0" * len(commit)
    _git(workspace, "update-ref", ref, commit, expected)
    return commit


def _revision_id(oid: str) -> str:
    return f"revision-{oid}"


@_serialized
def list_revisions(workspace: Path) -> list[CourseRevision]:
    _require_private_workspace_repository(workspace)
    _recover_pending_transaction(workspace)
    _recover_abandoned_run(workspace)
    head = _head_oid(workspace)
    if head is None:
        return []
    result = _git_result(
        workspace, "rev-list", "--max-count=500", "--first-parent", "refs/heads/main"
    )
    if result.returncode != 0:
        raise WorkspaceHistoryError(HISTORY_UNSAFE)
    revisions: list[CourseRevision] = []
    try:
        oids = result.stdout.decode("ascii", errors="strict").splitlines()
        for oid in oids:
            if not OID_PATTERN.fullmatch(oid):
                raise ValueError
            metadata = _git_result_bounded(
                workspace,
                MAX_REVISION_METADATA_BYTES,
                "show",
                "-s",
                "--format=%s%x00%ct",
                oid,
            )
            if metadata.returncode != 0:
                raise ValueError
            fields = metadata.stdout.rstrip(b"\n").split(b"\0")
            if len(fields) != 2:
                raise ValueError
            summary = fields[0].decode("utf-8", errors="strict")
            if not summary or any(
                ord(character) < 32 or ord(character) == 127 for character in summary
            ):
                raise ValueError
            timestamp = int(fields[1].decode("ascii", errors="strict"))
            revisions.append(
                CourseRevision(
                    id=_revision_id(oid),
                    summary=summary,
                    created_at=datetime.fromtimestamp(timestamp, UTC).isoformat(),
                )
            )
    except (OSError, OverflowError, UnicodeError, ValueError) as error:
        raise WorkspaceHistoryError(HISTORY_UNSAFE) from error
    return revisions


@_serialized
def create_revision(
    workspace: Path,
    request: RevisionCreateRequest,
    *,
    expected: Mapping[str, CanonicalFile] | None = None,
) -> CourseRevision:
    _require_private_workspace_repository(workspace)
    if read_current_state(workspace).clean:
        raise ValueError("Current State has no changes to capture as a Course Revision")
    blobs = _capture_canonical_blobs(workspace)
    provenance = _read_provenance(workspace)
    if expected is not None:
        actual_paths = set(blobs)
        expected_paths = {path for path, entry in expected.items() if entry.content is not None}
        if actual_paths != expected_paths or any(
            _canonical_file(blobs.get(path)) != entry for path, entry in expected.items()
        ):
            raise ValueError("Current State changed before the Course Revision was created")
    elif provenance is not None and _fingerprints(blobs) != provenance:
        raise ValueError("Workspace Drift must be accepted before creating a Course Revision")
    oid = _commit_current(workspace, request.summary, "refs/heads/main", blobs=blobs)
    return CourseRevision(
        id=_revision_id(oid),
        summary=request.summary,
        created_at=_commit_created_at(workspace, oid),
    )


def _commit_created_at(workspace: Path, oid: str) -> str:
    result = _git_result(workspace, "show", "-s", "--format=%ct", oid)
    if result.returncode != 0:
        raise WorkspaceHistoryError(HISTORY_UNSAFE)
    try:
        timestamp = int(result.stdout.strip().decode("ascii", errors="strict"))
        return datetime.fromtimestamp(timestamp, UTC).isoformat()
    except (OSError, OverflowError, UnicodeError, ValueError) as error:
        raise WorkspaceHistoryError(HISTORY_UNSAFE) from error


def _tree_blobs(workspace: Path, oid: str) -> dict[str, _CapturedEntry]:
    result = _git_result_bounded(
        workspace,
        MAX_CANONICAL_ENTRIES * 512,
        "ls-tree",
        "-r",
        "-z",
        "--name-only",
        oid,
    )
    if result.returncode != 0:
        raise WorkspaceHistoryError(HISTORY_UNSAFE)
    output = result.stdout.decode("utf-8", errors="surrogateescape")
    blobs: dict[str, _CapturedEntry] = {}
    total_bytes = 0
    for path in output.split("\0"):
        if not _is_canonical_path(path):
            continue
        if len(blobs) >= MAX_CANONICAL_ENTRIES:
            raise WorkspaceHistoryError("Canonical Course history has too many files")
        listing = _git_result(workspace, "ls-tree", "-z", oid, "--", path)
        metadata, _, _ = listing.stdout.rstrip(b"\0").partition(b"\t")
        fields = metadata.split()
        if (
            listing.returncode != 0
            or len(fields) != 3
            or fields[0] not in {b"100644", b"100755", b"120000"}
            or fields[1] != b"blob"
        ):
            raise WorkspaceHistoryError(HISTORY_UNSAFE)
        try:
            blob_oid = fields[2].decode("ascii", errors="strict")
        except UnicodeError as error:
            raise WorkspaceHistoryError(HISTORY_UNSAFE) from error
        if not OID_PATTERN.fullmatch(blob_oid):
            raise WorkspaceHistoryError(HISTORY_UNSAFE)
        size_result = _git_result(workspace, "cat-file", "-s", blob_oid)
        try:
            size = int(size_result.stdout.decode("ascii", errors="strict").strip())
        except (UnicodeError, ValueError) as error:
            raise WorkspaceHistoryError(HISTORY_UNSAFE) from error
        if size_result.returncode != 0 or size < 0:
            raise WorkspaceHistoryError(HISTORY_UNSAFE)
        if size > MAX_CANONICAL_FILE_BYTES:
            raise WorkspaceHistoryError("A canonical Course history file is too large")
        total_bytes += size
        if total_bytes > MAX_CANONICAL_TOTAL_BYTES:
            raise WorkspaceHistoryError("Canonical Course history is too large")
        blob = _git_result(workspace, "cat-file", "blob", blob_oid)
        if blob.returncode != 0:
            raise WorkspaceHistoryError(HISTORY_UNSAFE)
        if len(blob.stdout) != size:
            raise WorkspaceHistoryError(HISTORY_UNSAFE)
        mode = cast(Literal["100644", "100755", "120000"], fields[0].decode("ascii"))
        blobs[path] = _CapturedEntry(mode=mode, content=blob.stdout)
    return blobs


def _write_blob(workspace: Path, path: str, content: bytes) -> None:
    _write_entry(workspace, path, _CapturedEntry(mode="100644", content=content))


def _write_entry(workspace: Path, path: str, entry: _CapturedEntry) -> None:
    target = workspace / path
    if target.parent == workspace / "presentations" and not _path_exists_or_symlink(target.parent):
        try:
            target.parent.mkdir(mode=0o700)
        except OSError as error:
            raise WorkspaceHistoryError(HISTORY_UNSAFE) from error
    if target.parent.is_symlink() or not _is_ordinary_directory(target.parent):
        raise WorkspaceHistoryError(HISTORY_UNSAFE)
    directory_flags = os.O_RDONLY | getattr(os, "O_DIRECTORY", 0) | getattr(os, "O_NOFOLLOW", 0)
    try:
        directory_fd = os.open(target.parent, directory_flags)
    except OSError as error:
        raise WorkspaceHistoryError(HISTORY_UNSAFE) from error
    temporary_name = f".{target.name}.course-harness-{os.urandom(12).hex()}.tmp"
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0)
    descriptor: int | None = None
    try:
        if entry.mode == "120000":
            os.symlink(os.fsdecode(entry.content), temporary_name, dir_fd=directory_fd)
        else:
            descriptor = os.open(temporary_name, flags, 0o600, dir_fd=directory_fd)
            with os.fdopen(descriptor, "wb", closefd=True) as stream:
                descriptor = None
                stream.write(entry.content)
                stream.flush()
                os.fsync(stream.fileno())
        os.replace(
            temporary_name,
            target.name,
            src_dir_fd=directory_fd,
            dst_dir_fd=directory_fd,
        )
        os.fsync(directory_fd)
    except OSError as error:
        with suppress(OSError):
            os.unlink(temporary_name, dir_fd=directory_fd)
        raise WorkspaceHistoryError(HISTORY_UNSAFE) from error
    finally:
        if descriptor is not None:
            os.close(descriptor)
        os.close(directory_fd)


def _remove_blob(workspace: Path, path: str) -> None:
    target = workspace / path
    directory_flags = os.O_RDONLY | getattr(os, "O_DIRECTORY", 0) | getattr(os, "O_NOFOLLOW", 0)
    try:
        directory_fd = os.open(target.parent, directory_flags)
        try:
            metadata = os.stat(target.name, dir_fd=directory_fd, follow_symlinks=False)
            if not (stat.S_ISREG(metadata.st_mode) or stat.S_ISLNK(metadata.st_mode)):
                raise WorkspaceHistoryError(HISTORY_UNSAFE)
            os.unlink(target.name, dir_fd=directory_fd)
            os.fsync(directory_fd)
        finally:
            os.close(directory_fd)
    except OSError as error:
        raise WorkspaceHistoryError(HISTORY_UNSAFE) from error


def _apply_tree(workspace: Path, blobs: dict[str, _CapturedEntry]) -> None:
    current = _current_canonical_paths(workspace)
    for path in current - set(blobs):
        _remove_blob(workspace, path)
    for path, entry in blobs.items():
        _write_entry(workspace, path, entry)


def _recovery_snapshot(workspace: Path) -> tuple[str, str]:
    ref = f"refs/course-harness/recovery/{os.urandom(12).hex()}"
    snapshot = _commit_current(
        workspace,
        "Course Harness recovery snapshot",
        ref,
        require_valid=False,
    )
    return ref, snapshot


def _delete_recovery_ref(workspace: Path, ref: str, snapshot: str) -> None:
    if not _RECOVERY_REF_PATTERN.fullmatch(ref) or not OID_PATTERN.fullmatch(snapshot):
        raise WorkspaceHistoryError(HISTORY_UNSAFE)
    result = _git_result(workspace, "update-ref", "-d", ref, snapshot)
    if result.returncode != 0:
        raise WorkspaceHistoryError(HISTORY_UNSAFE)


def _private_record_path(workspace: Path, name: str) -> Path:
    return workspace / ".git" / name


def _write_pending_provenance(
    workspace: Path,
    phase: Literal["prepared", "committed"],
    canonical: dict[str, dict[str, str]],
    commit: str,
    previous_head: str | None = None,
) -> None:
    if not OID_PATTERN.fullmatch(commit):
        raise WorkspaceHistoryError(HISTORY_UNSAFE)
    if previous_head is not None and not OID_PATTERN.fullmatch(previous_head):
        raise WorkspaceHistoryError(HISTORY_UNSAFE)
    if phase == "committed" and previous_head is not None:
        raise WorkspaceHistoryError(HISTORY_UNSAFE)
    payload: dict[str, object] = {
        "version": 1,
        "phase": phase,
        "canonical": canonical,
        "commit": commit,
    }
    if phase == "prepared":
        payload["previous_head"] = previous_head
    _write_blob(
        workspace / ".git",
        PROVENANCE_PENDING_FILE,
        json.dumps(payload, separators=(",", ":")).encode("ascii"),
    )


def _persist_committed_provenance(
    workspace: Path, revision: str, expected: dict[str, dict[str, str]]
) -> None:
    committed = _fingerprints(_tree_blobs(workspace, revision))
    if committed != expected:
        raise WorkspaceHistoryError(HISTORY_UNSAFE)
    _write_pending_provenance(workspace, "committed", expected, revision)
    _write_blob(
        workspace / ".git",
        PROVENANCE_FILE,
        json.dumps({"version": 1, "canonical": committed}, separators=(",", ":")).encode("ascii"),
    )
    _clear_private_record(workspace, PROVENANCE_PENDING_FILE)


def _read_pending_provenance(
    workspace: Path,
) -> (
    tuple[
        Literal["prepared", "committed"],
        dict[str, dict[str, str]],
        str,
        str | None,
    ]
    | None
):
    record = _read_private_record(workspace, PROVENANCE_PENDING_FILE, max_bytes=1_000_000)
    if record is None:
        return None
    try:
        payload = json.loads(record)
        phase = payload["phase"]
        canonical = payload["canonical"]
        commit = payload["commit"]
        previous_head = payload.get("previous_head")
        if (
            payload.get("version") != 1
            or phase not in {"prepared", "committed"}
            or not isinstance(canonical, dict)
            or any(
                not _is_canonical_path(path) or not _is_fingerprint(value)
                for path, value in canonical.items()
            )
            or not isinstance(commit, str)
            or not OID_PATTERN.fullmatch(commit)
            or (phase == "prepared" and "previous_head" not in payload)
            or (
                previous_head is not None
                and (not isinstance(previous_head, str) or not OID_PATTERN.fullmatch(previous_head))
            )
            or (phase == "committed" and "previous_head" in payload)
        ):
            raise ValueError
        return (
            cast(Literal["prepared", "committed"], phase),
            cast(dict[str, dict[str, str]], canonical),
            commit,
            cast(str | None, previous_head),
        )
    except KeyError, TypeError, ValueError, json.JSONDecodeError:
        raise WorkspaceHistoryError(HISTORY_UNSAFE) from None


def _recover_pending_provenance(workspace: Path) -> None:
    """Finish a post-commit provenance write, never trusting a later changed tree."""
    pending = _read_pending_provenance(workspace)
    if pending is None:
        return
    phase, expected, commit, previous_head = pending
    head = _head_oid(workspace)
    if phase == "prepared" and head == previous_head:
        _clear_private_record(workspace, PROVENANCE_PENDING_FILE)
        return
    if head != commit:
        raise WorkspaceHistoryError(HISTORY_UNSAFE)
    if _fingerprints(_tree_blobs(workspace, commit)) != expected:
        raise WorkspaceHistoryError(HISTORY_UNSAFE)
    if phase == "prepared":
        transaction = _read_private_record(workspace, TRANSACTION_FILE)
        if transaction is not None:
            fields = transaction.split(" ")
            if (
                len(fields) != 3
                or fields[0] not in {"active", "completed"}
                or not OID_PATTERN.fullmatch(fields[1])
                or not _RECOVERY_REF_PATTERN.fullmatch(fields[2])
            ):
                raise WorkspaceHistoryError(HISTORY_UNSAFE)
            _complete_transaction(workspace, fields[1])
    _write_blob(
        workspace / ".git",
        PROVENANCE_FILE,
        json.dumps({"version": 1, "canonical": expected}, separators=(",", ":")).encode("ascii"),
    )
    _clear_private_record(workspace, PROVENANCE_PENDING_FILE)


def _read_private_record(workspace: Path, name: str, *, max_bytes: int = 256) -> str | None:
    record_path = _private_record_path(workspace, name)
    if not _path_exists_or_symlink(record_path):
        return None
    directory_flags = os.O_RDONLY | getattr(os, "O_DIRECTORY", 0) | getattr(os, "O_NOFOLLOW", 0)
    directory_fd = -1
    descriptor = -1
    try:
        directory_fd = os.open(record_path.parent, directory_flags)
        descriptor = os.open(
            record_path.name,
            os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0),
            dir_fd=directory_fd,
        )
        metadata = os.fstat(descriptor)
        if not stat.S_ISREG(metadata.st_mode) or metadata.st_size > max_bytes:
            raise WorkspaceHistoryError(HISTORY_UNSAFE)
        with os.fdopen(descriptor, "rb", closefd=True) as stream:
            descriptor = -1
            return stream.read(max_bytes + 1).decode("ascii", errors="strict").strip()
    except (OSError, UnicodeError) as error:
        raise WorkspaceHistoryError(HISTORY_UNSAFE) from error
    finally:
        if descriptor >= 0:
            os.close(descriptor)
        if directory_fd >= 0:
            os.close(directory_fd)


def _clear_private_record(workspace: Path, name: str) -> None:
    record_path = _private_record_path(workspace, name)
    if not _path_exists_or_symlink(record_path):
        return
    if not _is_regular_file(record_path):
        raise WorkspaceHistoryError(HISTORY_UNSAFE)
    _remove_blob(workspace / ".git", name)


def _require_commit(workspace: Path, snapshot: str) -> None:
    resolved = _git_result(workspace, "rev-parse", "--verify", f"{snapshot}^{{commit}}")
    if resolved.returncode != 0:
        raise WorkspaceHistoryError(HISTORY_UNSAFE)
    try:
        oid = resolved.stdout.decode("ascii", errors="strict").strip()
    except UnicodeError as error:
        raise WorkspaceHistoryError(HISTORY_UNSAFE) from error
    if oid != snapshot:
        raise WorkspaceHistoryError(HISTORY_UNSAFE)


def _parse_run_boundary(
    record: str | None,
) -> tuple[Literal["active", "completed"], str, bool, bool, str] | None:
    if record is None:
        return None
    fields = record.split(" ")
    if (
        len(fields) != 5
        or fields[0] not in {"active", "completed"}
        or not OID_PATTERN.fullmatch(fields[1])
        or fields[2] not in {"0", "1"}
        or fields[3] not in {"0", "1"}
        or not _RECOVERY_REF_PATTERN.fullmatch(fields[4])
    ):
        raise WorkspaceHistoryError(HISTORY_UNSAFE)
    phase: Literal["active", "completed"] = "active" if fields[0] == "active" else "completed"
    return (
        phase,
        fields[1],
        fields[2] == "1",
        fields[3] == "1",
        fields[4],
    )


def _write_run_boundary(
    workspace: Path,
    phase: Literal["active", "completed"],
    snapshot: str,
    baseline_valid: bool,
    agent_mutating: bool,
    ref: str,
) -> None:
    if not OID_PATTERN.fullmatch(snapshot) or not _RECOVERY_REF_PATTERN.fullmatch(ref):
        raise WorkspaceHistoryError(HISTORY_UNSAFE)
    _write_blob(
        workspace / ".git",
        RUN_BOUNDARY_FILE,
        f"{phase} {snapshot} {int(baseline_valid)} {int(agent_mutating)} {ref}\n".encode("ascii"),
    )


def _recover_abandoned_run(workspace: Path) -> None:
    boundary = _parse_run_boundary(_read_private_record(workspace, RUN_BOUNDARY_FILE))
    if boundary is None:
        return
    phase, snapshot, baseline_valid, agent_mutating, ref = boundary
    if phase == "active" and _ACTIVE_RUNS.get(workspace.resolve()) == (
        snapshot,
        baseline_valid,
        agent_mutating,
        ref,
    ):
        return
    _require_commit(workspace, snapshot)
    # A crash cannot prove that invalid bytes came from the agent rather than an
    # external editor. Preserve them as Workspace Drift for reviewed recovery.
    _delete_recovery_ref(workspace, ref, snapshot)
    _clear_private_record(workspace, RUN_BOUNDARY_FILE)


def _write_transaction(
    workspace: Path, phase: Literal["active", "completed"], snapshot: str, ref: str
) -> None:
    if not OID_PATTERN.fullmatch(snapshot) or not _RECOVERY_REF_PATTERN.fullmatch(ref):
        raise WorkspaceHistoryError(HISTORY_UNSAFE)
    _write_blob(
        workspace / ".git",
        TRANSACTION_FILE,
        f"{phase} {snapshot} {ref}\n".encode("ascii"),
    )


def _clear_transaction(workspace: Path) -> None:
    _clear_private_record(workspace, TRANSACTION_FILE)


def _recover_pending_transaction(workspace: Path) -> None:
    record = _read_private_record(workspace, TRANSACTION_FILE)
    if record is None:
        return
    _recover_pending_provenance(workspace)
    record = _read_private_record(workspace, TRANSACTION_FILE)
    if record is None:
        return
    fields = record.split(" ")
    if (
        len(fields) != 3
        or fields[0] not in {"active", "completed"}
        or not _RECOVERY_REF_PATTERN.fullmatch(fields[2])
    ):
        raise WorkspaceHistoryError(HISTORY_UNSAFE)
    _phase, snapshot, ref = fields
    if not OID_PATTERN.fullmatch(snapshot):
        raise WorkspaceHistoryError(HISTORY_UNSAFE)
    _require_commit(workspace, snapshot)
    # An interrupted operation does not leave enough durable information to
    # distinguish its partial output from a Course Author's later edit.  Never
    # overwrite that current state during restart recovery; provenance will
    # expose it as Workspace Drift for reviewed acceptance or reconciliation.
    _delete_recovery_ref(workspace, ref, snapshot)
    _clear_transaction(workspace)


def _begin_transaction(workspace: Path) -> tuple[str, str]:
    recovery_ref, snapshot = _recovery_snapshot(workspace)
    _write_transaction(workspace, "active", snapshot, recovery_ref)
    return recovery_ref, snapshot


def _rollback_transaction(
    workspace: Path,
    snapshot: str,
    *,
    expected_current: dict[str, _CapturedEntry] | None = None,
) -> bool:
    """Compensate a transaction without ever replacing newer external bytes."""
    record = _read_private_record(workspace, TRANSACTION_FILE)
    fields = record.split(" ") if record is not None else []
    if len(fields) != 3 or fields[1] != snapshot or not _RECOVERY_REF_PATTERN.fullmatch(fields[2]):
        raise WorkspaceHistoryError(HISTORY_UNSAFE)
    restored = False
    target = _tree_blobs(workspace, snapshot)
    if expected_current is not None:
        changed_paths = {
            path
            for path in target.keys() | expected_current.keys()
            if target.get(path) != expected_current.get(path)
        }
        if changed_paths and all(
            entry.mode != "120000"
            for entry in [
                *[expected_current[path] for path in changed_paths if path in expected_current],
                *[target[path] for path in changed_paths if path in target],
            ]
        ):
            restored = restore_canonical_mutation(
                workspace,
                expected_current={
                    path: _canonical_file(expected_current.get(path)) for path in changed_paths
                },
                restore={path: _canonical_file(target.get(path)) for path in changed_paths},
            )
    _complete_transaction(workspace, snapshot)
    return restored


def _complete_transaction(workspace: Path, snapshot: str) -> None:
    record = _read_private_record(workspace, TRANSACTION_FILE)
    fields = record.split(" ") if record is not None else []
    if len(fields) != 3 or fields[1] != snapshot or not _RECOVERY_REF_PATTERN.fullmatch(fields[2]):
        raise WorkspaceHistoryError(HISTORY_UNSAFE)
    _write_transaction(workspace, "completed", snapshot, fields[2])
    _delete_recovery_ref(workspace, fields[2], snapshot)
    _clear_transaction(workspace)


@_serialized
def revert_current_path(workspace: Path, request: SelectiveRevertRequest) -> CurrentState:
    _require_private_workspace_repository(workspace)
    state = read_current_state(workspace)
    if request.path not in {entry.path for entry in state.changes}:
        raise ValueError("Choose a currently changed canonical path from Current State")
    _recovery_ref, snapshot = _begin_transaction(workspace)
    snapshot_blobs = _tree_blobs(workspace, snapshot)
    expected_after = dict(snapshot_blobs)
    try:
        head = _head_oid(workspace)
        if head is None or request.path not in _head_canonical_paths(workspace):
            target = None
            expected_after.pop(request.path, None)
        else:
            target = _tree_blobs(workspace, head)[request.path]
            expected_after[request.path] = target
        if target is not None and target.mode == "120000":
            raise WorkspaceHistoryError(HISTORY_UNSAFE)
        apply_canonical_mutation(
            workspace,
            expected={request.path: _canonical_file(snapshot_blobs.get(request.path))},
            updates={request.path: _canonical_file(target)},
        )
        result = _read_current_state(workspace)
        if not result.validation.valid:
            raise ValueError("Revert would leave Current State invalid")
        _complete_transaction(workspace, snapshot)
        return result
    except Exception:
        _rollback_transaction(workspace, snapshot, expected_current=expected_after)
        raise


@_serialized
def restore_revision(workspace: Path, revision_id: str) -> CurrentState:
    _require_private_workspace_repository(workspace)
    revisions = {revision.id: revision for revision in list_revisions(workspace)}
    if revision_id not in revisions:
        raise ValueError("Course Revision was not found")
    oid = revision_id.removeprefix("revision-")
    _recovery_ref, snapshot = _begin_transaction(workspace)
    snapshot_blobs = _tree_blobs(workspace, snapshot)
    revision_blobs = _tree_blobs(workspace, oid)
    changed_paths = {
        path
        for path in snapshot_blobs.keys() | revision_blobs.keys()
        if snapshot_blobs.get(path) != revision_blobs.get(path)
    }
    try:
        if any(
            entry.mode == "120000"
            for entry in [revision_blobs[path] for path in changed_paths if path in revision_blobs]
        ):
            raise WorkspaceHistoryError(HISTORY_UNSAFE)
        if changed_paths:
            apply_canonical_mutation(
                workspace,
                expected={
                    path: _canonical_file(snapshot_blobs.get(path)) for path in changed_paths
                },
                updates={path: _canonical_file(revision_blobs.get(path)) for path in changed_paths},
            )
        result = _read_current_state(workspace)
        if not result.validation.valid:
            raise ValueError("Revision would leave Current State invalid")
        _complete_transaction(workspace, snapshot)
        return result
    except Exception:
        _rollback_transaction(workspace, snapshot, expected_current=revision_blobs)
        raise
