"""Share a Course and its Releases through the Course Workspace's Git remote.

The remote is an ordinary Git remote named ``origin`` in the Workspace's ``.git/config``: it is
neither canonical Course state nor a secret, and a URL with an embedded password is refused.
Pushing is always the Course Author's explicit action. It runs the system ``git push`` with the
Author's own Git configuration and credentials (SSH agent, credential helpers), exactly as the
terminal command it mirrors would, but with hooks disabled and no terminal prompt.
"""

from __future__ import annotations

import os
import re
import signal
import subprocess
from contextlib import suppress
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlsplit

from pydantic import BaseModel, ConfigDict, Field

from course_harness.core.release_files import RELEASE_FILES_REF_PREFIX
from course_harness.release_service import backfill_release_files
from course_harness.workspace_history import list_revisions

REMOTE_NAME = "origin"
BRANCH = "main"
PUSH_REFSPECS = (
    f"refs/heads/{BRANCH}",
    "refs/tags/course-release/*",
    f"{RELEASE_FILES_REF_PREFIX}*",
)
PUSH_COMMAND = (
    f"git push {REMOTE_NAME} {BRANCH} 'refs/tags/course-release/*' '{RELEASE_FILES_REF_PREFIX}*'"
)
CONFIG_TIMEOUT_SECONDS = 5
PUSH_TIMEOUT_SECONDS = 300
MAX_URL_LENGTH = 2_048
MAX_REPORTED_OUTPUT = 4_000
# ``[user@]host:path``; Git reads this form as SSH when no slash comes before the colon.
_SCP_LIKE = re.compile(r"(?:[A-Za-z0-9._-]+@)?([^@:/\s]+):(\S+)")
_HOST = re.compile(r"[A-Za-z0-9](?:[A-Za-z0-9.-]*[A-Za-z0-9])?|\[[0-9A-Fa-f:.]+\]")
_URL_USERINFO = re.compile(r"(?i)\b([a-z][a-z0-9+.-]*://)[^/\s@]*@")
# Environment that would point Git at another repository, object store or configuration.
_REDIRECTING_ENVIRONMENT = (
    "GIT_DIR",
    "GIT_WORK_TREE",
    "GIT_INDEX_FILE",
    "GIT_OBJECT_DIRECTORY",
    "GIT_ALTERNATE_OBJECT_DIRECTORIES",
    "GIT_COMMON_DIR",
    "GIT_NAMESPACE",
    "GIT_CONFIG",
    "GIT_CONFIG_PARAMETERS",
    "GIT_CONFIG_COUNT",
)


class WorkspaceRemoteError(RuntimeError):
    """The Workspace remote cannot be read, changed or pushed to."""


class InvalidRemoteUrl(WorkspaceRemoteError, ValueError):
    """The URL is not an HTTPS or SSH Git URL that may be stored."""


class RemoteSettings(BaseModel):
    """The Workspace's Git remote and the terminal command that Push runs."""

    name: str = REMOTE_NAME
    url: str | None
    branch: str = BRANCH
    push_command: str = PUSH_COMMAND


class RemoteUpdateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    url: str | None = Field(default=None, max_length=MAX_URL_LENGTH)


class PushResult(BaseModel):
    """What one Push sent, or why Git refused it."""

    ok: bool
    remote: str
    message: str
    # Releases whose files are neither in Git nor in this computer's Release data.
    releases_without_files: list[str] = Field(default_factory=list)


def validate_remote_url(url: str) -> str:
    """Accept ``https://`` and ``ssh://`` URLs and scp-like ``user@host:path`` addresses."""
    url = url.strip()
    if not url or len(url) > MAX_URL_LENGTH:
        raise InvalidRemoteUrl("Enter the Git URL of the remote repository.")
    if url.startswith("-") or any(character.isspace() or ord(character) < 32 for character in url):
        raise InvalidRemoteUrl("A Git URL cannot contain spaces or start with a hyphen.")
    if "::" in url:
        raise InvalidRemoteUrl("Use an HTTPS or SSH Git URL.")
    if "://" in url:
        try:
            parts = urlsplit(url)
            hostname = parts.hostname
            parts.port  # noqa: B018 - raises for an invalid port
        except ValueError as error:
            raise InvalidRemoteUrl("Use an HTTPS or SSH Git URL.") from error
        if parts.scheme not in {"https", "ssh"} or not hostname or parts.path in {"", "/"}:
            raise InvalidRemoteUrl("Use an HTTPS or SSH Git URL with a repository path.")
        if parts.password is not None:
            raise InvalidRemoteUrl(
                "Leave passwords and tokens out of the URL; Git asks your credential helper."
            )
        if parts.query or parts.fragment:
            raise InvalidRemoteUrl("A Git URL cannot have a query or fragment.")
        return url
    match = _SCP_LIKE.fullmatch(url)
    if match is None or not _HOST.fullmatch(match.group(1)):
        raise InvalidRemoteUrl(
            "Use an HTTPS URL or an SSH address such as git@host:owner/repo.git."
        )
    return url


def redact(text: str) -> str:
    """Hide any credentials Git prints inside URLs."""
    return _URL_USERINFO.sub(r"\1***@", text)


def read_remote(workspace: Path) -> RemoteSettings:
    list_revisions(workspace)
    result = _config_git(workspace, "config", "--local", "--get", f"remote.{REMOTE_NAME}.url")
    if result.returncode == 1:
        return RemoteSettings(url=None)
    if result.returncode != 0:
        raise WorkspaceRemoteError("The Workspace's Git remote cannot be read")
    url = result.stdout.decode("utf-8", errors="replace").strip()
    return RemoteSettings(url=redact(url) or None)


def set_remote(workspace: Path, url: str | None) -> RemoteSettings:
    """Set, replace or (with ``None``) remove the Workspace's ``origin`` remote."""
    list_revisions(workspace)
    exists = _config_git(workspace, "remote", "get-url", REMOTE_NAME).returncode == 0
    if url is None or not url.strip():
        if exists and _config_git(workspace, "remote", "remove", REMOTE_NAME).returncode != 0:
            raise WorkspaceRemoteError("The Workspace's Git remote cannot be removed")
        return RemoteSettings(url=None)
    valid = validate_remote_url(url)
    arguments = ("set-url", REMOTE_NAME, valid) if exists else ("add", REMOTE_NAME, valid)
    if _config_git(workspace, "remote", *arguments).returncode != 0:
        raise WorkspaceRemoteError("The Workspace's Git remote cannot be saved")
    return read_remote(workspace)


@dataclass(frozen=True)
class PushPlan:
    """A configured remote, with every Release whose files could be recorded now in Git."""

    remote: str
    releases_without_files: list[str]


def prepare_push(workspace: Path, *, release_data_root: Path) -> PushPlan:
    """Require a remote and record any missing Release files refs from local Release data."""
    remote = read_remote(workspace)
    if remote.url is None:
        raise WorkspaceRemoteError("Set a Git remote before pushing.")
    backfill = backfill_release_files(workspace=workspace, release_data_root=release_data_root)
    return PushPlan(remote=remote.url, releases_without_files=backfill.missing)


def push_workspace(
    workspace: Path, plan: PushPlan, *, timeout: float = PUSH_TIMEOUT_SECONDS
) -> PushResult:
    """Push the branch, the Release tags and the Release files refs; see ``PUSH_COMMAND``."""
    ok, message = _run_push(workspace, timeout=timeout)
    return PushResult(
        ok=ok,
        remote=plan.remote,
        message=message,
        releases_without_files=plan.releases_without_files,
    )


def _run_push(workspace: Path, *, timeout: float) -> tuple[bool, str]:
    command = [
        "git",
        "-c",
        "core.hooksPath=/dev/null",
        "-c",
        "core.fsmonitor=false",
        "-c",
        "protocol.ext.allow=never",
        "push",
        "--porcelain",
        REMOTE_NAME,
        *PUSH_REFSPECS,
    ]
    environment = {
        key: value for key, value in os.environ.items() if key not in _REDIRECTING_ENVIRONMENT
    }
    environment["GIT_TERMINAL_PROMPT"] = "0"
    try:
        # A new session has no controlling terminal, so nothing can wait on a hidden prompt.
        process = subprocess.Popen(
            command,
            cwd=workspace,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            env=environment,
            start_new_session=True,
        )
    except OSError as error:
        raise WorkspaceRemoteError("Git could not be started") from error
    try:
        stdout, stderr = process.communicate(timeout=timeout)
    except subprocess.TimeoutExpired:
        with suppress(OSError):
            os.killpg(process.pid, signal.SIGKILL)
        process.communicate()
        return False, f"The push did not finish within {int(timeout)} seconds."
    output = _trim(redact(stderr.decode("utf-8", errors="replace")))
    if process.returncode != 0:
        detail = _trim(redact(stdout.decode("utf-8", errors="replace")))
        return False, output or detail or "Git could not push to the remote."
    return True, _summary(stdout.decode("utf-8", errors="replace"))


def _summary(porcelain: str) -> str:
    updated = sum(
        1
        for line in porcelain.splitlines()
        if line[:1] in {" ", "+", "*"} and "\t" in line and not line.startswith("To ")
    )
    if updated == 0:
        return "Everything was already up to date."
    return f"Pushed {updated} {'ref' if updated == 1 else 'refs'}."


def _trim(text: str) -> str:
    text = text.strip()
    return text if len(text) <= MAX_REPORTED_OUTPUT else "…" + text[-MAX_REPORTED_OUTPUT:]


def _config_git(workspace: Path, *arguments: str) -> subprocess.CompletedProcess[bytes]:
    environment = {
        key: value for key, value in os.environ.items() if key not in _REDIRECTING_ENVIRONMENT
    }
    try:
        return subprocess.run(
            ["git", "-c", "core.hooksPath=/dev/null", "-c", "core.fsmonitor=false", *arguments],
            cwd=workspace,
            check=False,
            capture_output=True,
            stdin=subprocess.DEVNULL,
            timeout=CONFIG_TIMEOUT_SECONDS,
            env=environment,
        )
    except (OSError, subprocess.TimeoutExpired) as error:
        raise WorkspaceRemoteError("The Workspace's Git remote cannot be read") from error
