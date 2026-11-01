"""Reading Releases from crafted bare mirrors that Course Harness Classroom does not control."""

import json
import os
import subprocess
from collections.abc import Sequence
from pathlib import Path

import pytest
import yaml

from course_harness.core import releases
from course_harness.core.canonical_yaml import load_canonical_yaml
from course_harness.core.course_plan import CoursePlan, Lecture, write_course_plan
from course_harness.core.presentation import Presentation, TitleSlide, write_presentation
from course_harness.core.release_validation import ReleaseSelection
from course_harness.core.releases import CourseRelease, RejectedTag, ReleaseError
from course_harness.release_service import PublishReleaseRequest, publish_release
from course_harness.workspace_history import (
    RevisionCreateRequest,
    create_revision,
    record_app_authored_state,
)

LECTURE_ID = "lecture-aaaaaaaaaaaa"
PRESENTATION_ID = "presentation-aaaaaaaaaaaa"


def _git(
    repository: Path,
    *arguments: str | bytes,
    input_data: bytes | None = None,
    date: str | None = None,
) -> bytes:
    environment = {"GIT_COMMITTER_DATE": date} if date else {}
    return subprocess.run(
        [
            "git",
            "-c",
            "tag.gpgSign=false",
            "-c",
            "user.name=Course Harness tests",
            "-c",
            "user.email=course-harness-tests@example.invalid",
            *arguments,
        ],
        cwd=repository,
        input=input_data,
        env={**os.environ, **environment},
        check=True,
        capture_output=True,
    ).stdout


def _published_mirror(tmp_path: Path, slugs: Sequence[str]) -> tuple[Path, list[CourseRelease]]:
    """Publish Releases from a clean Course Workspace and mirror it as a bare repository."""
    workspace = tmp_path / "course"
    workspace.mkdir()
    _git(workspace, "init", "--quiet", "--initial-branch=main")
    write_course_plan(
        workspace,
        CoursePlan(
            id="course-aaaaaaaaaaaa",
            title="Causal inference",
            audience="Graduate students",
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
    create_revision(workspace, RevisionCreateRequest(summary="Initial Course"))
    record_app_authored_state(workspace)
    published = [
        publish_release(
            workspace=workspace,
            release_data_root=tmp_path / "releases",
            templates_data_root=tmp_path / "templates",
            evidence_line_counts={},
            request=PublishReleaseRequest(
                slug=slug,
                name=slug,
                selection=ReleaseSelection(
                    lecture_ids=[LECTURE_ID], artifact_ids=[PRESENTATION_ID]
                ),
            ),
        )
        for slug in slugs
    ]
    mirror = tmp_path / "mirror.git"
    _git(tmp_path, "clone", "--quiet", "--mirror", str(workspace), str(mirror))
    return mirror, published


def _manifest(release: CourseRelease, **changes: str) -> bytes:
    payload = release.model_dump(mode="json")
    slug = changes.get("slug", release.slug)
    payload.update(name=slug, tag=f"course-release/{slug}", **changes)
    return (
        json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()
        + b"\n"
    )


def _tag(mirror: Path, slug: str, commit_oid: str, message: bytes, *, date: str) -> None:
    """Create or replace an annotated Release tag with a chosen creation date."""
    if _git(mirror, "tag", "--list", f"course-release/{slug}").strip():
        _git(mirror, "tag", "--delete", f"course-release/{slug}")
    _git(
        mirror,
        "tag",
        "--annotate",
        "--cleanup=verbatim",
        "--file=-",
        f"course-release/{slug}",
        commit_oid,
        input_data=message,
        date=date,
    )


def _commit_replacing(mirror: Path, commit_oid: str, path: str, content: bytes) -> str:
    blob = _git(mirror, "hash-object", "-w", "--stdin", input_data=content).decode().strip()
    entries = [
        line
        for line in _git(mirror, "ls-tree", commit_oid).decode().splitlines()
        if line.split("\t", 1)[1] != path
    ]
    entries.append(f"100644 blob {blob}\t{path}")
    tree = _git(mirror, "mktree", input_data="\n".join(entries).encode() + b"\n").decode().strip()
    return _git(mirror, "commit-tree", tree, "-p", commit_oid, "-m", "Crafted").decode().strip()


def test_a_tag_name_with_non_utf8_bytes_is_rejected_without_hiding_valid_releases(
    tmp_path: Path,
) -> None:
    mirror, [release] = _published_mirror(tmp_path, ["fall-2026"])
    tag_oid = _git(mirror, "rev-parse", "refs/tags/course-release/fall-2026").strip()
    _git(mirror, "update-ref", b"refs/tags/course-release/caf\xe9", tag_oid)

    scan = releases.scan_releases(mirror)
    assert scan.releases == [release]
    assert scan.rejected == [
        RejectedTag(
            name="course-release/caf\\xe9", reason="Repository contains an unsafe Release tag"
        )
    ]
    assert releases.newest_release(mirror).newest == release
    with pytest.raises(ReleaseError, match="unsafe Release tag"):
        releases.list_releases(mirror)


def test_an_oversized_blob_is_rejected_by_its_size_before_it_is_read(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    mirror, [release] = _published_mirror(tmp_path, ["fall-2026"])
    calls: list[tuple[str, ...]] = []
    run_git = releases.run_git

    def recording_run_git(
        repository: Path,
        *arguments: str,
        input_data: bytes | None = None,
        max_output_bytes: int = releases.MAX_GIT_OUTPUT_BYTES,
    ) -> bytes:
        calls.append(arguments)
        return run_git(
            repository, *arguments, input_data=input_data, max_output_bytes=max_output_bytes
        )

    monkeypatch.setattr(releases, "run_git", recording_run_git)
    monkeypatch.setattr(releases, "MAX_CANONICAL_BLOB_BYTES", 16)

    with pytest.raises(ReleaseError, match="Repository object is too large"):
        releases.read_release(mirror, release.slug)
    assert ("cat-file", "--batch-check") in calls
    assert not any(call[:2] == ("cat-file", "blob") for call in calls)
    assert "core.bigFileThreshold=1m" in releases.git_command(mirror, "status")


def test_a_read_budget_accumulates_across_reads_and_nested_budgets_charge_the_outer_one(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    mirror, [release] = _published_mirror(tmp_path, ["fall-2026"])
    size = len(releases.read_commit_blob(mirror, release.commit_oid, "course.yaml"))

    # Each read fits the budget on its own; the second one no longer fits what is left.
    with releases.read_budget(seconds=30, max_bytes=size + size // 2):
        releases.read_commit_blob(mirror, release.commit_oid, "course.yaml")
        with pytest.raises(ReleaseError, match="read budget"):
            releases.read_commit_blob(mirror, release.commit_oid, "course.yaml")

    with releases.read_budget(seconds=30, max_bytes=10 * size) as outer:
        with releases.read_budget(seconds=30, max_bytes=10 * size) as inner:
            releases.read_commit_blob(mirror, release.commit_oid, "course.yaml")
        assert outer.remaining_bytes == inner.remaining_bytes < 10 * size - size

    monkeypatch.setattr(releases, "RELEASE_READ_SECONDS", 0)
    with pytest.raises(ReleaseError, match="timed out|time budget"):
        releases.read_release(mirror, release.slug)


def test_release_tags_that_share_a_commit_parse_its_tagged_files_once(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    mirror, published = _published_mirror(tmp_path, ["fall-2026", "spring-2027", "summer-2027"])
    assert len({release.commit_oid for release in published}) == 1
    parsed: list[str] = []
    parse_tagged_plan = releases.parse_tagged_plan

    def recording_parse(repository: Path, commit_oid: str) -> CoursePlan:
        parsed.append(commit_oid)
        return parse_tagged_plan(repository, commit_oid)

    monkeypatch.setattr(releases, "parse_tagged_plan", recording_parse)

    assert len(releases.scan_releases(mirror).releases) == 3
    assert parsed == [published[0].commit_oid]


def test_newest_release_tries_a_bounded_number_of_candidate_tags(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    mirror, [release] = _published_mirror(tmp_path, ["fall-2026"])
    _tag(
        mirror,
        "fall-2026",
        release.commit_oid,
        _manifest(release),
        date="2026-09-01T00:00:00+00:00",
    )
    for month in range(1, 5):
        _tag(
            mirror,
            f"bad-{month}",
            release.commit_oid,
            b"{}\n",
            date=f"2027-0{month}-01T00:00:00+00:00",
        )
    monkeypatch.setattr(releases, "MAX_RELEASE_CANDIDATES", 3)

    scan = releases.newest_release(mirror)

    assert scan.releases == []
    assert [tag.name for tag in scan.rejected] == [
        "course-release/bad-4",
        "course-release/bad-3",
        "course-release/bad-2",
    ]
    monkeypatch.setattr(releases, "MAX_RELEASE_CANDIDATES", 5)
    assert releases.newest_release(mirror).newest == release

    monkeypatch.setattr(releases, "RELEASE_SCAN_SECONDS", 0)
    with pytest.raises(ReleaseError, match="timed out|time budget"):
        releases.newest_release(mirror)


def test_deeply_nested_yaml_in_a_tagged_revision_is_a_release_error(tmp_path: Path) -> None:
    mirror, [release] = _published_mirror(tmp_path, ["fall-2026"])
    commit_oid = _commit_replacing(
        mirror, release.commit_oid, "course.yaml", b"a: " + b"[" * 200_000 + b"\n"
    )
    _tag(
        mirror,
        "deep",
        commit_oid,
        _manifest(
            release, slug="deep", commit_oid=commit_oid, revision_id=f"revision-{commit_oid}"
        ),
        date="2030-01-01T00:00:00+00:00",
    )

    with pytest.raises(ReleaseError, match="Tagged Course Plan is invalid") as raised:
        releases.read_release(mirror, "deep")
    assert isinstance(raised.value.__cause__, yaml.YAMLError)
    assert releases.newest_release(mirror).newest == release
    with pytest.raises(yaml.YAMLError, match="nested too deeply"):
        load_canonical_yaml("a: " + "[" * 65 + "]" * 65 + "\n")
    assert load_canonical_yaml("a: " + "[" * 60 + "]" * 60 + "\n") is not None


def test_hostile_repository_configuration_and_attributes_run_nothing(tmp_path: Path) -> None:
    mirror, [release] = _published_mirror(tmp_path, ["fall-2026"])
    marker = tmp_path / "ran"
    command = f"touch {marker}"
    with (mirror / "config").open("a", encoding="utf-8") as config:
        config.write(
            "[core]\n"
            f"\tfsmonitor = {command}\n"
            f"\thooksPath = {tmp_path}\n"
            f"\tpager = {command}\n"
            "\tbigFileThreshold = 1g\n"
            '[filter "evil"]\n'
            f"\tsmudge = {command}\n"
            f"\tclean = {command}\n"
            '[diff "evil"]\n'
            f"\ttextconv = {command}\n"
        )
    (mirror / "info").mkdir(exist_ok=True)
    (mirror / "info" / "attributes").write_text("* filter=evil diff=evil\n", encoding="utf-8")
    commit_oid = _commit_replacing(
        mirror, release.commit_oid, ".gitattributes", b"* filter=evil diff=evil\n"
    )
    _tag(
        mirror,
        "attributes",
        commit_oid,
        _manifest(
            release, slug="attributes", commit_oid=commit_oid, revision_id=f"revision-{commit_oid}"
        ),
        date="2030-01-01T00:00:00+00:00",
    )

    scan = releases.scan_releases(mirror)
    assert {item.slug for item in scan.releases} == {"fall-2026", "attributes"}
    assert scan.rejected == []
    assert not marker.exists()


def test_a_repository_that_borrows_objects_through_alternates_is_refused(tmp_path: Path) -> None:
    mirror, _published = _published_mirror(tmp_path, ["fall-2026"])
    borrower = tmp_path / "borrower.git"
    _git(tmp_path, "init", "--quiet", "--bare", str(borrower))
    (borrower / "objects" / "info" / "alternates").write_text(
        f"{mirror / 'objects'}\n", encoding="utf-8"
    )

    with pytest.raises(ReleaseError, match="borrows objects"):
        releases.newest_release(borrower)
    with pytest.raises(ReleaseError, match="borrows objects"):
        releases.read_release(borrower, "fall-2026")


def test_canonical_yaml_rejects_an_alias_bomb_in_a_tagged_revision(tmp_path: Path) -> None:
    mirror, [release] = _published_mirror(tmp_path, ["fall-2026"])
    names = "abcdefghi"
    bomb = [f"{names[0]}: &{names[0]} [{', '.join(['lol'] * 9)}]"]
    for previous, name in zip(names, names[1:], strict=False):
        bomb.append(f"{name}: &{name} [{', '.join([f'*{previous}'] * 9)}]")
    commit_oid = _commit_replacing(
        mirror, release.commit_oid, "course.yaml", "\n".join(bomb).encode() + b"\n"
    )
    _tag(
        mirror,
        "bomb",
        commit_oid,
        _manifest(
            release, slug="bomb", commit_oid=commit_oid, revision_id=f"revision-{commit_oid}"
        ),
        date="2030-01-01T00:00:00+00:00",
    )

    with pytest.raises(ReleaseError, match="Tagged Course Plan is invalid") as raised:
        releases.read_release(mirror, "bomb")
    assert isinstance(raised.value.__cause__, yaml.YAMLError)
    scan = releases.newest_release(mirror)
    assert scan.newest == release
    assert scan.rejected == [
        RejectedTag(name="course-release/bomb", reason="Tagged Course Plan is invalid")
    ]

    assert load_canonical_yaml("title: Plain\nlectures: [one, two]\n") == {
        "title": "Plain",
        "lectures": ["one", "two"],
    }
    with pytest.raises(yaml.YAMLError, match="anchors or aliases"):
        load_canonical_yaml("first: &shared plain\nsecond: other\n")


def test_the_newest_valid_release_skips_an_invalid_newer_tag_and_stops_at_the_first_valid(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    mirror, [fall, spring] = _published_mirror(tmp_path, ["fall-2026", "spring-2027"])
    _tag(mirror, "fall-2026", fall.commit_oid, _manifest(fall), date="2026-09-01T00:00:00+00:00")
    _tag(
        mirror,
        "spring-2027",
        spring.commit_oid,
        _manifest(spring),
        date="2027-01-01T00:00:00+00:00",
    )
    _tag(mirror, "summer-2027", fall.commit_oid, b"{}\n", date="2027-06-01T00:00:00+00:00")

    read: list[str] = []
    read_release = releases.read_release

    def recording_read_release(repository: Path, slug: str) -> CourseRelease | None:
        read.append(slug)
        return read_release(repository, slug)

    monkeypatch.setattr(releases, "read_release", recording_read_release)
    scan = releases.newest_release(mirror)

    assert scan.releases == [spring]
    assert [tag.name for tag in scan.rejected] == ["course-release/summer-2027"]
    assert scan.rejected[0].reason == "Release tag does not contain a valid manifest"
    assert read == ["summer-2027", "spring-2027"]


def test_a_path_that_is_not_a_repository_never_reads_a_parent_repository(tmp_path: Path) -> None:
    _published_mirror(tmp_path, ["fall-2026"])
    workspace = tmp_path / "course"
    assert releases.list_releases(workspace) != []

    plain = workspace / "plain"
    plain.mkdir()
    with pytest.raises(ReleaseError, match="Git operation failed"):
        releases.list_releases(plain)
    with pytest.raises(ReleaseError, match="Git operation failed"):
        releases.newest_release(plain)
    with pytest.raises(ReleaseError, match="Git operation failed"):
        releases.read_release(plain, "fall-2026")
    with pytest.raises(ReleaseError, match="Git operation failed"):
        releases.list_releases(tmp_path / "missing")


def test_git_ignores_user_configuration_and_core_sets_no_commit_identity(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    mirror, _published = _published_mirror(tmp_path, ["fall-2026"])
    home = tmp_path / "home"
    home.mkdir()
    (home / ".gitconfig").write_text("[user]\n\tname = Someone Else\n", encoding="utf-8")
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("GIT_CONFIG_GLOBAL", str(home / ".gitconfig"))

    with pytest.raises(ReleaseError, match="Git operation failed"):
        releases.run_git(mirror, "config", "--get", "user.name")
    assert releases.list_releases(mirror) != []


def test_releases_are_ordered_by_parsed_publication_time(tmp_path: Path) -> None:
    mirror, [first, second] = _published_mirror(tmp_path, ["first", "second"])
    # As strings "10:00+00:00" sorts after "09:30-02:00", but 09:30-02:00 is 11:30 UTC.
    first = first.model_copy(update={"published_at": "2026-11-01T10:00:00+00:00"})
    second = second.model_copy(update={"published_at": "2026-11-01T09:30:00-02:00"})
    for release in (first, second):
        _tag(
            mirror,
            release.slug,
            release.commit_oid,
            _manifest(release),
            date="2026-11-01T00:00:00+00:00",
        )

    assert [release.slug for release in releases.list_releases(mirror)] == ["second", "first"]
    assert releases.scan_releases(mirror).newest == second

    naive = first.model_copy(update={"published_at": "2026-11-01T12:00:00.000"})
    _tag(mirror, "first", naive.commit_oid, _manifest(naive), date="2026-11-01T00:00:00+00:00")
    scan = releases.scan_releases(mirror)
    assert scan.releases == [second]
    assert scan.rejected == [
        RejectedTag(name="course-release/first", reason="Release publication time is invalid")
    ]
