"""Push and sync a Course and its Releases through the Course Workspace's Git remote.

The remote is an ordinary Git remote named ``origin`` in the Workspace's ``.git/config``: it is
neither canonical Course state nor a secret, and a URL with an embedded password is refused.
Pushing is always the Course Author's explicit action. It runs the system ``git push`` with the
Author's own Git configuration and credentials (SSH agent, credential helpers), exactly as the
terminal command it mirrors would, but with hooks disabled and no terminal prompt.
"""

from __future__ import annotations

import json
import os
import re
import signal
import subprocess
import tempfile
from contextlib import suppress
from datetime import UTC, datetime
from pathlib import Path
from urllib.parse import urlsplit

from pydantic import BaseModel, ConfigDict, Field

from course_harness.core.release_files import RELEASE_FILES_REF_PREFIX
from course_harness.core.releases import ReleaseError, read_release
from course_harness.release_service import ReleaseFilesBackfill
from course_harness.workspace_history import list_revisions

REMOTE_NAME = "origin"
BRANCH = "main"
RELEASE_TAGS = "refs/tags/course-release/*"
PUSH_COMMAND = (
    f"git push {REMOTE_NAME} {BRANCH} 'refs/tags/course-release/*' '{RELEASE_FILES_REF_PREFIX}*'"
)
CONFIG_TIMEOUT_SECONDS = 5
PUSH_TIMEOUT_SECONDS = 300
CHECK_TIMEOUT_SECONDS = 60
# The remote's ``main`` as of the last check; Sync fast-forwards to it.
REMOTE_MAIN = f"refs/remotes/{REMOTE_NAME}/{BRANCH}"
# What the last check saw on the remote, kept beside the repository's other private records.
REMOTE_CHECK_FILE = "course-harness-remote-check.json"
MAX_REMOTE_CHECK_BYTES = 1_000_000
# Development only: also accept an absolute local path, such as a bare test repository.
ALLOW_LOCAL_REMOTES_ENVIRONMENT = "COURSE_HARNESS_ALLOW_LOCAL_REMOTES"
MAX_URL_LENGTH = 2_048
MAX_REPORTED_OUTPUT = 4_000
# ``[user@]host:path``; Git reads this form as SSH when no slash comes before the colon.
_SCP_LIKE = re.compile(r"(?:[A-Za-z0-9._-]+@)?([^@:/\s]+):(\S+)")
_HOST = re.compile(r"[A-Za-z0-9](?:[A-Za-z0-9.-]*[A-Za-z0-9])?|\[[0-9A-Fa-f:.]+\]")
_URL_USERINFO = re.compile(r"(?i)\b([a-z][a-z0-9+.-]*://)[^/\s@]*@")
_URL_PASSWORD = re.compile(r"(?i)\b([a-z][a-z0-9+.-]*://[^/\s:@]*):[^/\s@]*@")
_OID = re.compile(r"(?:[0-9a-f]{40}|[0-9a-f]{64})")
_SLUG = re.compile(r"[a-z0-9](?:[a-z0-9-]{0,62}[a-z0-9])?")
MAX_REMOTE_REFS = 10_000
_LOCAL_HINT = "To use a local folder, run `git remote add origin <path>` in a terminal."
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

    url: str | None
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
    # Releases whose files ref does not match the manifest; their files ref was not pushed.
    releases_with_invalid_files: list[str] = Field(default_factory=list)


class RemoteCheck(BaseModel):
    """The remote's shared refs as of the last check, or why the check failed."""

    url: str
    checked_at: str
    # Ref name to object ID for ``main``, the Release tags and the Release files refs.
    refs: dict[str, str] = Field(default_factory=dict)
    # The remote's ``main`` as fetched into ``REMOTE_MAIN``; Sync moves forward only to this.
    fetched_main: str | None = None
    error: str | None = None
    # Releases whose files ref the last push left out because it does not match the manifest.
    skipped_files: list[str] = Field(default_factory=list)


def validate_remote_url(url: str) -> str:
    """Accept ``https://`` and ``ssh://`` URLs and scp-like ``user@host:path`` addresses."""
    url = url.strip()
    if not url or len(url) > MAX_URL_LENGTH:
        raise InvalidRemoteUrl("Enter the Git URL of the remote repository.")
    if (
        os.environ.get(ALLOW_LOCAL_REMOTES_ENVIRONMENT) == "1"
        and url.startswith("/")
        and not any(character.isspace() or ord(character) < 32 for character in url)
    ):
        return url
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
        if parts.scheme == "file":
            raise InvalidRemoteUrl(f"Use an HTTPS or SSH Git URL. {_LOCAL_HINT}")
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
            "Use an HTTPS URL or an SSH address such as git@host:owner/repo.git. " + _LOCAL_HINT
        )
    return url


def redact(text: str) -> str:
    """Hide any credentials Git prints inside URLs."""
    return _URL_USERINFO.sub(r"\1***@", text)


def remote_label(url: str) -> str:
    """Name a remote as ``host/owner/repo``, the way people say it."""
    if url.startswith("/"):
        return url
    if "://" in url:
        parts = urlsplit(url)
        host, path = parts.hostname or "", parts.path
    else:
        match = _SCP_LIKE.fullmatch(url)
        if match is None:
            return url
        host, path = match.group(1), match.group(2)
    path = path.strip("/").removesuffix(".git")
    return f"{host}/{path}" if path else host


def _hide_password(url: str) -> str:
    """Show a stored URL as it is, except a password someone set outside the app.

    Saving never stores the result: a URL with a password is refused.
    """
    return _URL_PASSWORD.sub(r"\1:***@", url)


def read_remote(workspace: Path) -> RemoteSettings:
    list_revisions(workspace)
    result = _config_git(workspace, "config", "--local", "--get", f"remote.{REMOTE_NAME}.url")
    if result.returncode == 1:
        return RemoteSettings(url=None)
    if result.returncode != 0:
        raise WorkspaceRemoteError("The Workspace's Git remote cannot be read")
    url = result.stdout.decode("utf-8", errors="replace").strip()
    return RemoteSettings(url=_hide_password(url) or None)


def set_remote(workspace: Path, url: str | None) -> RemoteSettings:
    """Set, replace or (with ``None``) remove the Workspace's ``origin`` remote."""
    list_revisions(workspace)
    exists = _config_git(workspace, "remote", "get-url", REMOTE_NAME).returncode == 0
    _forget_remote_check(workspace)
    if url is None or not url.strip():
        if exists and _config_git(workspace, "remote", "remove", REMOTE_NAME).returncode != 0:
            raise WorkspaceRemoteError("The Workspace's Git remote cannot be removed")
        return RemoteSettings(url=None)
    valid = validate_remote_url(url)
    arguments = ("set-url", REMOTE_NAME, valid) if exists else ("add", REMOTE_NAME, valid)
    if _config_git(workspace, "remote", *arguments).returncode != 0:
        raise WorkspaceRemoteError("The Workspace's Git remote cannot be saved")
    return read_remote(workspace)


def push_workspace(
    workspace: Path,
    *,
    remote: str,
    backfill: ReleaseFilesBackfill,
    timeout: float = PUSH_TIMEOUT_SECONDS,
    replace_main_from: str | None = None,
) -> PushResult:
    """Push the branch, the Release tags and the Release files refs; see ``PUSH_COMMAND``.

    Files refs that do not match their manifest are left out, so one bad ref never blocks the
    rest: the glob is then replaced by one refspec per ready Release.
    """
    files = (
        [f"{RELEASE_FILES_REF_PREFIX}{slug}" for slug in backfill.ready]
        if backfill.invalid
        else [f"{RELEASE_FILES_REF_PREFIX}*"]
    )
    refspecs = [f"refs/heads/{BRANCH}", RELEASE_TAGS, *files]
    # Replacing the shared ``main`` is allowed only while it still is the checked commit; the
    # Release tags and files refs are never forced.
    lease = (
        [f"--force-with-lease=refs/heads/{BRANCH}:{replace_main_from}"]
        if replace_main_from is not None
        else []
    )
    if replace_main_from is not None and not _OID.fullmatch(replace_main_from):
        raise WorkspaceRemoteError("The remote must be checked first.")
    ok, message = _run_push(workspace, [*lease, *refspecs], remote=remote, timeout=timeout)
    if ok:
        _remember_skipped_files(workspace, backfill.invalid)
    return PushResult(
        ok=ok,
        remote=remote,
        message=message,
        releases_without_files=backfill.missing,
        releases_with_invalid_files=backfill.invalid,
    )


def _run_push(
    workspace: Path, refspecs: list[str], *, remote: str, timeout: float
) -> tuple[bool, str]:
    returncode, stdout, stderr = _run_network_git(
        workspace,
        [
            "-c",
            "protocol.ext.allow=never",
            "push",
            "--porcelain",
            *[item for item in refspecs if item.startswith("--")],
            REMOTE_NAME,
            *[item for item in refspecs if not item.startswith("--")],
        ],
        timeout=timeout,
        what="push",
    )
    output = _trim(redact(stderr))
    if returncode != 0:
        detail = _trim(redact(stdout))
        return False, output or detail or "Git could not push to the remote."
    return True, _summary(stdout, remote)


def _run_network_git(
    workspace: Path,
    arguments: list[str],
    *,
    timeout: float,
    what: str,
    interactive: bool = True,
) -> tuple[int, str, str]:
    """Run Git against the remote as the Course Author would, but never on a hidden prompt.

    A background (non-interactive) run also never opens a sign-in window: no askpass helper,
    no interactive credential helper, no Git Credential Manager dialog.
    """
    quiet = [] if interactive else ["-c", "core.askPass=", "-c", "credential.interactive=false"]
    command = [
        "git",
        "-c",
        "core.hooksPath=/dev/null",
        "-c",
        "core.fsmonitor=false",
        *quiet,
        *arguments,
    ]
    environment = {
        key: value for key, value in os.environ.items() if key not in _REDIRECTING_ENVIRONMENT
    }
    environment["GIT_TERMINAL_PROMPT"] = "0"
    if not interactive:
        environment.pop("SSH_ASKPASS", None)
        environment.pop("GIT_ASKPASS", None)
        environment["SSH_ASKPASS_REQUIRE"] = "never"
        environment["GCM_INTERACTIVE"] = "never"
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
        with suppress(subprocess.TimeoutExpired):
            process.communicate(timeout=5)
        return 124, "", f"The {what} did not finish within {int(timeout)} seconds."
    return (
        process.returncode,
        stdout.decode("utf-8", errors="replace"),
        stderr.decode("utf-8", errors="replace"),
    )


def _raw_remote_url(workspace: Path) -> str | None:
    result = _config_git(workspace, "config", "--local", "--get", f"remote.{REMOTE_NAME}.url")
    if result.returncode != 0:
        return None
    return result.stdout.decode("utf-8", errors="replace").strip() or None


def check_remote(
    workspace: Path, *, timeout: float = CHECK_TIMEOUT_SECONDS, interactive: bool = False
) -> RemoteCheck:
    """Learn which shared refs the remote has, and fetch its ``main`` and new Release tags.

    ``main`` goes to ``REMOTE_MAIN``. Release tags that this computer lacks are fetched, never
    forced, so a Release published elsewhere counts here; a local tag is never replaced.
    Release files refs are only compared by object ID, so a check never downloads Release
    files. The result is kept for ``read_remote_check``. Background checks are
    non-interactive; Sync's own check may ask for credentials the usual way.
    """
    list_revisions(workspace)
    url = _raw_remote_url(workspace)
    if url is None:
        raise WorkspaceRemoteError("Set a Git remote before checking it.")
    previous = read_remote_check(workspace)
    checked_at = datetime.now(UTC).isoformat()
    skipped = previous.skipped_files if previous is not None else []

    def failed(message: str) -> RemoteCheck:
        check = RemoteCheck(
            url=url,
            checked_at=checked_at,
            refs=previous.refs if previous is not None else {},
            fetched_main=previous.fetched_main if previous is not None else None,
            error=message,
            skipped_files=skipped,
        )
        _write_remote_check(workspace, check)
        return check

    returncode, stdout, stderr = _run_network_git(
        workspace,
        ["-c", "protocol.ext.allow=never", "ls-remote", "--refs", REMOTE_NAME],
        timeout=timeout,
        what="check",
        interactive=interactive,
    )
    if returncode != 0:
        return failed(_trim(redact(stderr)) or "Git could not reach the remote.")
    refs: dict[str, str] = {}
    for line in stdout.splitlines():
        oid, _, name = line.partition("\t")
        if not _OID.fullmatch(oid):
            continue
        if name == f"refs/heads/{BRANCH}" or (
            name.startswith(("refs/tags/course-release/", RELEASE_FILES_REF_PREFIX))
            and _SLUG.fullmatch(name.rsplit("/", 1)[1])
        ):
            refs[name] = oid
        if len(refs) > MAX_REMOTE_REFS:
            return failed("The remote has too many refs to compare.")
    local_tags = set(_local_ref_names(workspace, "refs/tags/course-release/"))
    new_tags = sorted(
        name
        for name in refs
        if name.startswith("refs/tags/course-release/") and name not in local_tags
    )
    refspecs = [f"{name}:{name}" for name in new_tags]
    if f"refs/heads/{BRANCH}" in refs:
        refspecs.insert(0, f"+refs/heads/{BRANCH}:{REMOTE_MAIN}")
    fetched_main: str | None = None
    if refspecs:
        returncode, _stdout, stderr = _run_network_git(
            workspace,
            [
                "-c",
                "protocol.ext.allow=never",
                "-c",
                "gc.auto=0",
                "-c",
                "maintenance.auto=false",
                "fetch",
                "--quiet",
                "--no-tags",
                "--refmap=",
                "--no-write-fetch-head",
                "--no-recurse-submodules",
                REMOTE_NAME,
                *refspecs,
            ],
            timeout=timeout,
            what="check",
            interactive=interactive,
        )
        if returncode != 0:
            return failed(_trim(redact(stderr)) or "Git could not fetch from the remote.")
        # A Release tag from the remote is kept only if it is a valid Release here too.
        for name in new_tags:
            try:
                valid = read_release(workspace, name.rsplit("/", 1)[1]) is not None
            except ReleaseError:
                valid = False
            if not valid:
                _config_git(workspace, "update-ref", "-d", name)
    if f"refs/heads/{BRANCH}" in refs:
        resolved = _config_git(workspace, "rev-parse", "--verify", "--quiet", REMOTE_MAIN)
        fetched_main = resolved.stdout.decode("ascii", errors="replace").strip() or None
        if fetched_main is None or not _OID.fullmatch(fetched_main):
            return failed("Git could not fetch from the remote.")
        # What was fetched is what Sync compares with, even if the remote moved since.
        refs[f"refs/heads/{BRANCH}"] = fetched_main
    else:
        _config_git(workspace, "update-ref", "-d", REMOTE_MAIN)
    check = RemoteCheck(
        url=url,
        checked_at=checked_at,
        refs=refs,
        fetched_main=fetched_main,
        skipped_files=skipped,
    )
    _write_remote_check(workspace, check)
    return check


def _local_ref_names(workspace: Path, prefix: str) -> list[str]:
    result = _config_git(workspace, "for-each-ref", "--format=%(refname)", prefix)
    if result.returncode != 0:
        raise WorkspaceRemoteError("The Workspace's Git refs cannot be read")
    return result.stdout.decode("utf-8", errors="replace").split()


def _remember_skipped_files(workspace: Path, slugs: list[str]) -> None:
    check = read_remote_check(workspace)
    if check is not None and check.skipped_files != slugs:
        _write_remote_check(workspace, check.model_copy(update={"skipped_files": slugs}))


def read_remote_check(workspace: Path) -> RemoteCheck | None:
    """The last check of the current remote, or ``None`` if it was never checked."""
    url = _raw_remote_url(workspace)
    path = workspace / ".git" / REMOTE_CHECK_FILE
    if url is None or path.is_symlink():
        return None
    try:
        with path.open("rb") as stream:
            content = stream.read(MAX_REMOTE_CHECK_BYTES + 1)
        if len(content) > MAX_REMOTE_CHECK_BYTES:
            return None
        check = RemoteCheck.model_validate(json.loads(content))
    except OSError, ValueError:
        return None
    return check if check.url == url else None


def _write_remote_check(workspace: Path, check: RemoteCheck) -> None:
    dot_git = workspace / ".git"
    with suppress(OSError):
        descriptor, temporary = tempfile.mkstemp(dir=dot_git, prefix=".remote-check-")
        try:
            with os.fdopen(descriptor, "wb") as stream:
                stream.write(check.model_dump_json().encode())
            os.replace(temporary, dot_git / REMOTE_CHECK_FILE)
        except OSError:
            with suppress(OSError):
                os.unlink(temporary)


def _forget_remote_check(workspace: Path) -> None:
    with suppress(FileNotFoundError):
        (workspace / ".git" / REMOTE_CHECK_FILE).unlink()


def _summary(porcelain: str, remote: str) -> str:
    updated = sum(
        1
        for line in porcelain.splitlines()
        if line[:1] in {" ", "+", "*"} and "\t" in line and not line.startswith("To ")
    )
    if updated == 0:
        return f"{remote} is already up to date."
    return f"Pushed {updated} {'ref' if updated == 1 else 'refs'} to {remote}."


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
