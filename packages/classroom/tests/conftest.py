"""Fixture Course repositories, made by the Course Harness release service and pushed like a
Course Author would, and a Classroom bound to a temporary data directory."""

import subprocess
from collections.abc import Callable, Iterator
from dataclasses import dataclass
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from pptx import Presentation as PPTXPresentation

from course_harness.core.course_plan import CoursePlan, Lecture, write_course_plan
from course_harness.core.presentation import Presentation, TitleSlide, write_presentation
from course_harness.core.quiz import Quiz, QuizOption, QuizQuestion, write_quiz
from course_harness.core.release_validation import ReleaseSelection, Waiver
from course_harness.core.releases import CourseRelease
from course_harness.core.template_profiles import TemplateLayoutMapping, TemplateProfile
from course_harness.release_service import (
    PublishReleaseRequest,
    backfill_release_files,
    publish_release,
)
from course_harness.template_profiles import profile_dir, write_profile_version
from course_harness.workspace_history import (
    RevisionCreateRequest,
    create_revision,
    record_app_authored_state,
)
from course_harness.workspace_remote import push_workspace
from course_harness_classroom.app import create_app
from course_harness_classroom.config import ClassroomSettings
from course_harness_classroom.security import CSRF_HEADER

ADMIN_TOKEN = "classroom-admin-token-for-tests-0123456789"
LECTURE_ID = "lecture-aaaaaaaaaaaa"
PRESENTATION_ID = "presentation-aaaaaaaaaaaa"
UNCITED_QUESTION = f"grounding.uncited-question:{LECTURE_ID}:question-aaaaaaaaaaaa"


def git(repository: Path, *arguments: str, env: dict[str, str] | None = None) -> bytes:
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
        check=True,
        capture_output=True,
        env=env,
    ).stdout


@dataclass
class CourseRepository:
    """A Course Workspace whose Releases are pushed to a local bare remote."""

    root: Path
    workspace: Path
    remote: Path
    template: TemplateProfile | None
    title: str = "Causal inference"

    def publish(self, slug: str, name: str, *, title: str = "Causal inference") -> CourseRelease:
        plan = _plan(title, self.template)
        if plan.title != self.title:
            self.title = plan.title
            write_course_plan(self.workspace, plan)
            record_app_authored_state(self.workspace)
            create_revision(self.workspace, RevisionCreateRequest(summary=f"Prepare {name}"))
        record_app_authored_state(self.workspace)
        release = publish_release(
            workspace=self.workspace,
            release_data_root=self.root / "releases",
            templates_data_root=self.root / "templates",
            evidence_line_counts={},
            request=PublishReleaseRequest(
                slug=slug,
                name=name,
                selection=ReleaseSelection(
                    lecture_ids=[LECTURE_ID], artifact_ids=[PRESENTATION_ID]
                ),
                waivers=[Waiver(finding_id=UNCITED_QUESTION, justification="A worked example.")],
            ),
        )
        self.push()
        return release

    def push(self) -> None:
        backfill = backfill_release_files(
            workspace=self.workspace, release_data_root=self.root / "releases"
        )
        result = push_workspace(self.workspace, remote="the remote", backfill=backfill)
        assert result.ok, result.message


def _plan(title: str, template: TemplateProfile | None) -> CoursePlan:
    return CoursePlan(
        id="course-aaaaaaaaaaaa",
        title=title,
        audience="Graduate students",
        template_profile_id=template.id if template else None,
        template_profile_version=template.version if template else None,
        lectures=[Lecture(id=LECTURE_ID, title="Foundations", presentation_id=PRESENTATION_ID)],
    )


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


def make_course_repository(root: Path, *, custom_template: bool = False) -> CourseRepository:
    root.mkdir(parents=True, exist_ok=True)
    template = _custom_template(root / "templates") if custom_template else None
    workspace = root / "course"
    workspace.mkdir()
    git(workspace, "init", "--quiet", "--initial-branch=main")
    write_course_plan(workspace, _plan("Causal inference", template))
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
    remote = root / "remote.git"
    git(root, "init", "--quiet", "--bare", str(remote))
    git(workspace, "remote", "add", "origin", str(remote))
    return CourseRepository(root=root, workspace=workspace, remote=remote, template=template)


GitRunner = Callable[..., bytes]
MakeRepository = Callable[..., CourseRepository]


@pytest.fixture(name="git")
def git_fixture() -> GitRunner:
    return git


@pytest.fixture
def make_repository(tmp_path: Path) -> MakeRepository:
    def make(name: str = "author", *, custom_template: bool = False) -> CourseRepository:
        return make_course_repository(tmp_path / name, custom_template=custom_template)

    return make


@pytest.fixture
def course_repository(make_repository: MakeRepository) -> CourseRepository:
    return make_repository()


@pytest.fixture(autouse=True)
def without_libreoffice(monkeypatch: pytest.MonkeyPatch) -> None:
    """Keep tests fast and independent of an installed LibreOffice; a test may opt back in."""
    monkeypatch.setattr("course_harness.core.slide_preview.shutil.which", lambda _name: None)


@pytest.fixture
def settings(tmp_path: Path) -> ClassroomSettings:
    return ClassroomSettings(
        data_dir=tmp_path / "classroom",
        admin_token=ADMIN_TOKEN,
        # Plain HTTP on loopback keeps cookies non-Secure, so the HTTP test client sends them.
        public_url="http://localhost",
        fetch_interval_seconds=0,
        fetch_timeout_seconds=60,
        allow_local_repos=True,
        static_dir=tmp_path / "no-interface",
    )


MakeClient = Callable[..., TestClient]


@pytest.fixture
def make_client(settings: ClassroomSettings) -> Iterator[MakeClient]:
    clients: list[TestClient] = []

    def make(
        app_settings: ClassroomSettings | None = None, *, base_url: str = "http://testserver"
    ) -> TestClient:
        client = TestClient(
            create_app(app_settings or settings, periodic_sync=False),
            base_url=base_url,
            headers={CSRF_HEADER: "1"},
            follow_redirects=False,
        )
        clients.append(client)
        return client

    yield make
    for client in clients:
        client.close()


@pytest.fixture
def admin(make_client: MakeClient) -> TestClient:
    client = make_client()
    response = client.post("/api/admin/session", json={"token": ADMIN_TOKEN})
    assert response.status_code == 200
    return client
