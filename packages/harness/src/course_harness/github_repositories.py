"""Create a GitHub repository for a Course through the Course Author's own GitHub CLI.

Course Harness never holds a GitHub token: it runs ``gh`` as the Course Author, as they would in
a terminal, and only when they ask. Without ``gh``, or signed out of it, the Course Author pastes
a Git URL instead.
"""

from __future__ import annotations

import os
import re
import shutil
import subprocess
import unicodedata
from typing import Literal

from pydantic import BaseModel, ConfigDict, field_validator

from course_harness.workspace_remote import redact

GITHUB_HOST = "github.com"
GH_TIMEOUT_SECONDS = 30
MAX_DESCRIPTION_CHARACTERS = 350
REPOSITORY_NAME_PATTERN = r"^[A-Za-z0-9_][A-Za-z0-9_.-]{0,99}$"
_OWNER = re.compile(r"[A-Za-z0-9](?:[A-Za-z0-9-]{0,38})")


class GitHubCliError(RuntimeError):
    """The GitHub CLI could not do what was asked; the message is safe to show."""


class GitHubCliStatus(BaseModel):
    """Whether "Create a GitHub repository" can be offered, and with what defaults."""

    available: bool
    # Why it cannot: ``gh`` is not installed, or not signed in to github.com.
    reason: Literal["missing", "signed-out"] | None = None
    owner: str | None = None
    protocol: Literal["ssh", "https"] = "https"


class GitHubRepositoryRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    name: str
    visibility: Literal["public", "private"]

    @field_validator("name")
    @classmethod
    def name_is_a_repository_name(cls, value: str) -> str:
        if not re.fullmatch(REPOSITORY_NAME_PATTERN, value) or value.endswith(".git"):
            raise ValueError(
                "Use up to 100 letters, numbers, hyphens, dots or underscores for the "
                "repository name, starting with a letter, number or underscore."
            )
        return value


def suggested_repository_name(course_title: str | None) -> str:
    """``course-<slug>`` from the Course title, as GitHub would accept it."""
    text = unicodedata.normalize("NFKD", course_title or "")
    slug = re.sub(r"[^a-z0-9]+", "-", text.encode("ascii", "ignore").decode().lower()).strip("-")
    return f"course-{slug}"[:100].rstrip("-") if slug else "course"


def _gh(*arguments: str) -> subprocess.CompletedProcess[str]:
    executable = shutil.which("gh")
    if executable is None:
        raise GitHubCliError("The GitHub CLI (gh) is not installed.")
    environment = dict(os.environ)
    environment["GH_PROMPT_DISABLED"] = "1"
    environment["GH_NO_UPDATE_NOTIFIER"] = "1"
    try:
        return subprocess.run(
            [executable, *arguments],
            check=False,
            capture_output=True,
            text=True,
            stdin=subprocess.DEVNULL,
            timeout=GH_TIMEOUT_SECONDS,
            env=environment,
            start_new_session=True,
        )
    except (OSError, subprocess.TimeoutExpired) as error:
        raise GitHubCliError("The GitHub CLI did not respond.") from error


def github_cli_status() -> GitHubCliStatus:
    if shutil.which("gh") is None:
        return GitHubCliStatus(available=False, reason="missing")
    try:
        if _gh("auth", "status", "--hostname", GITHUB_HOST).returncode != 0:
            return GitHubCliStatus(available=False, reason="signed-out")
        user = _gh("api", "--hostname", GITHUB_HOST, "user", "--jq", ".login")
    except GitHubCliError:
        return GitHubCliStatus(available=False, reason="signed-out")
    owner = user.stdout.strip()
    if user.returncode != 0 or not _OWNER.fullmatch(owner):
        return GitHubCliStatus(available=False, reason="signed-out")
    try:
        configured = _gh("config", "get", "git_protocol", "--host", GITHUB_HOST).stdout.strip()
    except GitHubCliError:
        configured = ""
    return GitHubCliStatus(
        available=True, owner=owner, protocol="ssh" if configured == "ssh" else "https"
    )


def create_github_repository(request: GitHubRepositoryRequest, *, description: str) -> str:
    """Create an empty repository owned by the signed-in user and return its Git URL."""
    status = github_cli_status()
    if not status.available or status.owner is None:
        raise GitHubCliError("Sign in to the GitHub CLI with `gh auth login` first.")
    description = " ".join(description.split())[:MAX_DESCRIPTION_CHARACTERS]
    full_name = f"{status.owner}/{request.name}"
    # The host is named, so gh never picks another signed-in host; ``--option=value`` keeps a
    # title starting with a hyphen from being read as an option.
    arguments = ["repo", "create", f"{GITHUB_HOST}/{full_name}", f"--{request.visibility}"]
    if description:
        arguments.append(f"--description={description}")
    result = _gh(*arguments)
    if result.returncode != 0:
        detail = redact((result.stderr or result.stdout).strip())[-2_000:]
        raise GitHubCliError(detail or f"GitHub did not create {full_name}.")
    if status.protocol == "ssh":
        return f"git@{GITHUB_HOST}:{full_name}.git"
    return f"https://{GITHUB_HOST}/{full_name}.git"
