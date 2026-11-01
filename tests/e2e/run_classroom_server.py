"""A Classroom serving one published fixture Course, for the Playwright Classroom journeys.

The Course is authored and released with Course Harness's own services and pushed to a local
bare remote, as a Course Author would. By default the Classroom attaches that remote, syncs it,
and gets a Learner and an Instructor Share Link with fixed tokens so the journeys can open them.

With ``--admin`` the Classroom starts with no courses and an Administrator token instead, for the
Administrator journey; the remote is published to a fixed folder (``ADMIN_FIXTURE_DIR``) so the
journey can attach it by path.
"""

import shutil
import subprocess
import sys
from pathlib import Path
from tempfile import TemporaryDirectory

import uvicorn

from course_harness.core.course_plan import CoursePlan, Lecture, write_course_plan
from course_harness.core.presentation import (
    BulletsSlide,
    ClosingSlide,
    Presentation,
    QuoteSlide,
    SlideCitation,
    TitleSlide,
    TwoColumnSlide,
    write_presentation,
)
from course_harness.core.quiz import Quiz, QuizOption, QuizQuestion, read_quiz, write_quiz
from course_harness.core.release_validation import ReleaseSelection, Waiver, validate_release
from course_harness.core.sources import Source, SourcesIndex, write_sources_index
from course_harness.release_service import (
    PublishReleaseRequest,
    backfill_release_files,
    publish_release,
)
from course_harness.workspace_history import (
    RevisionCreateRequest,
    create_revision,
    record_app_authored_state,
)
from course_harness.workspace_remote import push_workspace
from course_harness_classroom.app import create_app
from course_harness_classroom.config import ClassroomSettings
from course_harness_classroom.security import token_hash

PORT = 18766  # playwright.config.ts and classroom-learner.spec.ts use this port
ADMIN_PORT = 18767  # playwright.config.ts and classroom-admin.spec.ts use this port
ADMIN_TOKEN = "e2e-administrator-token-0123456789abcdef"
# classroom-admin.spec.ts attaches `<this>/author/remote.git`.
ADMIN_FIXTURE_DIR = Path(__file__).resolve().parents[2] / ".cache" / "classroom-e2e-admin"
LEARNER_TOKEN = "e2e-learner-share-link-token-0123456789abcdef"
INSTRUCTOR_TOKEN = "e2e-instructor-share-link-token-0123456789abcdef"
SOURCE_ID = "source-aaaaaaaaaaaa"
OTHER_SOURCE_ID = "source-bbbbbbbbbbbb"
LINE_COUNT = 400

RUBIN = SlideCitation(
    source_id=SOURCE_ID,
    label="Rubin (1974), Estimating causal effects of treatments",
    url="https://doi.org/10.1037/h0037350",
    line_start=11,
    line_end=14,
)
PEARL = SlideCitation(
    source_id=OTHER_SOURCE_ID,
    label="Pearl (2009), Causality",
    url="https://bayes.cs.ucla.edu/BOOK-2K/",
)


def lecture_id(n: int) -> str:
    return f"lecture-{n:012x}"


def presentation_id(n: int) -> str:
    return f"presentation-{n:012x}"


def slide_id(lecture: int, n: int) -> str:
    return f"slide-{lecture:06x}{n:06x}"


def question_id(lecture: int, n: int) -> str:
    return f"question-{lecture:06x}{n:06x}"


def option_id(lecture: int, question: int, n: int) -> str:
    return f"option-{lecture:04x}{question:04x}{n:04x}"


LECTURES = [
    (1, "From association to intervention", "Foundations"),
    (2, "Randomised experiments", "Foundations"),
    (3, "Confounding and adjustment", "Observational studies"),
]


def presentation(number: int, title: str) -> Presentation:
    return Presentation(
        id=presentation_id(number),
        lecture_id=lecture_id(number),
        slides=[
            TitleSlide(
                id=slide_id(number, 1),
                title=title,
                subtitle="Causal inference in practice",
                speaker_notes="Welcome everyone. Today builds on last week's reading.",
            ),
            BulletsSlide(
                id=slide_id(number, 2),
                title="Why randomise?",
                bullets=[
                    "Random assignment breaks the link between treatment and confounders",
                    "Differences in outcomes can then be read as effects",
                    "Sampling error remains and shrinks with sample size",
                ],
                speaker_notes=(
                    "Stress the difference between bias and variance here.\n"
                    "Ask: what would go wrong if people chose their own treatment?"
                ),
                citations=[RUBIN],
            ),
            TwoColumnSlide(
                id=slide_id(number, 3),
                title="Association and intervention",
                left_content=["P(Y | X = x)", "What we see"],
                right_content=["P(Y | do(X = x))", "What we would see if we set X"],
                citations=[PEARL, RUBIN],
            ),
            QuoteSlide(
                id=slide_id(number, 4),
                quote="No causation without manipulation.",
                attribution="Holland (1986)",
            ),
            ClosingSlide(id=slide_id(number, 5), title="Next time", text="Read chapter 3."),
        ],
    )


def quiz(number: int) -> Quiz:
    return Quiz(
        lecture_id=lecture_id(number),
        questions=[
            QuizQuestion(
                id=question_id(number, 1),
                prompt="What does **random assignment** remove from a comparison?",
                options=[
                    QuizOption(
                        id=option_id(number, 1, 1),
                        text="Confounding",
                        correct=True,
                        explanation=(
                            "Random assignment balances confounders between groups *on "
                            "average*, so they cannot explain a difference in outcomes."
                        ),
                    ),
                    QuizOption(
                        id=option_id(number, 1, 2),
                        text="Sampling error",
                        explanation=(
                            "Randomisation does not remove sampling error: small groups can "
                            "still differ by chance. Larger samples make it smaller."
                        ),
                        citations=[RUBIN],
                    ),
                    QuizOption(
                        id=option_id(number, 1, 3),
                        text="Measurement error",
                        explanation=(
                            "How outcomes are measured is a separate question; randomisation "
                            "says nothing about it."
                        ),
                    ),
                    QuizOption(
                        id=option_id(number, 1, 4),
                        text="The need for a control group",
                        explanation=(
                            "Randomisation *assigns* people to treatment and control; it "
                            "needs a control group to compare with."
                        ),
                    ),
                ],
            ),
            QuizQuestion(
                id=question_id(number, 2),
                prompt="Which expression describes an **intervention** on `X`?",
                options=[
                    QuizOption(
                        id=option_id(number, 2, 1),
                        text="P(Y | X = x)",
                        explanation="This conditions on observing X; it is an association.",
                        citations=[PEARL],
                    ),
                    QuizOption(id=option_id(number, 2, 2), text="P(Y | do(X = x))", correct=True),
                ],
            ),
        ],
    )


def git(directory: Path, *arguments: str) -> None:
    subprocess.run(
        [
            "git",
            "-c",
            "user.name=Course Harness e2e",
            "-c",
            "user.email=course-harness-e2e@example.invalid",
            *arguments,
        ],
        cwd=directory,
        check=True,
        capture_output=True,
    )


def publish_fixture_course(root: Path) -> Path:
    """Author, release and push the fixture Course; return its bare remote."""
    workspace = root / "course"
    workspace.mkdir(parents=True)
    git(workspace, "init", "--quiet", "--initial-branch=main")
    plan = CoursePlan(
        id="course-aaaaaaaaaaaa",
        title="Causal Inference in Practice",
        audience="Applied researchers who know regression",
        lectures=[
            Lecture(id=lecture_id(n), title=title, group=group, presentation_id=presentation_id(n))
            for n, title, group in LECTURES
        ],
    )
    write_course_plan(workspace, plan)
    sources = SourcesIndex(
        sources=[
            Source(
                id=source,
                resource_id=f"resource-{source[-12:]}",
                source_version_id=source[-1] * 64,
                label=label,
                admitted_at="2026-10-01T09:00:00+00:00",
            )
            for source, label in ((SOURCE_ID, RUBIN.label), (OTHER_SOURCE_ID, PEARL.label))
        ]
    )
    write_sources_index(workspace, sources)
    presentations = [presentation(n, title) for n, title, _group in LECTURES]
    for item in presentations:
        write_presentation(workspace, item)
    for n, _title, _group in LECTURES:
        write_quiz(workspace, quiz(n))
    create_revision(workspace, RevisionCreateRequest(summary="Fixture Course"))
    record_app_authored_state(workspace)

    selection = ReleaseSelection(
        lecture_ids=[lecture_id(n) for n, *_ in LECTURES],
        artifact_ids=[presentation_id(n) for n, *_ in LECTURES],
    )
    line_counts = {source.source_version_id: LINE_COUNT for source in sources.sources}
    quizzes = [read_quiz(workspace, lecture_id(n)) for n, *_ in LECTURES]
    findings = validate_release(
        plan=plan,
        presentations=presentations,
        sources=sources,
        selection=selection,
        evidence_line_counts=line_counts,
        quizzes=[item for item in quizzes if item is not None],
    ).findings
    errors = [finding.message for finding in findings if finding.severity == "error"]
    if errors:
        raise SystemExit("The fixture Course is not valid:\n" + "\n".join(errors))
    publish_release(
        workspace=workspace,
        release_data_root=root / "releases",
        templates_data_root=root / "templates",
        evidence_line_counts=line_counts,
        request=PublishReleaseRequest(
            slug="autumn-2026",
            name="Autumn 2026",
            selection=selection,
            waivers=[
                Waiver(finding_id=finding.id, justification="Fixture Course.")
                for finding in findings
                if finding.severity == "warning"
            ],
        ),
    )
    remote = root / "remote.git"
    git(root, "init", "--quiet", "--bare", str(remote))
    git(workspace, "remote", "add", "origin", str(remote))
    backfill = backfill_release_files(workspace=workspace, release_data_root=root / "releases")
    result = push_workspace(workspace, remote="the fixture remote", backfill=backfill)
    if not result.ok:
        raise SystemExit(result.message)
    return remote


def serve_for_administrator() -> None:
    """An empty Classroom with an Administrator token that accepts local repositories."""
    shutil.rmtree(ADMIN_FIXTURE_DIR, ignore_errors=True)
    try:
        publish_fixture_course(ADMIN_FIXTURE_DIR / "author")
        settings = ClassroomSettings(
            data_dir=ADMIN_FIXTURE_DIR / "classroom",
            admin_token=ADMIN_TOKEN,
            public_url=f"http://127.0.0.1:{ADMIN_PORT}",
            fetch_interval_seconds=0,
            fetch_timeout_seconds=60,
            allow_local_repos=True,
            static_dir=None,
        )
        app = create_app(settings, periodic_sync=False)
        uvicorn.run(app, host="127.0.0.1", port=ADMIN_PORT, log_level="warning")
    finally:
        shutil.rmtree(ADMIN_FIXTURE_DIR, ignore_errors=True)


def main() -> None:
    if "--admin" in sys.argv[1:]:
        serve_for_administrator()
        return
    with TemporaryDirectory(prefix="classroom-e2e-") as directory:
        root = Path(directory)
        remote = publish_fixture_course(root / "author")
        settings = ClassroomSettings(
            data_dir=root / "classroom",
            admin_token=None,
            fetch_interval_seconds=0,
            fetch_timeout_seconds=60,
            allow_local_repos=True,
            static_dir=None,
        )
        app = create_app(settings, periodic_sync=False)
        store = app.state.store
        course = store.add_course(slug="causal", repo_url=str(remote))
        synced = app.state.sync.sync(course.id)
        if synced.release_slug is None:
            raise SystemExit(f"The fixture Course did not sync: {synced.sync_message}")
        store.set_share_link(course.id, "learner", token_hash(LEARNER_TOKEN))
        store.set_share_link(course.id, "instructor", token_hash(INSTRUCTOR_TOKEN))
        uvicorn.run(app, host="127.0.0.1", port=PORT, log_level="warning")


if __name__ == "__main__":
    main()
