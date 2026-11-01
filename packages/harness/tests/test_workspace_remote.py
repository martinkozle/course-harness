"""Pushing a Course, its Releases and their files to a Git remote, then reading a mirror of it."""

import shutil
import subprocess
from pathlib import Path

import httpx2
import pytest
from pptx import Presentation as PPTXPresentation

from course_harness import release_service
from course_harness.app import create_app
from course_harness.core import releases
from course_harness.core.course_plan import CoursePlan, Lecture, write_course_plan
from course_harness.core.presentation import Presentation, TitleSlide, write_presentation
from course_harness.core.quiz import Quiz, QuizOption, QuizQuestion, write_quiz
from course_harness.core.release_files import (
    read_release_artifact_file,
    read_release_files,
    release_files_commit,
    release_files_ref,
)
from course_harness.core.release_validation import ReleaseSelection, Waiver
from course_harness.core.releases import ReleaseError, sha256_hex
from course_harness.core.template_profiles import TemplateLayoutMapping, TemplateProfile
from course_harness.release_service import (
    PublishReleaseRequest,
    backfill_release_files,
    publish_release,
    read_release_artifact,
)
from course_harness.template_profiles import profile_dir, write_profile_version
from course_harness.workspace_history import (
    RevisionCreateRequest,
    create_revision,
    record_app_authored_state,
)
from course_harness.workspace_remote import (
    InvalidRemoteUrl,
    PushResult,
    push_workspace,
    read_remote,
    redact,
    set_remote,
    validate_remote_url,
)

LECTURE_ID = "lecture-aaaaaaaaaaaa"
PRESENTATION_ID = "presentation-aaaaaaaaaaaa"
UNCITED_QUESTION = f"grounding.uncited-question:{LECTURE_ID}:question-aaaaaaaaaaaa"


def _git(repository: Path, *arguments: str, input_data: bytes | None = None) -> bytes:
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
        input=input_data,
        check=True,
        capture_output=True,
    ).stdout


def _custom_template(templates: Path) -> TemplateProfile:
    profile = TemplateProfile(
        id="tpl-aaaaaaaaaaaa",
        name="Institutional",
        version=1,
        template_filename="institutional.pptx",
        slide_width=12_192_000,
        slide_height=6_858_000,
        slide_count=11,
        layouts=[
            TemplateLayoutMapping(
                semantic_layout="title",
                template_layout_index=0,
                confidence=1,
                rationale="Course Author mapping",
            )
        ],
    )
    write_profile_version(templates, profile)
    PPTXPresentation().save(str(profile_dir(templates, profile.id) / "template.pptx"))
    return profile


def _quiz() -> Quiz:
    return Quiz(
        lecture_id=LECTURE_ID,
        questions=[
            QuizQuestion(
                id="question-aaaaaaaaaaaa",
                prompt="What does randomisation remove?",
                options=[
                    QuizOption(id="option-aaaaaaaaaaaa", text="Confounding", correct=True),
                    QuizOption(
                        id="option-bbbbbbbbbbbb",
                        text="Sampling error",
                        explanation="Randomisation does not remove sampling error.",
                    ),
                ],
            )
        ],
    )


def _clean_course(workspace: Path, *, template: TemplateProfile | None = None) -> None:
    workspace.mkdir()
    _git(workspace, "init", "--quiet", "--initial-branch=main")
    write_course_plan(
        workspace,
        CoursePlan(
            id="course-aaaaaaaaaaaa",
            title="Causal inference",
            audience="Graduate students",
            template_profile_id=template.id if template else None,
            template_profile_version=template.version if template else None,
            lectures=[Lecture(id=LECTURE_ID, title="Foundations", presentation_id=PRESENTATION_ID)],
        ),
    )
    write_presentation(
        workspace,
        Presentation(
            id=PRESENTATION_ID,
            lecture_id=LECTURE_ID,
            slides=[TitleSlide(id="slide-aaaaaaaaaaaa", title="Foundations")],
        ),
    )
    write_quiz(workspace, _quiz())
    create_revision(workspace, RevisionCreateRequest(summary="Initial Course"))
    record_app_authored_state(workspace)


def _publish(tmp_path: Path, workspace: Path, slug: str = "fall-2026") -> releases.CourseRelease:
    return publish_release(
        workspace=workspace,
        release_data_root=tmp_path / "releases",
        templates_data_root=tmp_path / "templates",
        evidence_line_counts={},
        request=PublishReleaseRequest(
            slug=slug,
            name="Fall 2026",
            selection=ReleaseSelection(lecture_ids=[LECTURE_ID], artifact_ids=[PRESENTATION_ID]),
            waivers=[Waiver(finding_id=UNCITED_QUESTION, justification="A worked example.")],
        ),
    )


def _bare_remote(tmp_path: Path) -> Path:
    remote = tmp_path / "remote.git"
    _git(tmp_path, "init", "--quiet", "--bare", str(remote))
    return remote


def _push(workspace: Path, remote: Path, tmp_path: Path) -> None:
    # Local paths are not accepted through the settings, as through HTTP; Git itself takes them.
    _git(workspace, "remote", "add", "origin", str(remote))
    result = _push_now(workspace, tmp_path)
    assert result.ok, result.message


def _push_now(workspace: Path, tmp_path: Path) -> PushResult:
    backfill = backfill_release_files(workspace=workspace, release_data_root=tmp_path / "releases")
    return push_workspace(workspace, remote="the remote", backfill=backfill)


def test_publishing_never_fails_because_its_files_cannot_be_recorded_in_git(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    workspace = tmp_path / "course"
    _clean_course(workspace)

    def fail(*_arguments: object, **_keywords: object) -> str:
        raise ReleaseError("Release files could not be recorded in Git")

    monkeypatch.setattr(release_service, "_write_release_files_commit", fail)
    release = _publish(tmp_path, workspace)
    monkeypatch.undo()

    assert releases.read_release(workspace, release.slug) == release
    assert release_files_commit(workspace, release.slug) is None
    backfill = backfill_release_files(workspace=workspace, release_data_root=tmp_path / "releases")
    assert backfill.written == [release.slug]


def test_publish_records_exactly_the_release_files_in_a_ref_without_touching_the_worktree(
    tmp_path: Path,
) -> None:
    workspace = tmp_path / "course"
    profile = _custom_template(tmp_path / "templates")
    _clean_course(workspace, template=profile)
    status_before = _git(workspace, "status", "--porcelain", "--ignored")

    release = _publish(tmp_path, workspace)

    commit = release_files_commit(workspace, release.slug)
    assert commit is not None
    listing = _git(workspace, "ls-tree", "-r", "--name-only", commit).decode().split()
    assert listing == [f"artifacts/{PRESENTATION_ID}.pptx", "inputs/template.pptx"]
    assert _git(workspace, "status", "--porcelain", "--ignored") == status_before
    assert _git(workspace, "rev-parse", "HEAD").decode().strip() == release.commit_oid
    files = read_release_files(workspace, release)
    assert files.template is not None
    assert sha256_hex(files.template) == release.template_profile.template_sha256
    assert sha256_hex(files.artifacts[PRESENTATION_ID]) == release.artifacts[0].sha256


def test_push_then_a_mirror_reads_and_verifies_manifest_quizzes_and_files(tmp_path: Path) -> None:
    workspace = tmp_path / "course"
    profile = _custom_template(tmp_path / "templates")
    _clean_course(workspace, template=profile)
    release = _publish(tmp_path, workspace)
    remote = _bare_remote(tmp_path)

    _push(workspace, remote, tmp_path)
    mirror = tmp_path / "mirror.git"
    _git(tmp_path, "clone", "--quiet", "--mirror", str(remote), str(mirror))

    assert releases.read_release(mirror, release.slug) == release
    assert release.quizzes[0].lecture_id == LECTURE_ID
    assert releases.tagged_quiz(mirror, release.commit_oid, LECTURE_ID) == _quiz()
    files = read_release_files(mirror, release)
    assert files.template is not None
    assert sha256_hex(files.template) == release.template_profile.template_sha256
    artifact = read_release_artifact_file(mirror, release, PRESENTATION_ID)
    assert artifact[:2] == b"PK"
    assert len(artifact) == release.artifacts[0].size
    assert sha256_hex(artifact) == release.artifacts[0].sha256
    # The binaries travel in their own ref, never in the Course branch.
    branch_files = _git(mirror, "ls-tree", "-r", "--name-only", "main").decode().split()
    assert not any(path.endswith(".pptx") for path in branch_files)

    # Pushing again changes nothing.
    result = _push_now(workspace, tmp_path)
    assert result.ok
    assert result.message == "the remote is already up to date."


def _replace_files_ref(mirror: Path, slug: str, files: dict[str, bytes]) -> None:
    """Point a Release's files ref at a crafted commit holding the given files."""
    directories: dict[str, list[str]] = {}
    for path, content in sorted(files.items()):
        directory, name = path.split("/", 1)
        blob = _git(mirror, "hash-object", "-w", "--stdin", input_data=content).decode().strip()
        directories.setdefault(directory, []).append(f"100644 blob {blob}\t{name}")
    entries = []
    for directory, lines in sorted(directories.items()):
        tree = _git(mirror, "mktree", input_data="\n".join(lines).encode() + b"\n").decode()
        entries.append(f"040000 tree {tree.strip()}\t{directory}")
    root = _git(mirror, "mktree", input_data="\n".join(entries).encode() + b"\n").decode().strip()
    commit = _git(mirror, "commit-tree", root, "-m", "Crafted").decode().strip()
    _git(mirror, "update-ref", release_files_ref(slug), commit)


def test_a_tampered_missing_or_extra_release_file_is_rejected(tmp_path: Path) -> None:
    workspace = tmp_path / "course"
    _clean_course(workspace)
    release = _publish(tmp_path, workspace)
    remote = _bare_remote(tmp_path)
    _push(workspace, remote, tmp_path)
    mirror = tmp_path / "mirror.git"
    _git(tmp_path, "clone", "--quiet", "--mirror", str(remote), str(mirror))
    original = read_release_artifact_file(mirror, release, PRESENTATION_ID)
    artifact_path = f"artifacts/{PRESENTATION_ID}.pptx"

    tampered = bytearray(original)
    tampered[-1] ^= 0xFF
    _replace_files_ref(mirror, release.slug, {artifact_path: bytes(tampered)})
    with pytest.raises(ReleaseError, match="does not match its manifest"):
        read_release_files(mirror, release)

    _replace_files_ref(mirror, release.slug, {artifact_path: original + b"x"})
    with pytest.raises(ReleaseError, match="does not match its manifest"):
        read_release_artifact_file(mirror, release, PRESENTATION_ID)

    _replace_files_ref(mirror, release.slug, {artifact_path: original, "inputs/extra.pptx": b"x"})
    with pytest.raises(ReleaseError, match="does not name"):
        read_release_files(mirror, release)

    _replace_files_ref(mirror, release.slug, {"inputs/template.pptx": b"x"})
    with pytest.raises(ReleaseError, match="does not name"):
        read_release_files(mirror, release)

    _git(mirror, "update-ref", "-d", release_files_ref(release.slug))
    with pytest.raises(ReleaseError, match="files are missing"):
        read_release_files(mirror, release)


def test_backfill_records_files_of_a_release_published_before_files_refs(tmp_path: Path) -> None:
    workspace = tmp_path / "course"
    _clean_course(workspace)
    release = _publish(tmp_path, workspace)
    first_commit = release_files_commit(workspace, release.slug)
    # A Release published before this change has no files ref.
    _git(workspace, "update-ref", "-d", release_files_ref(release.slug))
    assert release_files_commit(workspace, release.slug) is None

    backfill = backfill_release_files(workspace=workspace, release_data_root=tmp_path / "releases")

    assert backfill.written == [release.slug]
    assert backfill.missing == []
    # The files commit depends only on the manifest and bytes, so it is the same commit again.
    assert release_files_commit(workspace, release.slug) == first_commit
    assert read_release_files(workspace, release).artifacts[PRESENTATION_ID][:2] == b"PK"
    again = backfill_release_files(workspace=workspace, release_data_root=tmp_path / "releases")
    assert again.written == []


def test_backfill_reports_a_release_without_files_anywhere(tmp_path: Path) -> None:
    workspace = tmp_path / "course"
    _clean_course(workspace)
    release = _publish(tmp_path, workspace)
    _git(workspace, "update-ref", "-d", release_files_ref(release.slug))
    shutil.rmtree(tmp_path / "releases")

    backfill = backfill_release_files(workspace=workspace, release_data_root=tmp_path / "releases")

    assert backfill.written == []
    assert backfill.missing == [release.slug]


def test_backfill_replaces_a_wrong_ref_from_local_release_data(tmp_path: Path) -> None:
    workspace = tmp_path / "course"
    _clean_course(workspace)
    release = _publish(tmp_path, workspace)
    good = release_files_commit(workspace, release.slug)
    _replace_files_ref(workspace, release.slug, {f"artifacts/{PRESENTATION_ID}.pptx": b"x"})

    backfill = backfill_release_files(workspace=workspace, release_data_root=tmp_path / "releases")

    assert backfill.written == [release.slug]
    assert backfill.ready == [release.slug]
    assert release_files_commit(workspace, release.slug) == good


def test_a_bad_files_ref_without_local_data_is_skipped_and_the_rest_is_pushed(
    tmp_path: Path,
) -> None:
    workspace = tmp_path / "course"
    _clean_course(workspace)
    good = _publish(tmp_path, workspace, "good")
    bad = _publish(tmp_path, workspace, "bad")
    _replace_files_ref(workspace, bad.slug, {f"artifacts/{PRESENTATION_ID}.pptx": b"x"})
    shutil.rmtree(tmp_path / "releases" / bad.course_id / bad.slug)
    remote = _bare_remote(tmp_path)
    _git(workspace, "remote", "add", "origin", str(remote))

    result = _push_now(workspace, tmp_path)

    assert result.ok, result.message
    assert result.releases_with_invalid_files == [bad.slug]
    refs = _git(remote, "for-each-ref", "--format=%(refname)").decode().split()
    assert release_files_ref(good.slug) in refs
    assert release_files_ref(bad.slug) not in refs
    assert f"refs/tags/{bad.tag}" in refs


def test_a_clone_serves_release_artifacts_from_git_without_release_data(tmp_path: Path) -> None:
    workspace = tmp_path / "course"
    _clean_course(workspace)
    release = _publish(tmp_path, workspace)
    original = read_release_artifact(
        workspace=workspace,
        release_data_root=tmp_path / "releases",
        slug=release.slug,
        artifact_id=PRESENTATION_ID,
    )
    shutil.rmtree(tmp_path / "releases")

    assert (
        read_release_artifact(
            workspace=workspace,
            release_data_root=tmp_path / "releases",
            slug=release.slug,
            artifact_id=PRESENTATION_ID,
        )
        == original
    )


@pytest.mark.parametrize(
    "url",
    [
        "https://github.com/example/course.git",
        "https://gitlab.example.edu/group/sub/course",
        "https://author@github.com/example/course.git",
        "ssh://git@github.com/example/course.git",
        "ssh://git@host.example:2222/course.git",
        "git@github.com:example/course.git",
        "github.com:example/course.git",
    ],
)
def test_https_and_ssh_remote_urls_are_accepted(url: str) -> None:
    assert validate_remote_url(f"  {url} ") == url


@pytest.mark.parametrize(
    "url",
    [
        "",
        "/home/author/course.git",
        "../course.git",
        "file:///home/author/course.git",
        "http://github.com/example/course.git",
        "git://github.com/example/course.git",
        "ext::sh -c touch% /tmp/pwned",
        "https://author:token@github.com/example/course.git",
        "https://github.com",
        "https://github.com/example/course.git?x=1",
        "-uhttps://github.com/example/course.git",
        "git@github.com:example/course .git",
        "git@-oProxyCommand=x:course.git",
        "https://" + "a" * 3000 + ".example/course.git",
    ],
)
def test_other_remote_urls_are_refused(url: str) -> None:
    with pytest.raises(InvalidRemoteUrl):
        validate_remote_url(url)


def test_remote_settings_are_an_ordinary_git_remote(tmp_path: Path) -> None:
    workspace = tmp_path / "course"
    _clean_course(workspace)
    assert read_remote(workspace).url is None

    set_remote(workspace, "git@github.com:example/course.git")
    set_remote(workspace, "https://github.com/example/course.git")

    assert read_remote(workspace).url == "https://github.com/example/course.git"
    assert _git(workspace, "remote", "get-url", "origin").strip() == (
        b"https://github.com/example/course.git"
    )
    assert set_remote(workspace, None).url is None
    assert _git(workspace, "remote").strip() == b""


def test_a_remote_is_shown_as_stored_except_a_password_set_outside_the_app(
    tmp_path: Path,
) -> None:
    workspace = tmp_path / "course"
    _clean_course(workspace)
    set_remote(workspace, "https://author@github.com/example/course.git")
    assert read_remote(workspace).url == "https://author@github.com/example/course.git"

    _git(workspace, "remote", "set-url", "origin", "https://author:secret@github.com/x/y.git")
    shown = read_remote(workspace).url
    assert shown == "https://author:***@github.com/x/y.git"
    # The shown value can never be saved back over the stored one.
    with pytest.raises(InvalidRemoteUrl):
        set_remote(workspace, shown)
    assert _git(workspace, "remote", "get-url", "origin").strip() == (
        b"https://author:secret@github.com/x/y.git"
    )


def test_a_local_path_is_refused_with_how_to_add_it_in_a_terminal() -> None:
    with pytest.raises(InvalidRemoteUrl, match="git remote add origin <path>"):
        validate_remote_url("/home/author/course.git")
    with pytest.raises(InvalidRemoteUrl, match="git remote add origin <path>"):
        validate_remote_url("file:///home/author/course.git")


def test_credentials_in_git_output_are_redacted() -> None:
    assert (
        redact("fatal: unable to access 'https://user:ghp_secret@github.com/x.git/'")
        == "fatal: unable to access 'https://***@github.com/x.git/'"
    )


@pytest.mark.anyio
async def test_remote_http_sets_the_remote_and_pushes_only_on_request(tmp_path: Path) -> None:
    workspace = tmp_path / "course"
    _clean_course(workspace)
    app = create_app(
        workspace,
        release_data_path=tmp_path / "releases",
        templates_data_path=tmp_path / "templates",
    )
    remote = _bare_remote(tmp_path)
    transport = httpx2.ASGITransport(app=app)
    async with httpx2.AsyncClient(transport=transport, base_url="http://test") as client:
        unset = await client.get("/api/workspace/remote")
        without_remote = await client.post("/api/workspace/remote/push")
        refused = await client.put("/api/workspace/remote", json={"url": str(remote)})
        saved = await client.put(
            "/api/workspace/remote", json={"url": "https://github.com/example/course.git"}
        )
        published = await client.post(
            "/api/releases",
            json={
                "slug": "fall-2026",
                "name": "Fall 2026",
                "selection": {"lecture_ids": [LECTURE_ID], "artifact_ids": [PRESENTATION_ID]},
                "waivers": [{"finding_id": UNCITED_QUESTION, "justification": "An example."}],
            },
        )
        # Nothing is pushed automatically when a Release is published.
        assert _git(remote, "for-each-ref").strip() == b""
        # A local bare repository stands in for the remote the Course Author configured.
        _git(workspace, "remote", "set-url", "origin", str(remote))
        _git(workspace, "update-ref", "-d", release_files_ref("fall-2026"))
        pushed = await client.post("/api/workspace/remote/push")

    assert unset.status_code == 200
    assert unset.json()["url"] is None
    assert "git push origin main" in unset.json()["push_command"]
    assert without_remote.status_code == 409
    assert refused.status_code == 422
    assert saved.status_code == 200
    assert saved.json()["url"] == "https://github.com/example/course.git"
    assert published.status_code == 201
    assert pushed.status_code == 200, pushed.text
    assert pushed.json()["ok"] is True
    assert pushed.json()["releases_without_files"] == []
    refs = _git(remote, "for-each-ref", "--format=%(refname)").decode().split()
    assert refs == [
        release_files_ref("fall-2026"),
        "refs/heads/main",
        "refs/tags/course-release/fall-2026",
    ]


@pytest.mark.anyio
async def test_a_failed_push_reports_git_output(tmp_path: Path) -> None:
    workspace = tmp_path / "course"
    _clean_course(workspace)
    _git(workspace, "remote", "add", "origin", str(tmp_path / "missing.git"))
    app = create_app(
        workspace,
        release_data_path=tmp_path / "releases",
        templates_data_path=tmp_path / "templates",
    )
    transport = httpx2.ASGITransport(app=app)
    async with httpx2.AsyncClient(transport=transport, base_url="http://test") as client:
        pushed = await client.post("/api/workspace/remote/push")

    assert pushed.status_code == 200
    assert pushed.json()["ok"] is False
    assert "missing.git" in pushed.json()["message"]
