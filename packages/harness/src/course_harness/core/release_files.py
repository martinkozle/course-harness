"""Read and verify a Release's template and Artifact bytes from Git objects.

Release binaries never enter the Course Workspace's working tree. Course Harness stores them in a
commit whose tree holds exactly the Release's ``inputs/template.pptx`` (when its Template Profile
pins one) and ``artifacts/<id>.pptx`` files, and points ``refs/course-harness/release-files/<slug>``
at it. Pushing that ref beside the Release tag lets a remote, and a bare mirror of it, serve them.

The manifest stays authoritative (ADR 0009, ADR 0019): every blob is checked against the
manifest's size before it is read and against its SHA-256 after. A missing, extra or changed file
is a ``ReleaseError``. Reading uses the same hardened Git runner as Release manifests, so it is
safe on an untrusted mirror.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

from course_harness.core.releases import (
    ACTIVE_READ_BUDGET,
    MAX_ARTIFACT_BYTES,
    OID_PATTERN,
    SLUG_PATTERN,
    CourseRelease,
    ReleaseError,
    check_read_deadline,
    read_budget,
    run_git,
    sha256_hex,
)

RELEASE_FILES_REF_PREFIX = "refs/course-harness/release-files/"
TEMPLATE_PATH = "inputs/template.pptx"
# A Release holds at most one template and its Artifacts, each up to MAX_ARTIFACT_BYTES.
MAX_RELEASE_FILES_BYTES = 256_000_000
RELEASE_FILES_READ_SECONDS = 120
_TREE_ENTRY = re.compile(rb"(100644|100755|120000|040000|160000) (blob|tree|commit) ([0-9a-f]+)\t")


@dataclass(frozen=True)
class ReleaseFiles:
    """A Release's verified binary files: its pinned template and Artifacts by Artifact ID."""

    template: bytes | None
    artifacts: dict[str, bytes]


def release_files_ref(slug: str) -> str:
    """The ref that names a Release's files commit."""
    if not SLUG_PATTERN.fullmatch(slug):
        raise ReleaseError("Release slug is invalid")
    return f"{RELEASE_FILES_REF_PREFIX}{slug}"


def expected_release_files(release: CourseRelease) -> dict[str, int | None]:
    """The exact paths a Release's files commit holds, with each Artifact's manifest size."""
    expected: dict[str, int | None] = {}
    if release.template_profile.template_storage_path is not None:
        expected[TEMPLATE_PATH] = None
    for artifact in release.artifacts:
        expected[artifact.storage_path] = artifact.size
    return expected


def read_release_files(repository: Path, release: CourseRelease) -> ReleaseFiles:
    """Read every binary file of a Release from its files ref and verify it against the manifest.

    ``repository`` is a Course Workspace or a bare repository such as a mirror; ``release`` is a
    manifest already read and verified with ``releases.read_release`` from the same repository.
    """
    with read_budget(seconds=RELEASE_FILES_READ_SECONDS, max_bytes=MAX_RELEASE_FILES_BYTES):
        blobs = _release_file_blobs(repository, release)
        template = None
        template_pin = release.template_profile
        if template_pin.template_storage_path is not None:
            template = _read_verified_blob(
                repository,
                blobs[TEMPLATE_PATH],
                size=None,
                sha256=template_pin.template_sha256,
                what="Release template",
            )
        artifacts = {
            artifact.id: _read_verified_blob(
                repository,
                blobs[artifact.storage_path],
                size=artifact.size,
                sha256=artifact.sha256,
                what="Release Artifact",
            )
            for artifact in release.artifacts
        }
    return ReleaseFiles(template=template, artifacts=artifacts)


def read_release_artifact_file(repository: Path, release: CourseRelease, artifact_id: str) -> bytes:
    """Read and verify one Artifact of a Release from its files ref."""
    artifact = next((item for item in release.artifacts if item.id == artifact_id), None)
    if artifact is None:
        raise ReleaseError("Release Artifact was not found")
    with read_budget(seconds=RELEASE_FILES_READ_SECONDS, max_bytes=MAX_RELEASE_FILES_BYTES):
        blobs = _release_file_blobs(repository, release)
        return _read_verified_blob(
            repository,
            blobs[artifact.storage_path],
            size=artifact.size,
            sha256=artifact.sha256,
            what="Release Artifact",
        )


def release_files_commit(repository: Path, slug: str) -> str | None:
    """The commit a Release's files ref names, or ``None`` when the ref does not exist."""
    ref = release_files_ref(slug)
    output = run_git(
        repository, "for-each-ref", "--format=%(refname) %(objectname) %(objecttype)", ref
    )
    matches = [line for line in output.split(b"\n") if line.startswith(f"{ref} ".encode())]
    if not matches:
        return None
    try:
        _name, oid, kind = matches[0].decode("ascii").split(" ")
    except (UnicodeError, ValueError) as error:
        raise ReleaseError("Release files ref is invalid") from error
    if kind != "commit" or not OID_PATTERN.fullmatch(oid):
        raise ReleaseError("Release files ref is invalid")
    return oid


def _release_file_blobs(repository: Path, release: CourseRelease) -> dict[str, str]:
    """Map each expected path to its blob, refusing a tree that holds anything else."""
    commit = release_files_commit(repository, release.slug)
    if commit is None:
        raise ReleaseError("Release files are missing")
    listing = run_git(repository, "ls-tree", "-r", "-z", "--full-tree", commit)
    expected = expected_release_files(release)
    blobs: dict[str, str] = {}
    for entry in listing.split(b"\0"):
        if not entry:
            continue
        match = _TREE_ENTRY.match(entry)
        if match is None:
            raise ReleaseError("Release files tree is invalid")
        mode, kind, oid = match.groups()
        try:
            path = entry[match.end() :].decode("utf-8")
        except UnicodeError as error:
            raise ReleaseError("Release files tree is invalid") from error
        if path not in expected or path in blobs:
            raise ReleaseError("Release files tree holds a file its manifest does not name")
        if mode != b"100644" or kind != b"blob" or not OID_PATTERN.fullmatch(oid.decode()):
            raise ReleaseError("Release files tree is invalid")
        blobs[path] = oid.decode()
    if set(blobs) != set(expected):
        raise ReleaseError("Release files are missing a file its manifest names")
    return blobs


def _read_verified_blob(
    repository: Path, oid: str, *, size: int | None, sha256: str | None, what: str
) -> bytes:
    check_read_deadline()
    output = run_git(repository, "cat-file", "--batch-check", input_data=f"{oid}\n".encode())
    fields = output.split()
    try:
        kind, actual_size = fields[1], int(fields[2])
    except (IndexError, ValueError) as error:
        raise ReleaseError(f"{what} is missing") from error
    if len(fields) != 3 or kind != b"blob":
        raise ReleaseError(f"{what} is missing")
    limit = MAX_ARTIFACT_BYTES if size is None else size
    if actual_size > limit or (size is not None and actual_size != size):
        raise ReleaseError(f"{what} does not match its manifest")
    budget = ACTIVE_READ_BUDGET.get()
    if budget is not None and actual_size > budget.allowance():
        raise ReleaseError("Release exceeds its read budget")
    content = run_git(repository, "cat-file", "blob", oid, max_output_bytes=actual_size)
    if len(content) != actual_size or sha256 is None or sha256_hex(content) != sha256:
        raise ReleaseError(f"{what} does not match its manifest")
    return content
