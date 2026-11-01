"""Attaching a course and syncing its newest valid Release from a fixture repository."""

import os
import subprocess
import threading
import time
from dataclasses import replace
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from course_harness_classroom import mirror as mirror_module
from course_harness_classroom.config import ClassroomSettings
from course_harness_classroom.store import Store
from course_harness_classroom.sync import PeriodicSync, SyncService


def _attach(admin: TestClient, repository: CourseRepository, slug: str = "causal") -> dict:
    response = admin.post(
        "/api/admin/courses", json={"repo_url": str(repository.remote), "slug": slug}
    )
    assert response.status_code == 201, response.text
    return response.json()


def _sync(admin: TestClient, slug: str = "causal") -> dict:
    response = admin.post(f"/api/admin/courses/{slug}/sync")
    assert response.status_code == 200, response.text
    return response.json()


LECTURE_ID = "lecture-aaaaaaaaaaaa"
PRESENTATION_ID = "presentation-aaaaaaaaaaaa"
CourseRepository = Any
MakeClient = Any


def _git(repository: Path, *arguments: str, env: dict[str, str] | None = None) -> bytes:
    command = ["git", "-c", "user.name=Tests", "-c", "user.email=tests@example.invalid"]
    result = subprocess.run(
        [*command, *arguments], cwd=repository, check=False, capture_output=True, env=env
    )
    assert result.returncode == 0, result.stderr.decode()
    return result.stdout


def _mirror(settings: ClassroomSettings) -> Path:
    return settings.courses_dir / "1" / "mirror.git"


def _broken_release_tag(repository: CourseRepository, slug: str) -> None:
    """Push a newer Release tag whose message is not a manifest."""
    environment = {**os.environ, "GIT_COMMITTER_DATE": "@4102444800 +0000"}
    _git(
        repository.remote,
        "tag",
        "-a",
        f"course-release/{slug}",
        "-m",
        "not a manifest",
        "main",
        env=environment,
    )


def test_attaching_a_course_serves_its_newest_release(
    admin: TestClient, course_repository: CourseRepository, settings: ClassroomSettings
) -> None:
    release = course_repository.publish("fall-2026", "Fall 2026")

    course = _attach(admin, course_repository)

    assert course["sync_status"] == "ok", course["sync_message"]
    assert course["title"] == "Causal inference"
    assert course["release"] == {
        "tag": "course-release/fall-2026",
        "name": "Fall 2026",
        "published_at": release.published_at,
    }
    served = settings.courses_dir / "1" / "served" / f"fall-2026-{release.commit_oid[:12]}"
    assert (served / "release.json").is_file()
    assert (served / "artifacts" / f"{PRESENTATION_ID}.pptx").read_bytes()[:2] == b"PK"
    store = Store(settings.database_path)
    snapshot = SyncService(store, settings).snapshot(store.courses()[0])
    assert snapshot is not None
    assert snapshot.lectures[0].id == LECTURE_ID
    assert snapshot.lectures[0].quiz is not None
    assert snapshot.lectures[0].preview is not None
    assert snapshot.backgrounds_rendered is False


def test_the_mirror_fetches_only_the_release_refspecs(
    admin: TestClient, course_repository: CourseRepository, settings: ClassroomSettings
) -> None:
    course_repository.publish("fall-2026", "Fall 2026")
    _git(course_repository.remote, "update-ref", "refs/pull/1/head", "main")
    _git(course_repository.remote, "update-ref", "refs/heads/feature", "main")
    _git(course_repository.remote, "update-ref", "refs/tags/unrelated", "main")

    _attach(admin, course_repository)

    refs = _git(_mirror(settings), "for-each-ref", "--format=%(refname)").decode().split()
    assert "refs/heads/main" in refs
    assert "refs/tags/course-release/fall-2026" in refs
    assert "refs/course-harness/release-files/fall-2026" in refs
    assert not [
        ref
        for ref in refs
        if not ref.startswith(
            ("refs/heads/main", "refs/tags/course-release/", "refs/course-harness/release-files/")
        )
    ]
    assert not list((_mirror(settings) / "hooks").glob("*"))
    assert not (_mirror(settings) / "objects" / "info" / "alternates").exists()


def test_a_newer_release_replaces_the_served_one(
    admin: TestClient, course_repository: CourseRepository, settings: ClassroomSettings
) -> None:
    first = course_repository.publish("fall-2026", "Fall 2026")
    _attach(admin, course_repository)

    time.sleep(1.1)  # Releases are ordered by tag creation time, in whole seconds
    second = course_repository.publish("spring-2027", "Spring 2027", title="Causal inference II")
    course = _sync(admin)

    assert course["sync_status"] == "ok"
    assert course["release"]["tag"] == "course-release/spring-2027"
    assert course["title"] == "Causal inference II"
    served_dir = settings.courses_dir / "1" / "served"
    # The previous Release stays on disk, so pages opened before the switch keep working.
    assert sorted(path.name for path in served_dir.iterdir()) == [
        f"fall-2026-{first.commit_oid[:12]}",
        f"spring-2027-{second.commit_oid[:12]}",
    ]
    learner = admin.post("/api/admin/courses/causal/links/learner").json()["url"]
    admin.get(learner.removeprefix("http://localhost"))
    previous = (
        f"/api/c/causal/assets/fall-2026-{first.commit_oid[:12]}/artifacts/{PRESENTATION_ID}.pptx"
    )
    assert admin.get(previous).status_code == 200

    time.sleep(1.1)
    third = course_repository.publish("summer-2027", "Summer 2027", title="Causal inference III")
    _sync(admin)

    assert sorted(path.name for path in served_dir.iterdir()) == [
        f"spring-2027-{second.commit_oid[:12]}",
        f"summer-2027-{third.commit_oid[:12]}",
    ]
    assert admin.get(previous).status_code == 404


def test_an_invalid_newer_release_is_skipped_and_the_previous_one_stays(
    admin: TestClient, course_repository: CourseRepository
) -> None:
    course_repository.publish("fall-2026", "Fall 2026")
    _attach(admin, course_repository)
    _broken_release_tag(course_repository, "broken")

    course = _sync(admin)

    assert course["release"]["tag"] == "course-release/fall-2026"
    assert course["sync_status"] == "warning"
    assert "Skipped Release course-release/broken" in course["sync_message"]


def test_a_repository_without_a_valid_release_reports_an_error(
    admin: TestClient, course_repository: CourseRepository
) -> None:
    course_repository.push()
    _broken_release_tag(course_repository, "broken")

    course = _attach(admin, course_repository)

    assert course["release"] is None
    assert course["sync_status"] == "error"
    assert "No valid Release" in course["sync_message"]


def test_a_failed_fetch_keeps_the_served_release(
    admin: TestClient, course_repository: CourseRepository
) -> None:
    course_repository.publish("fall-2026", "Fall 2026")
    _attach(admin, course_repository)
    moved = course_repository.remote.with_name("moved.git")
    course_repository.remote.rename(moved)

    course = _sync(admin)

    assert course["sync_status"] == "error"
    assert "Fetching the repository failed" in course["sync_message"]
    assert course["release"]["tag"] == "course-release/fall-2026"


def test_a_repository_larger_than_the_limit_is_not_served(
    make_client: MakeClient,
    course_repository: CourseRepository,
    settings: ClassroomSettings,
) -> None:
    course_repository.publish("fall-2026", "Fall 2026")
    small = replace(settings, max_mirror_bytes=2_000)
    client = make_client(small)
    client.post("/api/admin/session", json={"token": small.admin_token})

    course = _attach(client, course_repository)

    assert course["sync_status"] == "error"
    assert "larger than this Classroom allows" in course["sync_message"]
    assert course["release"] is None


def test_release_files_that_do_not_match_the_manifest_only_lose_the_download(
    admin: TestClient, course_repository: CourseRepository
) -> None:
    course_repository.publish("fall-2026", "Fall 2026")
    _git(
        course_repository.remote, "update-ref", "-d", "refs/course-harness/release-files/fall-2026"
    )

    course = _attach(admin, course_repository)

    assert course["sync_status"] == "warning"
    assert "Release files are unavailable" in course["sync_message"]
    assert course["release"]["tag"] == "course-release/fall-2026"


def test_backgrounds_are_rendered_from_the_release_template(
    admin: TestClient,
    make_repository: Any,
    settings: ClassroomSettings,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    repository = make_repository("templated", custom_template=True)
    release = repository.publish("fall-2026", "Fall 2026")
    rendered: list[bytes] = []

    def fake_render(pptx: bytes, target: Path) -> None:
        rendered.append(pptx)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(b"\x89PNG fake")

    monkeypatch.setattr(
        "course_harness.core.slide_preview.shutil.which", lambda _name: "/bin/libreoffice"
    )
    monkeypatch.setattr("course_harness.core.slide_preview._render_pptx_png", fake_render)

    course = _attach(admin, repository)

    assert course["sync_status"] == "ok", course["sync_message"]
    assert len(rendered) == 1
    name = f"fall-2026-{release.commit_oid[:12]}"
    background = (
        settings.courses_dir
        / "1"
        / "served"
        / name
        / "previews"
        / "tpl-aaaaaaaaaaaa"
        / "backgrounds"
        / "v1"
        / "title.png"
    )
    assert background.read_bytes() == b"\x89PNG fake"
    store = Store(settings.database_path)
    snapshot = SyncService(store, settings).snapshot(store.courses()[0])
    assert snapshot is not None and snapshot.lectures[0].preview is not None
    assert snapshot.lectures[0].preview.slides[0].background_url == (
        f"/api/c/causal/assets/{name}/previews/tpl-aaaaaaaaaaaa/backgrounds/v1/title.png"
    )


def test_a_failing_renderer_falls_back_to_plain_backgrounds(
    admin: TestClient,
    make_repository: Any,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    repository = make_repository("templated", custom_template=True)
    repository.publish("fall-2026", "Fall 2026")

    def failing_render(_pptx: bytes, _target: Path) -> None:
        raise RuntimeError("LibreOffice preview render failed")

    monkeypatch.setattr(
        "course_harness.core.slide_preview.shutil.which", lambda _name: "/bin/libreoffice"
    )
    monkeypatch.setattr("course_harness.core.slide_preview._render_pptx_png", failing_render)

    course = _attach(admin, repository)

    assert course["release"]["tag"] == "course-release/fall-2026"
    assert course["sync_status"] == "warning"
    assert "plain ones are shown" in course["sync_message"]


def test_periodic_sync_syncs_every_course_until_stopped(
    admin: TestClient, course_repository: CourseRepository, settings: ClassroomSettings
) -> None:
    course_repository.publish("fall-2026", "Fall 2026")
    _attach(admin, course_repository)
    store = Store(settings.database_path)
    synced = threading.Event()

    class RecordingService(SyncService):
        def sync_all(self) -> None:
            super().sync_all()
            synced.set()

    periodic = PeriodicSync(RecordingService(store, settings), interval_seconds=3600)
    periodic.start()
    try:
        assert synced.wait(30)
    finally:
        periodic.stop()

    assert store.courses()[0].sync_status == "ok"


def test_stale_lock_files_from_a_killed_fetch_are_removed(
    admin: TestClient, course_repository: CourseRepository, settings: ClassroomSettings
) -> None:
    course_repository.publish("fall-2026", "Fall 2026")
    _attach(admin, course_repository)
    stale = _mirror(settings) / "refs" / "heads" / "main.lock"
    stale.write_text("left by a killed fetch")
    (_mirror(settings) / "packed-refs.lock").write_text("")

    course = _sync(admin)

    assert course["sync_status"] == "ok", course["sync_message"]
    assert not list(_mirror(settings).rglob("*.lock"))


def test_a_completed_fetch_over_the_limit_discards_the_mirror(
    course_repository: CourseRepository, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    course_repository.publish("fall-2026", "Fall 2026")
    monkeypatch.setattr(mirror_module, "SIZE_CHECK_SECONDS", 60)  # only the final check runs
    mirror = tmp_path / "mirror.git"

    with pytest.raises(mirror_module.MirrorError, match="larger than"):
        mirror_module.fetch(
            mirror,
            str(course_repository.remote),
            timeout_seconds=60,
            max_bytes=2_000,
            allow_local=True,
        )

    assert (mirror / "HEAD").is_file()
    assert _git(mirror, "for-each-ref") == b""


def test_a_fetch_connects_only_to_the_checked_address_without_proxies(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def resolve(*_arguments: object, **_keywords: object) -> list[tuple]:
        return [(10, 1, 6, "", ("2606:4700::6810:84e5", 443, 0, 0))]

    calls: list[tuple[list[str], dict[str, str]]] = []

    class Refused(Exception):
        pass

    def popen(command: list[str], **keywords: Any) -> None:
        calls.append((command, keywords["env"]))
        raise Refused

    mirror_module.ensure_mirror(tmp_path / "mirror.git")
    monkeypatch.setattr("course_harness_classroom.repo_urls.socket.getaddrinfo", resolve)
    monkeypatch.setattr(mirror_module.subprocess, "Popen", popen)
    monkeypatch.setenv("https_proxy", "http://proxy.internal:3128")
    monkeypatch.setenv("HTTPS_PROXY", "http://proxy.internal:3128")

    with pytest.raises(Refused):
        mirror_module.fetch(
            tmp_path / "mirror.git",
            "https://git.example.org/course.git",
            timeout_seconds=10,
            max_bytes=10_000_000,
        )

    command, environment = calls[0]
    assert "http.curloptResolve=git.example.org:443:[2606:4700::6810:84e5]" in command
    assert "protocol.allow=never" in command and "protocol.https.allow=always" in command
    assert "https_proxy" not in environment and "HTTPS_PROXY" not in environment


def test_a_repository_with_no_release_says_how_to_publish_one(
    admin: TestClient, course_repository: CourseRepository
) -> None:
    course_repository.push()

    course = _attach(admin, course_repository)

    assert course["release"] is None
    assert course["sync_status"] == "error"
    assert course["sync_message"].startswith("This repository has no published Release yet.")
