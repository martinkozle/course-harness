"""Automatic Course Revisions, Publish and Sync counts, fast-forward Sync and GitHub setup."""

import json
import os
import stat
import subprocess
from pathlib import Path

import httpx2
import pytest

from course_harness import release_service, workspace_remote
from course_harness.app import create_app
from course_harness.core.course_plan import CoursePlan, Lecture, read_course_plan, write_course_plan
from course_harness.core.presentation import Presentation, TitleSlide, write_presentation
from course_harness.core.quiz import Quiz, QuizOption, QuizQuestion, write_quiz
from course_harness.core.releases import ReleaseError, canonical_json, read_release
from course_harness.release_service import backfill_release_files
from course_harness.workspace_history import (
    KEPT_REF_PREFIX,
    FastForwardRefused,
    RevisionCreateRequest,
    create_revision,
    fast_forward_main,
    list_revisions,
    read_current_state,
    record_app_authored_state,
)
from course_harness.workspace_remote import (
    ALLOW_LOCAL_REMOTES_ENVIRONMENT,
    InvalidRemoteUrl,
    check_remote,
    push_workspace,
    remote_label,
    validate_remote_url,
)
from course_harness.workspace_sync import (
    AutomaticRevisionBlocked,
    automatic_revision_summary,
    create_automatic_revision,
    sharing_status,
)

LECTURES = [
    Lecture(
        id=f"lecture-{letter * 12}",
        title=title,
        presentation_id=f"presentation-{letter * 12}",
    )
    for letter, title in [
        ("a", "Foundations"),
        ("b", "Building an MCP Server"),
        ("c", "Evaluation"),
    ]
]


def _git(repository: Path, *arguments: str, input_text: str | None = None) -> str:
    return subprocess.run(
        [
            "git",
            "-c",
            "user.name=Course Harness tests",
            "-c",
            "user.email=course-harness-tests@example.invalid",
            *arguments,
        ],
        cwd=repository,
        input=input_text,
        check=True,
        capture_output=True,
        text=True,
    ).stdout


def _plan(title: str = "Agents in practice") -> CoursePlan:
    return CoursePlan(
        id="course-aaaaaaaaaaaa",
        title=title,
        audience="Graduate students",
        lectures=list(LECTURES),
    )


def _slides(lecture: Lecture, title: str) -> Presentation:
    assert lecture.presentation_id is not None
    return Presentation(
        id=lecture.presentation_id,
        lecture_id=lecture.id,
        slides=[TitleSlide(id=f"slide-{lecture.id[-12:]}", title=title)],
    )


def _quiz(lecture: Lecture, prompt: str) -> Quiz:
    suffix = lecture.id[-12:]
    return Quiz(
        lecture_id=lecture.id,
        questions=[
            QuizQuestion(
                id=f"question-{suffix}",
                prompt=prompt,
                options=[
                    QuizOption(id=f"option-{suffix}", text="Yes", correct=True),
                    QuizOption(id=f"option-{suffix[:-1]}f", text="No"),
                ],
            )
        ],
    )


def _course(workspace: Path) -> None:
    workspace.mkdir()
    _git(workspace, "init", "--quiet", "--initial-branch=main")
    write_course_plan(workspace, _plan())
    for lecture in LECTURES:
        write_presentation(workspace, _slides(lecture, lecture.title))
        write_quiz(workspace, _quiz(lecture, "Is this covered?"))
    create_revision(workspace, RevisionCreateRequest(summary="Initial Course"))
    record_app_authored_state(workspace)


def _edit(workspace: Path) -> None:
    """Edit Lecture 2's Slides, Lecture 3's Quiz and the Course Plan, as the app would."""
    write_presentation(workspace, _slides(LECTURES[1], "Servers and tools"))
    write_quiz(workspace, _quiz(LECTURES[2], "Is this measured?"))
    write_course_plan(workspace, _plan("Agents in production"))
    record_app_authored_state(workspace)


def _bare_remote(tmp_path: Path, workspace: Path) -> Path:
    remote = tmp_path / "remote.git"
    _git(tmp_path, "init", "--quiet", "--bare", str(remote))
    _git(workspace, "remote", "add", "origin", str(remote))
    return remote


def _commit_elsewhere(tmp_path: Path, remote: Path, title: str) -> None:
    """Another computer changes the Course title and pushes it."""
    other = tmp_path / "other"
    if not other.exists():
        _git(tmp_path, "clone", "--quiet", str(remote), str(other))
    _git(other, "pull", "--quiet", "--ff-only")
    write_course_plan(other, _plan(title))
    _git(other, "add", "course.yaml")
    _git(other, "commit", "--quiet", "-m", f"Rename the Course to {title}")
    _git(other, "push", "--quiet", "origin", "main")


def _app(workspace: Path, tmp_path: Path):
    return create_app(
        workspace,
        release_data_path=tmp_path / "releases",
        templates_data_path=tmp_path / "templates",
    )


def _client(app) -> httpx2.AsyncClient:
    return httpx2.AsyncClient(transport=httpx2.ASGITransport(app=app), base_url="http://test")


def test_the_automatic_revision_summary_names_what_changed(tmp_path: Path) -> None:
    workspace = tmp_path / "course"
    _course(workspace)
    _edit(workspace)

    state = read_current_state(workspace)
    assert automatic_revision_summary(state, read_course_plan(workspace)) == (
        "Edited Lecture 2 'Building an MCP Server', the Quiz for Lecture 3 and the Course Plan"
    )
    revision = create_automatic_revision(workspace)

    assert revision is not None
    assert revision.summary.startswith("Edited Lecture 2")
    assert read_current_state(workspace).clean
    # Nothing to save is not an error.
    assert create_automatic_revision(workspace) is None


def test_added_and_removed_files_get_their_own_verbs(tmp_path: Path) -> None:
    workspace = tmp_path / "course"
    _course(workspace)
    (workspace / "quizzes" / f"{LECTURES[0].id}.yaml").unlink()
    record_app_authored_state(workspace)

    summary = automatic_revision_summary(read_current_state(workspace), read_course_plan(workspace))

    assert summary == "Removed the Quiz for Lecture 1"


def test_outside_changes_do_not_become_a_revision_automatically(tmp_path: Path) -> None:
    workspace = tmp_path / "course"
    _course(workspace)
    (workspace / "course.yaml").write_text("id: course-aaaaaaaaaaaa\nlectures: nope\n")

    with pytest.raises(AutomaticRevisionBlocked, match="outside Course Harness"):
        create_automatic_revision(workspace)

    status = sharing_status(workspace)
    assert status.blocked == "drift"
    assert status.pending_summary is None
    assert len(list_revisions(workspace)) == 1


def test_changes_since_the_last_release_count_revisions_and_uncommitted_edits(
    tmp_path: Path,
) -> None:
    workspace = tmp_path / "course"
    _course(workspace)

    before = sharing_status(workspace)
    assert before.release_count == 0
    assert before.changes_since_release == 1
    assert before.revision_summaries == ["Initial Course"]

    _git(workspace, "tag", "-a", "-m", "not a manifest", "unrelated")
    _edit(workspace)
    edited = sharing_status(workspace)
    assert edited.uncommitted
    assert edited.changes_since_release == 2
    assert edited.pending_summary is not None
    assert edited.pending_summary.startswith("Edited Lecture 2")

    create_automatic_revision(workspace)
    saved = sharing_status(workspace)
    assert saved.changes_since_release == 2
    assert not saved.uncommitted
    assert saved.revision_summaries[0] == "Initial Course"


def test_ahead_and_behind_compare_with_the_last_check_of_the_remote(tmp_path: Path) -> None:
    workspace = tmp_path / "course"
    _course(workspace)
    assert sharing_status(workspace).remote_url is None
    remote = _bare_remote(tmp_path, workspace)

    unchecked = sharing_status(workspace)
    assert unchecked.remote_url == str(remote)
    assert unchecked.ahead is None

    check_remote(workspace)
    empty = sharing_status(workspace)
    # The remote has nothing yet: the one Revision is ahead.
    assert (empty.ahead, empty.behind) == (1, 0)

    _git(workspace, "push", "--quiet", "origin", "main")
    check_remote(workspace)
    assert (sharing_status(workspace).ahead, sharing_status(workspace).behind) == (0, 0)

    _edit(workspace)
    assert sharing_status(workspace).ahead == 1

    _commit_elsewhere(tmp_path, remote, "Agents elsewhere")
    check_remote(workspace)
    assert (sharing_status(workspace).ahead, sharing_status(workspace).behind) == (1, 1)


def test_a_failed_check_is_reported_and_keeps_the_previous_counts(tmp_path: Path) -> None:
    workspace = tmp_path / "course"
    _course(workspace)
    remote = _bare_remote(tmp_path, workspace)
    _git(workspace, "push", "--quiet", "origin", "main")
    check_remote(workspace)
    remote.rename(tmp_path / "moved.git")

    failed = check_remote(workspace)
    status = sharing_status(workspace)

    assert failed.error is not None
    assert status.check_error == failed.error
    assert (status.ahead, status.behind) == (0, 0)


def test_fast_forward_receives_remote_revisions_into_the_working_course(tmp_path: Path) -> None:
    workspace = tmp_path / "course"
    _course(workspace)
    remote = _bare_remote(tmp_path, workspace)
    _git(workspace, "push", "--quiet", "origin", "main")
    _commit_elsewhere(tmp_path, remote, "Agents elsewhere")
    check = check_remote(workspace)

    received = fast_forward_main(workspace, check.refs["refs/heads/main"])

    assert received == 1
    plan = read_course_plan(workspace)
    assert plan is not None and plan.title == "Agents elsewhere"
    state = read_current_state(workspace)
    assert state.clean and state.drift == "clean"
    assert list_revisions(workspace)[0].summary == "Rename the Course to Agents elsewhere"


def test_fast_forward_refuses_uncommitted_edits_and_diverged_history(tmp_path: Path) -> None:
    workspace = tmp_path / "course"
    _course(workspace)
    remote = _bare_remote(tmp_path, workspace)
    _git(workspace, "push", "--quiet", "origin", "main")
    _commit_elsewhere(tmp_path, remote, "Agents elsewhere")
    target = check_remote(workspace).refs["refs/heads/main"]

    # Both sides changed the Course Plan.
    _edit(workspace)
    with pytest.raises(FastForwardRefused, match="same parts"):
        fast_forward_main(workspace, target)

    create_automatic_revision(workspace)
    head = list_revisions(workspace)[0].id
    with pytest.raises(FastForwardRefused, match="both have changes"):
        fast_forward_main(workspace, target)

    # Nothing was touched.
    assert list_revisions(workspace)[0].id == head
    plan = read_course_plan(workspace)
    assert plan is not None and plan.title == "Agents in production"


@pytest.mark.anyio
async def test_sync_saves_receives_and_sends(tmp_path: Path) -> None:
    workspace = tmp_path / "course"
    _course(workspace)
    remote = _bare_remote(tmp_path, workspace)
    _git(workspace, "push", "--quiet", "origin", "main")
    _commit_elsewhere(tmp_path, remote, "Agents elsewhere")

    async with _client(_app(workspace, tmp_path)) as client:
        checked = await client.post("/api/workspace/remote/check")
        first = await client.post("/api/workspace/sync")
        write_quiz(workspace, _quiz(LECTURES[0], "Is this new?"))
        record_app_authored_state(workspace)
        second = await client.post("/api/workspace/sync")

    assert checked.json()["behind"] == 1
    assert first.status_code == 200, first.text
    assert first.json()["ok"] is True
    assert first.json()["received"] == 1
    assert first.json()["status"]["ahead"] == 0
    assert second.json()["ok"] is True
    assert second.json()["revision"]["summary"] == "Edited the Quiz for Lecture 1"
    assert second.json()["status"]["ahead"] == 0
    remote_head = _git(remote, "log", "-1", "--format=%s", "main").strip()
    assert remote_head == "Edited the Quiz for Lecture 1"


@pytest.mark.anyio
async def test_sync_explains_diverged_history_and_changes_nothing(tmp_path: Path) -> None:
    workspace = tmp_path / "course"
    _course(workspace)
    remote = _bare_remote(tmp_path, workspace)
    _git(workspace, "push", "--quiet", "origin", "main")
    _commit_elsewhere(tmp_path, remote, "Agents elsewhere")
    _edit(workspace)
    remote_before = _git(remote, "rev-parse", "main")

    async with _client(_app(workspace, tmp_path)) as client:
        synced = await client.post("/api/workspace/sync")

    body = synced.json()
    assert body["ok"] is False
    assert "can't combine them" in body["message"]
    assert body["revision"] is not None
    assert _git(remote, "rev-parse", "main") == remote_before
    plan = read_course_plan(workspace)
    assert plan is not None and plan.title == "Agents in production"


@pytest.mark.anyio
async def test_sync_without_a_remote_or_with_invalid_edits(tmp_path: Path) -> None:
    workspace = tmp_path / "course"
    _course(workspace)
    async with _client(_app(workspace, tmp_path)) as client:
        without = await client.post("/api/workspace/sync")
        _bare_remote(tmp_path, workspace)
        (workspace / "course.yaml").write_text("id: course-aaaaaaaaaaaa\nlectures: nope\n")
        invalid = await client.post("/api/workspace/sync")

    assert without.status_code == 409
    assert invalid.status_code == 200
    assert invalid.json()["ok"] is False


@pytest.mark.anyio
async def test_publish_with_notes_then_a_rejected_push_keeps_the_release(tmp_path: Path) -> None:
    workspace = tmp_path / "course"
    _course(workspace)
    remote = _bare_remote(tmp_path, workspace)
    _git(workspace, "push", "--quiet", "origin", "main")
    _commit_elsewhere(tmp_path, remote, "Agents elsewhere")
    _edit(workspace)

    async with _client(_app(workspace, tmp_path)) as client:
        await client.post("/api/workspace/remote/check")
        saved = await client.post("/api/workspace/revisions/automatic")
        selection = {
            "lecture_ids": [lecture.id for lecture in LECTURES],
            "artifact_ids": [lecture.presentation_id for lecture in LECTURES],
        }
        checked = await client.post(
            "/api/releases/validate", json={"selection": selection, "waivers": []}
        )
        waivers = [
            {"finding_id": finding["id"], "justification": "Worked examples."}
            for finding in checked.json()["findings"]
            if finding["severity"] == "warning"
        ]
        published = await client.post(
            "/api/releases",
            json={
                "slug": "release-1",
                "name": "Release 1 · 1 Nov 2026",
                "selection": selection,
                "waivers": waivers,
                "notes": "Edited Lecture 2 'Building an MCP Server'",
            },
        )
        pushed = await client.post("/api/workspace/remote/push")
        status = await client.get("/api/workspace/sharing")

    assert saved.status_code == 200, saved.text
    assert saved.json()["revision"]["summary"].startswith("Edited Lecture 2")
    assert published.status_code == 201, published.text
    release = read_release(workspace, "release-1")
    assert release is not None and release.notes == "Edited Lecture 2 'Building an MCP Server'"
    # Manifests of Releases without notes stay exactly as before.
    assert "notes" not in release.model_copy(update={"notes": None}).model_dump(mode="json")
    assert pushed.json()["ok"] is False
    body = status.json()
    assert body["release_count"] == 1
    assert body["changes_since_release"] == 0
    # The automatic Revision and the Release (its tag and files count as one change).
    assert body["ahead"] == 2
    assert body["behind"] == 1 and body["diverged"] is True


def test_local_remotes_are_accepted_only_in_development(monkeypatch: pytest.MonkeyPatch) -> None:
    with pytest.raises(InvalidRemoteUrl):
        validate_remote_url("/tmp/course.git")
    monkeypatch.setenv(ALLOW_LOCAL_REMOTES_ENVIRONMENT, "1")
    assert validate_remote_url("/tmp/course.git") == "/tmp/course.git"
    with pytest.raises(InvalidRemoteUrl):
        validate_remote_url("relative/course.git")


@pytest.mark.parametrize(
    ("url", "label"),
    [
        ("git@github.com:octo/course.git", "github.com/octo/course"),
        ("https://github.com/octo/course.git", "github.com/octo/course"),
        ("ssh://git@git.example.org:2222/team/course", "git.example.org/team/course"),
    ],
)
def test_remotes_are_named_by_host_and_path(url: str, label: str) -> None:
    assert remote_label(url) == label


def _fake_gh(directory: Path, *, signed_in: bool = True, protocol: str = "ssh") -> Path:
    directory.mkdir()
    log = directory / "calls.jsonl"
    script = directory / "gh"
    script.write_text(
        "#!/usr/bin/env python3\n"
        "import json, sys\n"
        f"open({str(log)!r}, 'a').write(json.dumps(sys.argv[1:]) + '\\n')\n"
        "args = sys.argv[1:]\n"
        "if args[:2] == ['auth', 'status']:\n"
        f"    sys.exit(0 if {signed_in!r} else 1)\n"
        "if args[:1] == ['api']:\n"
        "    print('octo'); sys.exit(0)\n"
        "if args[:2] == ['config', 'get']:\n"
        f"    print({protocol!r}); sys.exit(0)\n"
        "if args[:2] == ['repo', 'create']:\n"
        "    print('https://github.com/' + args[2]); sys.exit(0)\n"
        "sys.exit(2)\n"
    )
    script.chmod(script.stat().st_mode | stat.S_IXUSR)
    return log


@pytest.mark.anyio
async def test_a_github_repository_is_created_with_gh_and_becomes_the_remote(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    workspace = tmp_path / "course"
    _course(workspace)
    log = _fake_gh(tmp_path / "bin")
    monkeypatch.setenv("PATH", f"{tmp_path / 'bin'}{os.pathsep}{os.environ['PATH']}")

    async with _client(_app(workspace, tmp_path)) as client:
        setup = await client.get("/api/workspace/remote/github")
        refused = await client.post(
            "/api/workspace/remote/github", json={"name": "-x", "visibility": "public"}
        )
        created = await client.post(
            "/api/workspace/remote/github",
            json={"name": "course-agents-in-practice", "visibility": "public"},
        )

    assert setup.json() == {
        "available": True,
        "reason": None,
        "owner": "octo",
        "protocol": "ssh",
        "suggested_name": "course-agents-in-practice",
    }
    assert refused.status_code == 422
    assert "repository name" in refused.json()["detail"][0]["msg"]
    assert created.status_code == 201, created.text
    assert created.json()["url"] == "git@github.com:octo/course-agents-in-practice.git"
    calls = [json.loads(line) for line in log.read_text().splitlines()]
    assert [
        "repo",
        "create",
        "github.com/octo/course-agents-in-practice",
        "--public",
        "--description=Agents in practice",
    ] in calls


@pytest.mark.anyio
async def test_without_gh_only_a_pasted_url_is_offered(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    workspace = tmp_path / "course"
    _course(workspace)
    signed_out = tmp_path / "signed-out"
    _fake_gh(signed_out, signed_in=False)

    monkeypatch.setenv("PATH", str(tmp_path / "empty"))
    async with _client(_app(workspace, tmp_path)) as client:
        missing = await client.get("/api/workspace/remote/github")
        monkeypatch.setenv("PATH", f"{signed_out}{os.pathsep}{os.environ['PATH']}")
        out = await client.get("/api/workspace/remote/github")
        created = await client.post(
            "/api/workspace/remote/github", json={"name": "course", "visibility": "private"}
        )

    assert missing.json()["reason"] == "missing"
    assert out.json()["reason"] == "signed-out"
    assert created.status_code == 422


def _edit_quiz_only(workspace: Path) -> None:
    write_quiz(workspace, _quiz(LECTURES[0], "Is this new?"))
    record_app_authored_state(workspace)


@pytest.mark.anyio
async def test_sync_receives_first_when_local_edits_touch_other_files(tmp_path: Path) -> None:
    workspace = tmp_path / "course"
    _course(workspace)
    remote = _bare_remote(tmp_path, workspace)
    _git(workspace, "push", "--quiet", "origin", "main")
    _commit_elsewhere(tmp_path, remote, "Agents elsewhere")
    _edit_quiz_only(workspace)

    async with _client(_app(workspace, tmp_path)) as client:
        synced = await client.post("/api/workspace/sync")

    body = synced.json()
    assert body["ok"] is True, body
    assert body["received"] == 1
    assert body["revision"]["summary"] == "Edited the Quiz for Lecture 1"
    # The edit is saved on top of what was received, so the remote simply moves forward.
    log = _git(remote, "log", "--format=%s", "main").splitlines()
    assert log[:2] == ["Edited the Quiz for Lecture 1", "Rename the Course to Agents elsewhere"]
    plan = read_course_plan(workspace)
    assert plan is not None and plan.title == "Agents elsewhere"
    assert read_current_state(workspace).drift == "clean"


async def _diverge(tmp_path: Path, client: httpx2.AsyncClient) -> tuple[Path, Path, str]:
    workspace = tmp_path / "course"
    remote = tmp_path / "remote.git"
    _commit_elsewhere(tmp_path, remote, "Agents elsewhere")
    _edit(workspace)
    synced = await client.post("/api/workspace/sync")
    assert synced.json()["diverged"] is True, synced.text
    return workspace, remote, synced.json()["revision"]["id"].removeprefix("revision-")


@pytest.mark.anyio
async def test_using_the_shared_copy_keeps_this_computers_version(tmp_path: Path) -> None:
    workspace = tmp_path / "course"
    _course(workspace)
    _bare_remote(tmp_path, workspace)
    _git(workspace, "push", "--quiet", "origin", "main")

    async with _client(_app(workspace, tmp_path)) as client:
        workspace, remote, local_head = await _diverge(tmp_path, client)
        used = await client.post("/api/workspace/sync/use-shared-copy")

    assert used.json()["ok"] is True, used.text
    assert _git(workspace, "rev-parse", "main") == _git(remote, "rev-parse", "main")
    plan = read_course_plan(workspace)
    assert plan is not None and plan.title == "Agents elsewhere"
    kept = _git(workspace, "for-each-ref", "--format=%(objectname)", KEPT_REF_PREFIX).split()
    assert kept == [local_head]
    state = read_current_state(workspace)
    assert state.clean and state.drift == "clean"
    assert used.json()["status"]["ahead"] == 0 and used.json()["status"]["behind"] == 0


@pytest.mark.anyio
async def test_replacing_the_shared_copy_never_touches_release_tags(tmp_path: Path) -> None:
    workspace = tmp_path / "course"
    _course(workspace)
    _bare_remote(tmp_path, workspace)
    _git(workspace, "push", "--quiet", "origin", "main")
    remote = tmp_path / "remote.git"
    # A Release tag someone shared stays as it is.
    _git(remote, "update-ref", "refs/tags/course-release/kept-elsewhere", "main")

    async with _client(_app(workspace, tmp_path)) as client:
        workspace, remote, local_head = await _diverge(tmp_path, client)
        replaced = await client.post("/api/workspace/sync/replace-shared-copy")

    assert replaced.json()["ok"] is True, replaced.text
    assert _git(remote, "rev-parse", "main").strip() == local_head
    assert _git(remote, "tag", "--list").split() == ["course-release/kept-elsewhere"]


def test_replacing_the_shared_copy_is_refused_when_it_moved_since_the_check(
    tmp_path: Path,
) -> None:
    workspace = tmp_path / "course"
    _course(workspace)
    remote = _bare_remote(tmp_path, workspace)
    _git(workspace, "push", "--quiet", "origin", "main")
    _commit_elsewhere(tmp_path, remote, "Agents elsewhere")
    checked = check_remote(workspace).fetched_main
    _commit_elsewhere(tmp_path, remote, "Agents moved again")
    _edit(workspace)
    create_automatic_revision(workspace)
    backfill = backfill_release_files(workspace=workspace, release_data_root=tmp_path / "r")

    pushed = push_workspace(
        workspace, remote="the remote", backfill=backfill, replace_main_from=checked
    )

    assert pushed.ok is False
    assert "Agents moved again" in _git(remote, "log", "-1", "--format=%s", "main")


def test_the_background_check_never_prompts(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    workspace = tmp_path / "course"
    _course(workspace)
    _bare_remote(tmp_path, workspace)
    monkeypatch.setenv("SSH_ASKPASS", "/usr/bin/ssh-askpass")
    monkeypatch.setenv("GIT_ASKPASS", "/usr/bin/ssh-askpass")
    calls: list[tuple[list[str], dict[str, str]]] = []
    real_popen = subprocess.Popen

    def recording_popen(command, *args, **kwargs):
        calls.append((list(command), dict(kwargs["env"])))
        return real_popen(command, *args, **kwargs)

    monkeypatch.setattr(workspace_remote.subprocess, "Popen", recording_popen)
    check_remote(workspace)
    check_remote(workspace, interactive=True)

    remote_calls = [call for call in calls if "ls-remote" in call[0]]
    background, environment = remote_calls[0]
    assert "credential.interactive=false" in background
    assert "core.askPass=" in background
    assert environment["SSH_ASKPASS_REQUIRE"] == "never"
    assert environment["GCM_INTERACTIVE"] == "never"
    assert "SSH_ASKPASS" not in environment and "GIT_ASKPASS" not in environment
    sync_command, sync_environment = remote_calls[-1]
    assert "credential.interactive=false" not in sync_command
    assert sync_environment["GIT_ASKPASS"] == "/usr/bin/ssh-askpass"


@pytest.mark.anyio
async def test_releases_shared_from_elsewhere_count_and_their_ids_are_taken(
    tmp_path: Path,
) -> None:
    workspace = tmp_path / "course"
    _course(workspace)
    remote = _bare_remote(tmp_path, workspace)
    async with _client(_app(workspace, tmp_path)) as client:
        await client.post("/api/workspace/sync")
        selection = {"lecture_ids": [LECTURES[0].id], "artifact_ids": []}
        checked = await client.post(
            "/api/releases/validate", json={"selection": selection, "waivers": []}
        )
        waivers = [
            {"finding_id": finding["id"], "justification": "Worked examples."}
            for finding in checked.json()["findings"]
            if finding["severity"] == "warning"
        ]
        release = {"slug": "spring", "name": "Spring", "selection": selection, "waivers": waivers}
        first = await client.post("/api/releases", json=release)
        unshared = (await client.get("/api/workspace/sharing")).json()
        await client.post("/api/workspace/sync")
        # This computer forgets the Release, as if another computer had published it.
        _git(workspace, "tag", "-d", "course-release/spring")
        _git(workspace, "update-ref", "-d", "refs/course-harness/release-files/spring")
        forgotten = (await client.get("/api/workspace/sharing")).json()
        reused = await client.post("/api/releases", json=release)
        rechecked = (await client.post("/api/workspace/remote/check")).json()

    assert first.status_code == 201, first.text
    assert unshared["ahead"] == 1
    assert forgotten["release_count"] == 1
    assert reused.status_code == 409
    assert "already on the remote" in reused.json()["detail"]
    # The check fetches the Release tag back; its Revisions are no longer changes.
    assert _git(workspace, "tag", "--list").split() == ["course-release/spring"]
    assert rechecked["release_count"] == 1
    assert rechecked["changes_since_release"] == 0
    assert (rechecked["ahead"], rechecked["behind"]) == (0, 0)
    assert remote.exists()


def test_a_release_whose_files_were_left_out_is_not_counted_forever(tmp_path: Path) -> None:
    workspace = tmp_path / "course"
    _course(workspace)
    _bare_remote(tmp_path, workspace)
    _git(workspace, "push", "--quiet", "origin", "main")
    tree = _git(workspace, "mktree", input_text="").strip()
    commit = _git(workspace, "commit-tree", tree, "-m", "files").strip()
    _git(workspace, "update-ref", "refs/course-harness/release-files/broken", commit)
    _git(workspace, "update-ref", "refs/tags/course-release/broken", "main")
    _git(workspace, "push", "--quiet", "origin", "refs/tags/course-release/broken")
    check_remote(workspace)
    assert sharing_status(workspace).ahead == 1

    workspace_remote._remember_skipped_files(workspace, ["broken"])

    assert sharing_status(workspace).ahead == 0
    assert sharing_status(workspace).skipped_files == ["broken"]


async def _publish(client: httpx2.AsyncClient, slug: str) -> httpx2.Response:
    selection = {"lecture_ids": [LECTURES[0].id], "artifact_ids": []}
    checked = await client.post(
        "/api/releases/validate", json={"selection": selection, "waivers": []}
    )
    waivers = [
        {"finding_id": finding["id"], "justification": "Worked examples."}
        for finding in checked.json()["findings"]
        if finding["severity"] == "warning"
    ]
    return await client.post(
        "/api/releases",
        json={"slug": slug, "name": slug.title(), "selection": selection, "waivers": waivers},
    )


@pytest.mark.anyio
async def test_a_manifest_from_a_newer_harness_names_the_upgrade(tmp_path: Path) -> None:
    workspace = tmp_path / "course"
    _course(workspace)
    async with _client(_app(workspace, tmp_path)) as client:
        assert (await _publish(client, "today")).status_code == 201
    release = read_release(workspace, "today")
    assert release is not None
    manifest = release.model_dump(mode="json") | {
        "slug": "future",
        "tag": "course-release/future",
        "from_the_future": True,
    }
    release_service._create_annotated_tag(
        workspace, "course-release/future", release.commit_oid, canonical_json(manifest)
    )

    with pytest.raises(ReleaseError, match="newer Course Harness"):
        read_release(workspace, "future")
