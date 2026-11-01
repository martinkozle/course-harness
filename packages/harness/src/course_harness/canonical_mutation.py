"""Conditional writes for the human-authored canonical Course files.

``rename(2)`` is atomic, but it is deliberately an unconditional replacement.
That is a poor fit for a Course Workspace: a Course Author may edit a YAML file
outside the application between the application's read and its eventual write.
This module gives all authoring paths one small compare-and-swap-like seam.

There is no portable kernel operation that says "replace this regular file only
when these bytes still name it".  We therefore compare the complete regular
file value immediately before each replacement, and use the value we wrote as
the condition for compensation.  In particular, compensation never replaces a
file whose value has changed since our write.  This is the strongest portable
behaviour available to a cooperative local filesystem and makes the
validation-to-write boundary explicit and testable.
"""

import os
import stat
from collections.abc import Callable, Mapping
from contextlib import suppress
from dataclasses import dataclass
from pathlib import Path
from uuid import uuid4

MAX_CANONICAL_FILE_BYTES = 8_000_000
MAX_CANONICAL_TOTAL_BYTES = 64_000_000
MAX_CANONICAL_FILES = 500


class CanonicalMutationConflict(ValueError):
    """A canonical file changed after the caller captured its precondition."""


class CanonicalMutationUnsafe(CanonicalMutationConflict):
    """A canonical path cannot be safely read or replaced."""


@dataclass(frozen=True)
class CanonicalFile:
    """The exact portable value of one canonical path.

    ``None`` represents a missing path.  A symlink is represented by its link
    text and ``0o120000`` mode so it can be replaced without following it and,
    when necessary, restored exactly during conditional compensation.
    """

    content: bytes | None
    mode: int | None

    @classmethod
    def missing(cls) -> CanonicalFile:
        return cls(content=None, mode=None)


# Tests may replace this narrow seam to model an external editor changing a
# file in the otherwise-unobservable interval after validation.  It is never
# set in production.
after_precondition_check: Callable[[Path, Mapping[str, CanonicalFile]], None] | None = None


def capture_canonical_file(workspace: Path, relative_path: str) -> CanonicalFile:
    """Capture one regular canonical file without following a symlink."""
    try:
        directory_fd, name = _open_parent_directory(workspace, relative_path, create=False)
    except FileNotFoundError:
        return CanonicalFile.missing()
    except OSError as error:
        raise CanonicalMutationUnsafe("Could not inspect canonical Course state") from error
    try:
        try:
            metadata = os.stat(name, dir_fd=directory_fd, follow_symlinks=False)
        except FileNotFoundError:
            return CanonicalFile.missing()
        if stat.S_ISLNK(metadata.st_mode):
            return CanonicalFile(
                content=os.fsencode(os.readlink(name, dir_fd=directory_fd)), mode=0o120000
            )
        if not stat.S_ISREG(metadata.st_mode):
            raise CanonicalMutationUnsafe("Canonical Course state must use regular files")
        descriptor = os.open(
            name,
            os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0),
            dir_fd=directory_fd,
        )
        with os.fdopen(descriptor, "rb", closefd=True) as stream:
            opened_metadata = os.fstat(stream.fileno())
            if not stat.S_ISREG(opened_metadata.st_mode):
                raise CanonicalMutationUnsafe("Canonical Course state must use regular files")
            content = stream.read(MAX_CANONICAL_FILE_BYTES + 1)
    except OSError as error:
        raise CanonicalMutationUnsafe("Could not read canonical Course state") from error
    finally:
        os.close(directory_fd)
    if len(content) > MAX_CANONICAL_FILE_BYTES:
        raise CanonicalMutationUnsafe("A canonical Course file is too large to inspect")
    return CanonicalFile(content=content, mode=stat.S_IMODE(opened_metadata.st_mode))


def capture_canonical_files(workspace: Path, paths: set[str]) -> dict[str, CanonicalFile]:
    if len(paths) > MAX_CANONICAL_FILES:
        raise CanonicalMutationUnsafe("Too many canonical Course files to inspect")
    captured: dict[str, CanonicalFile] = {}
    total = 0
    for path in sorted(paths):
        entry = capture_canonical_file(workspace, path)
        total += len(entry.content or b"")
        if total > MAX_CANONICAL_TOTAL_BYTES:
            raise CanonicalMutationUnsafe("Canonical Course state is too large to inspect")
        captured[path] = entry
    return captured


def apply_canonical_mutation(
    workspace: Path,
    *,
    expected: Mapping[str, CanonicalFile],
    updates: Mapping[str, bytes | CanonicalFile | None],
) -> dict[str, CanonicalFile]:
    """Apply a conditional multi-file canonical mutation.

    Every updated path must have a complete expected value.  If any condition
    is stale, nothing is written.  If a later update fails, previously written
    paths are compensated only while they still equal this mutation's output;
    an external change is consequently never overwritten by rollback.
    """
    if not updates or set(updates) - set(expected):
        raise ValueError("Canonical mutations require an expected value for every updated path")
    if set(expected) - set(updates):
        raise ValueError("Canonical mutation preconditions must match updated paths")
    paths = sorted(updates)
    _assert_expected(workspace, expected)
    hook = after_precondition_check
    if hook is not None:
        hook(workspace, expected)

    written: dict[str, CanonicalFile] = {}
    outputs = {path: _canonical_output(value) for path, value in updates.items()}
    if (
        len(outputs) > MAX_CANONICAL_FILES
        or sum(len(output.content or b"") for output in outputs.values())
        > MAX_CANONICAL_TOTAL_BYTES
    ):
        raise CanonicalMutationUnsafe("Canonical Course mutation is too large to save")
    try:
        for path in paths:
            # Re-check each target immediately before replacement.  This closes
            # the interval between a whole-mutation validation and the write of
            # a later path in a multi-file update.
            if capture_canonical_file(workspace, path) != expected[path]:
                raise CanonicalMutationConflict(
                    "Canonical Course state changed before the app mutation could be saved"
                )
            output = outputs[path]
            if output.content is None:
                _remove_regular_file(workspace, path, required=expected[path].content is not None)
            else:
                _replace_canonical_file(workspace, path, output)
            written[path] = output
        return outputs
    except Exception:
        _compensate(workspace, expected, written)
        raise


def restore_canonical_mutation(
    workspace: Path,
    *,
    expected_current: Mapping[str, CanonicalFile],
    restore: Mapping[str, CanonicalFile],
) -> bool:
    """Restore a failed mutation only if every target remains our own output.

    ``False`` says a later external write was observed, so no rollback was
    attempted.  This conservative all-or-nothing guard avoids a partial
    rollback that might hide a Course Author's edit.
    """
    if set(expected_current) != set(restore):
        raise ValueError("Canonical rollback paths must match their expected current values")
    try:
        _assert_expected(workspace, expected_current)
    except CanonicalMutationConflict:
        return False
    updates = dict(restore)
    # Values were just checked, so apply through the same seam.  A new external
    # write in this final interval is caught by its per-path checks and the
    # compensation guard above never replaces that external value.
    apply_canonical_mutation(workspace, expected=expected_current, updates=updates)
    return True


def _assert_expected(workspace: Path, expected: Mapping[str, CanonicalFile]) -> None:
    for path in sorted(expected):
        if capture_canonical_file(workspace, path) != expected[path]:
            raise CanonicalMutationConflict(
                "Canonical Course state changed before the app mutation could be saved"
            )


def _canonical_output(value: bytes | CanonicalFile | None) -> CanonicalFile:
    if value is None:
        return CanonicalFile.missing()
    if isinstance(value, bytes):
        if len(value) > MAX_CANONICAL_FILE_BYTES:
            raise CanonicalMutationUnsafe("A canonical Course file is too large to save")
        return CanonicalFile(content=value, mode=0o644)
    if value.content is None:
        if value.mode is not None:
            raise ValueError("A missing canonical file cannot declare a mode")
        return value
    if len(value.content) > MAX_CANONICAL_FILE_BYTES:
        raise CanonicalMutationUnsafe("A canonical Course file is too large to save")
    if value.mode != 0o120000 and (value.mode is None or value.mode < 0 or value.mode > 0o7777):
        raise CanonicalMutationUnsafe("Canonical Course state must use regular files")
    return value


def _canonical_parts(relative_path: str) -> tuple[tuple[str, ...], str]:
    candidate = Path(relative_path)
    if (
        not relative_path
        or candidate.is_absolute()
        or ".." in candidate.parts
        or not candidate.parts
    ):
        raise CanonicalMutationUnsafe("Canonical path is not workspace-relative")
    return tuple(candidate.parts[:-1]), candidate.parts[-1]


def _open_parent_directory(workspace: Path, relative_path: str, *, create: bool) -> tuple[int, str]:
    parents, name = _canonical_parts(relative_path)
    flags = os.O_RDONLY | getattr(os, "O_DIRECTORY", 0) | getattr(os, "O_NOFOLLOW", 0)
    descriptor = os.open(workspace, flags)
    try:
        for part in parents:
            if create:
                with suppress(FileExistsError):
                    os.mkdir(part, mode=0o755, dir_fd=descriptor)
            child = os.open(part, flags, dir_fd=descriptor)
            os.close(descriptor)
            descriptor = child
        return descriptor, name
    except BaseException:
        os.close(descriptor)
        raise


def _replace_canonical_file(workspace: Path, relative_path: str, output: CanonicalFile) -> None:
    assert output.content is not None and output.mode is not None
    directory_fd = -1
    temporary = ""
    descriptor: int | None = None
    try:
        directory_fd, name = _open_parent_directory(workspace, relative_path, create=True)
        temporary = f".{name}.course-harness-{uuid4().hex}.tmp"
        if output.mode == 0o120000:
            os.symlink(os.fsdecode(output.content), temporary, dir_fd=directory_fd)
        else:
            descriptor = os.open(
                temporary,
                os.O_WRONLY | os.O_CREAT | os.O_EXCL,
                output.mode,
                dir_fd=directory_fd,
            )
            with os.fdopen(descriptor, "wb", closefd=True) as stream:
                descriptor = None
                stream.write(output.content)
                stream.flush()
                os.fsync(stream.fileno())
            os.chmod(temporary, output.mode, dir_fd=directory_fd, follow_symlinks=False)
        os.replace(
            temporary,
            name,
            src_dir_fd=directory_fd,
            dst_dir_fd=directory_fd,
        )
        os.fsync(directory_fd)
    except OSError as error:
        if directory_fd >= 0 and temporary:
            with suppress(OSError):
                os.unlink(temporary, dir_fd=directory_fd)
        raise CanonicalMutationUnsafe("Could not save canonical Course state") from error
    finally:
        if descriptor is not None:
            os.close(descriptor)
        if directory_fd >= 0:
            os.close(directory_fd)


def _remove_regular_file(workspace: Path, relative_path: str, *, required: bool) -> None:
    directory_fd = -1
    try:
        directory_fd, name = _open_parent_directory(workspace, relative_path, create=False)
        try:
            metadata = os.stat(name, dir_fd=directory_fd, follow_symlinks=False)
        except FileNotFoundError:
            if required:
                raise CanonicalMutationConflict(
                    "Canonical Course state changed before the app mutation could be saved"
                ) from None
            return
        if not (stat.S_ISLNK(metadata.st_mode) or stat.S_ISREG(metadata.st_mode)):
            raise CanonicalMutationUnsafe("Canonical Course state must use regular files")
        os.unlink(name, dir_fd=directory_fd)
        os.fsync(directory_fd)
    except FileNotFoundError:
        if required:
            raise CanonicalMutationConflict(
                "Canonical Course state changed before the app mutation could be saved"
            ) from None
        return
    except OSError as error:
        raise CanonicalMutationUnsafe("Could not remove canonical Course state") from error
    finally:
        if directory_fd >= 0:
            os.close(directory_fd)


def _compensate(
    workspace: Path,
    expected: Mapping[str, CanonicalFile],
    written: Mapping[str, CanonicalFile],
) -> None:
    for path in reversed(sorted(written)):
        # Do not use apply_canonical_mutation recursively: a failed rollback
        # should never conceal the original error or overwrite newer bytes.
        if capture_canonical_file(workspace, path) != written[path]:
            continue
        previous = expected[path]
        try:
            if previous.content is None:
                _remove_regular_file(workspace, path, required=False)
            else:
                _replace_canonical_file(workspace, path, previous)
        except CanonicalMutationConflict, CanonicalMutationUnsafe:
            continue
