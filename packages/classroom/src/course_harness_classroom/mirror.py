"""Keep a course's bare mirror of its repository up to date, safely.

The mirror is ``git init --bare`` without templates (so without sample hooks), and a fetch asks
only for the three refspecs a Course Release needs (ADR 0019), never ``--mirror``. Git runs
without user or system configuration, hooks, credential helpers, prompts or redirects; only
HTTPS may be used (plus ``file`` with the development setting); every received object is
checked; and the fetch is killed when it takes too long or the mirror grows past its size limit.
Nothing is ever checked out.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import tempfile
import time
from contextlib import suppress
from pathlib import Path

from course_harness.core.releases import git_environment
from course_harness_classroom.repo_urls import resolve_public_address

REFSPECS = (
    "+refs/heads/main:refs/heads/main",
    "+refs/tags/course-release/*:refs/tags/course-release/*",
    "+refs/course-harness/release-files/*:refs/course-harness/release-files/*",
)
SIZE_CHECK_SECONDS = 0.5
PROXY_VARIABLES = (
    "http_proxy",
    "https_proxy",
    "all_proxy",
    "no_proxy",
    "HTTP_PROXY",
    "HTTPS_PROXY",
    "ALL_PROXY",
    "NO_PROXY",
)
MAX_REPORTED_OUTPUT = 2_000


class MirrorError(RuntimeError):
    """The mirror could not be created or updated."""


def _hardened_config(*, allow_local: bool) -> list[str]:
    settings = [
        "protocol.allow=never",
        "protocol.https.allow=always",
        "transfer.fsckObjects=true",
        "fetch.fsckObjects=true",
        "core.hooksPath=/dev/null",
        "core.fsmonitor=false",
        "credential.helper=",
        "core.askPass=",
        "http.followRedirects=false",
        "http.lowSpeedLimit=1000",
        "http.lowSpeedTime=60",
        "http.proxy=",
        "gc.auto=0",
        "maintenance.auto=false",
        "submodule.recurse=false",
    ]
    if allow_local:
        settings += ["protocol.file.allow=always", "protocol.http.allow=always"]
    return [argument for setting in settings for argument in ("-c", setting)]


def _environment() -> dict[str, str]:
    environment = {
        key: value for key, value in git_environment().items() if key not in PROXY_VARIABLES
    }
    environment.update({"GIT_ASKPASS": "", "SSH_ASKPASS": "", "GIT_PROTOCOL_FROM_USER": "0"})
    return environment


def directory_size(path: Path) -> int:
    total = 0
    for root, _directories, files in os.walk(path):
        for name in files:
            with suppress(OSError):
                total += (Path(root) / name).lstat().st_size
    return total


def ensure_mirror(mirror: Path) -> None:
    """Create an empty bare repository without template hooks, unless it exists."""
    if (mirror / "HEAD").is_file():
        return
    mirror.parent.mkdir(parents=True, exist_ok=True)
    result = subprocess.run(
        ["git", "init", "--bare", "--quiet", "--template=", str(mirror)],
        env=_environment(),
        stdin=subprocess.DEVNULL,
        capture_output=True,
        timeout=30,
        check=False,
    )
    if result.returncode != 0:
        raise MirrorError("The mirror could not be created")


def fetch(
    mirror: Path,
    url: str,
    *,
    timeout_seconds: float,
    max_bytes: int,
    allow_local: bool = False,
) -> None:
    """Fetch the Release refspecs into the mirror, within time and disk-size limits.

    The host is resolved once and checked, and Git connects to exactly that address. The caller
    holds the course's sync lock, so any ``*.lock`` file left in the mirror is from a fetch that
    was killed and is removed first. A completed fetch that leaves the mirror over the size limit
    discards the mirror, so the next sync starts clean.
    """
    pinned: list[str] = []
    if not allow_local:
        host, port, address = resolve_public_address(url)
        literal = f"[{address}]" if ":" in address else address
        pinned = ["-c", f"http.curloptResolve={host}:{port}:{literal}"]
    ensure_mirror(mirror)
    remove_stale_locks(mirror)
    command = [
        "git",
        f"--git-dir={mirror}",
        *_hardened_config(allow_local=allow_local),
        *pinned,
        "fetch",
        "--quiet",
        "--no-tags",
        "--prune",
        "--no-write-fetch-head",
        "--no-recurse-submodules",
        "--no-auto-gc",
        "--",
        url,
        *REFSPECS,
    ]
    deadline = time.monotonic() + timeout_seconds
    with tempfile.TemporaryFile() as errors:
        process = subprocess.Popen(
            command,
            cwd=mirror,
            env=_environment(),
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=errors,
            start_new_session=True,
        )
        try:
            _wait(process, mirror, deadline=deadline, max_bytes=max_bytes)
        except MirrorError:
            _remove_partial_packs(mirror)
            raise
        if process.returncode != 0:
            errors.seek(0)
            detail = errors.read(MAX_REPORTED_OUTPUT).decode("utf-8", errors="replace").strip()
            raise MirrorError(f"Fetching the repository failed: {detail or 'git fetch failed'}")
    if directory_size(mirror) > max_bytes:
        remove_mirror(mirror)
        ensure_mirror(mirror)
        raise MirrorError("The repository is larger than this Classroom allows")


def remove_stale_locks(mirror: Path) -> None:
    for lock in mirror.rglob("*.lock"):
        with suppress(OSError):
            lock.unlink()


def _wait(
    process: subprocess.Popen[bytes], mirror: Path, *, deadline: float, max_bytes: int
) -> None:
    try:
        while True:
            try:
                process.wait(timeout=SIZE_CHECK_SECONDS)
                break
            except subprocess.TimeoutExpired:
                pass
            if time.monotonic() > deadline:
                raise MirrorError("Fetching the repository took too long")
            if directory_size(mirror) > max_bytes:
                raise MirrorError("The repository is larger than this Classroom allows")
    finally:
        if process.poll() is None:
            with suppress(ProcessLookupError):
                os.killpg(process.pid, 9)
            with suppress(subprocess.TimeoutExpired):
                process.wait(timeout=5)


def _remove_partial_packs(mirror: Path) -> None:
    """Drop what an interrupted fetch left behind, so it does not count against the limit."""
    objects = mirror / "objects"
    for leftover in [
        *objects.glob("pack/tmp_*"),
        *objects.glob("tmp_*"),
    ]:
        if leftover.is_dir():
            shutil.rmtree(leftover, ignore_errors=True)
        else:
            leftover.unlink(missing_ok=True)


def remove_mirror(mirror: Path) -> None:
    shutil.rmtree(mirror, ignore_errors=True)
