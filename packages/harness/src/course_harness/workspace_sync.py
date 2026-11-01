"""Automatic Course Revisions and what Publish and Sync show about a Course Workspace.

Course Revisions are a safety net: before Publish or Sync, uncommitted edits become a Revision
on their own, with a deterministic summary of what changed. The status here is read locally;
only ``workspace_remote.check_remote`` talks to the remote, and its last result is compared
with the local refs.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, Field

from course_harness.core.course_plan import CoursePlan, InvalidCoursePlan, read_course_plan
from course_harness.core.release_files import RELEASE_FILES_REF_PREFIX
from course_harness.core.releases import ReleaseError, run_git, scan_releases
from course_harness.workspace_history import (
    MAX_REVISION_SUMMARY_CHARACTERS,
    CourseRevision,
    CurrentState,
    RevisionCreateRequest,
    create_revision,
    read_current_state,
)
from course_harness.workspace_remote import (
    BRANCH,
    REMOTE_MAIN,
    read_remote,
    read_remote_check,
    remote_label,
)

MAX_LISTED_REVISIONS = 50
MAX_TITLE_CHARACTERS = 60
_PRESENTATION_PATH = re.compile(r"presentations/(presentation-[0-9a-f]{12})\.yaml\Z")
_QUIZ_PATH = re.compile(r"quizzes/(lecture-[0-9a-f]{12})\.yaml\Z")
TAG_PREFIX = "refs/tags/course-release/"
_VERBS = {"added": "Added", "modified": "Edited", "deleted": "Removed"}


class AutomaticRevisionBlocked(ValueError):
    """Current State cannot become a Course Revision without the Course Author."""

    def __init__(self, message: str, findings: list[str]) -> None:
        super().__init__(message)
        self.findings = findings


class AutomaticRevisionResult(BaseModel):
    """The Revision created from uncommitted edits, or ``None`` when there were none."""

    revision: CourseRevision | None


class SharingStatus(BaseModel):
    """What the Publish and Sync controls show, read without contacting the remote."""

    release_count: int
    latest_release_name: str | None
    # Course Revisions since the newest Release, plus one for uncommitted edits.
    changes_since_release: int
    uncommitted: bool
    # What the automatic Revision would be called, while there are uncommitted edits.
    pending_summary: str | None
    # Summaries of the Revisions since the newest Release, oldest first.
    revision_summaries: list[str] = Field(default_factory=list)
    # Why uncommitted edits cannot become a Revision right now.
    blocked: Literal["invalid", "drift"] | None = None
    findings: list[str] = Field(default_factory=list)
    remote_url: str | None = None
    remote_label: str | None = None
    # Local changes the remote lacks, and remote Revisions this computer lacks; ``None`` until
    # the remote has been checked.
    ahead: int | None = None
    behind: int | None = None
    checked_at: str | None = None
    check_error: str | None = None
    # Both sides have Revisions the other lacks: Sync offers to use one of them.
    diverged: bool = False
    # Release IDs already on the remote from elsewhere, which a new Release cannot reuse.
    remote_release_slugs: list[str] = Field(default_factory=list)
    # Releases whose files could not be pushed because they do not match the Release.
    skipped_files: list[str] = Field(default_factory=list)


class SyncResult(BaseModel):
    """What one Sync did, in plain words, with Git's own message only in ``detail``."""

    ok: bool
    message: str
    detail: str | None = None
    findings: list[str] = Field(default_factory=list)
    revision: CourseRevision | None = None
    # Course Revisions received from the remote.
    received: int = 0
    # This computer and the remote diverged; ``use-shared-copy`` or
    # ``replace-shared-copy`` resolves it.
    diverged: bool = False
    # Things that worked but need the Course Author's attention.
    notices: list[str] = Field(default_factory=list)
    status: SharingStatus | None = None


def _quote(title: str) -> str:
    title = " ".join(title.split())
    if len(title) > MAX_TITLE_CHARACTERS:
        title = title[: MAX_TITLE_CHARACTERS - 1].rstrip() + "…"
    return f"'{title}'"


def _join(parts: list[str]) -> str:
    if len(parts) == 1:
        return parts[0]
    return f"{', '.join(parts[:-1])} and {parts[-1]}"


def automatic_revision_summary(state: CurrentState, plan: CoursePlan | None) -> str:
    """Describe Current State's changes: "Edited Lecture 2 'Agents' and the Quiz for Lecture 3"."""
    planned = plan.lectures if plan else []
    lectures = {lecture.id: (number, lecture) for number, lecture in enumerate(planned, 1)}
    presentations = {
        lecture.presentation_id: lecture.id for lecture in planned if lecture.presentation_id
    }
    groups: dict[str, list[tuple[tuple[int, int], str]]] = {}
    for change in state.changes:
        if change.path == "course.yaml":
            item = ((1_000_000, 0), "the Course Plan")
        elif change.path == "sources.yaml":
            item = ((1_000_001, 0), "the Sources")
        elif match := _PRESENTATION_PATH.fullmatch(change.path):
            lecture_id = presentations.get(match.group(1))
            if lecture_id in lectures:
                number, lecture = lectures[lecture_id]
                item = ((number, 0), f"Lecture {number} {_quote(lecture.title)}")
            else:
                item = ((999_999, 0), "Slides")
        elif match := _QUIZ_PATH.fullmatch(change.path):
            if match.group(1) in lectures:
                number = lectures[match.group(1)][0]
                item = ((number, 1), f"the Quiz for Lecture {number}")
            else:
                item = ((999_999, 1), "a Quiz")
        else:
            item = ((1_000_002, 0), "Course files")
        groups.setdefault(change.status, []).append(item)
    sentences: list[str] = []
    for status in ("modified", "added", "deleted"):
        items = groups.get(status)
        if not items:
            continue
        names = list(dict.fromkeys(name for _key, name in sorted(items)))
        verb = _VERBS[status] if not sentences else _VERBS[status].lower()
        sentences.append(f"{verb} {_join(names)}")
    summary = "; ".join(sentences) or "Saved changes"
    if len(summary) <= MAX_REVISION_SUMMARY_CHARACTERS:
        return summary
    count = len(state.changes)
    return f"Edited {count} parts of the Course"


def _read_plan(workspace: Path) -> CoursePlan | None:
    try:
        return read_course_plan(workspace)
    except InvalidCoursePlan, OSError:
        return None


def _blocker(state: CurrentState) -> AutomaticRevisionBlocked | None:
    if state.clean:
        return None
    if state.drift != "clean":
        return AutomaticRevisionBlocked(
            "Files were changed outside Course Harness; review them in History first.", []
        )
    if not state.validation.valid:
        return AutomaticRevisionBlocked(
            "Your changes have problems to fix first.", list(state.validation.findings)
        )
    return None


def create_automatic_revision(workspace: Path) -> CourseRevision | None:
    """Save uncommitted edits as a Course Revision with a summary built from what changed."""
    state = read_current_state(workspace)
    if state.clean:
        return None
    blocker = _blocker(state)
    if blocker is not None:
        raise blocker
    summary = automatic_revision_summary(state, _read_plan(workspace))
    return create_revision(workspace, RevisionCreateRequest(summary=summary))


def _count(workspace: Path, *arguments: str) -> int:
    return int(run_git(workspace, "rev-list", "--count", *arguments).decode("ascii").strip())


def _local_refs(workspace: Path) -> dict[str, str]:
    output = run_git(
        workspace,
        "for-each-ref",
        "--format=%(objectname) %(refname)",
        "refs/tags/course-release",
        RELEASE_FILES_REF_PREFIX.removesuffix("/"),
    )
    refs: dict[str, str] = {}
    for line in output.decode("utf-8", errors="replace").splitlines():
        oid, _, name = line.partition(" ")
        refs[name] = oid
    return refs


def _head(workspace: Path, ref: str) -> str | None:
    try:
        return run_git(workspace, "rev-parse", "--verify", "--quiet", ref).decode().strip()
    except ReleaseError:
        return None


def main_contains(workspace: Path, commit: str) -> bool:
    """Whether this computer's ``main`` already includes ``commit``."""
    head = _head(workspace, f"refs/heads/{BRANCH}")
    if head is None:
        return False
    try:
        run_git(workspace, "merge-base", "--is-ancestor", commit, head)
    except ReleaseError:
        return False
    return True


def _release_slug(name: str) -> str:
    return name.rsplit("/", 1)[1]


def sharing_status(workspace: Path) -> SharingStatus:
    state = read_current_state(workspace)
    scan = scan_releases(workspace)
    latest = scan.releases[0] if scan.releases else None
    head = _head(workspace, f"refs/heads/{BRANCH}")
    url = read_remote(workspace).url
    check = read_remote_check(workspace) if url is not None else None
    remote_tags = {
        _release_slug(name)
        for name in (check.refs if check is not None else {})
        if name.startswith(TAG_PREFIX)
    }
    local_slugs = {release.slug for release in scan.releases}
    summaries: list[str] = []
    committed = 0
    if head is not None:
        # Revisions that some Release (published here or pushed from elsewhere) covers are
        # not changes since a Release.
        exclude = sorted({f"^{release.commit_oid}" for release in scan.releases})
        committed = _count(workspace, "--first-parent", head, *exclude)
        log = run_git(
            workspace,
            "log",
            "--first-parent",
            f"--max-count={MAX_LISTED_REVISIONS}",
            "--format=%s",
            head,
            *exclude,
        )
        summaries = list(reversed(log.decode("utf-8", errors="replace").splitlines()))
    blocker = _blocker(state)
    status = SharingStatus(
        release_count=len(local_slugs | remote_tags),
        latest_release_name=latest.name if latest else None,
        changes_since_release=committed + (0 if state.clean else 1),
        uncommitted=not state.clean,
        pending_summary=(
            automatic_revision_summary(state, _read_plan(workspace))
            if not state.clean and blocker is None
            else None
        ),
        revision_summaries=summaries,
        blocked=(None if blocker is None else "drift" if state.drift != "clean" else "invalid"),
        findings=blocker.findings if blocker is not None else [],
    )
    if url is None:
        return status
    status.remote_url = url
    status.remote_label = remote_label(url)
    if check is None:
        return status
    status.checked_at = check.checked_at
    status.check_error = check.error
    status.remote_release_slugs = sorted(remote_tags)
    status.skipped_files = list(check.skipped_files)
    remote_head = check.refs.get(f"refs/heads/{BRANCH}")
    if remote_head is None:
        ahead = _count(workspace, head) if head is not None else 0
        behind = 0
    elif _head(workspace, REMOTE_MAIN) != remote_head:
        # The remote-tracking ref changed since the check; wait for the next one.
        return status
    elif head is None:
        ahead, behind = 0, _count(workspace, remote_head)
    else:
        ahead = _count(workspace, head, f"^{remote_head}")
        behind = _count(workspace, remote_head, f"^{head}")
    status.diverged = ahead > 0 and behind > 0
    # One change per Release the remote lacks: its tag, or else its files.
    local = _local_refs(workspace)
    for name, oid in local.items():
        if not name.startswith(TAG_PREFIX):
            continue
        slug = _release_slug(name)
        files = f"{RELEASE_FILES_REF_PREFIX}{slug}"
        if check.refs.get(name) != oid or (
            files in local
            and check.refs.get(files) != local[files]
            and slug not in check.skipped_files
        ):
            ahead += 1
    status.ahead = ahead + (0 if state.clean else 1)
    status.behind = behind
    return status
