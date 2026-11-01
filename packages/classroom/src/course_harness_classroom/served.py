"""The served form of a Course Release, prepared once per sync from the verified mirror.

A sync that picks a new Release reads everything a page needs from Git once (the manifest, the
Course Plan, the Presentations, the Quizzes and the Release files, all verified by
``course_harness.core``), renders the template's layout backgrounds, and writes the result to its
own directory. Requests then read only that directory: they never touch Git, and a later fetch
that moves a ref cannot change what is served until the next sync verifies it.

Layout of ``<course dir>/served/<release slug>-<commit prefix>/``::

    release.json                       ServedSnapshot
    artifacts/<presentation id>.pptx   verified Artifact bytes
    template.pptx                      verified template bytes, for a custom Template Profile
    previews/<profile>/backgrounds/v<n>/<layout>.png   rendered layout backgrounds
"""

from __future__ import annotations

import logging
import shutil
from dataclasses import dataclass
from pathlib import Path
from uuid import uuid4

from pydantic import BaseModel, ConfigDict

from course_harness.core.course_plan import CoursePlan
from course_harness.core.presentation import Presentation
from course_harness.core.quiz import Quiz
from course_harness.core.release_files import ReleaseFiles, read_release_files
from course_harness.core.releases import (
    MAX_RELEASE_READ_BYTES,
    RELEASE_READ_SECONDS,
    CourseRelease,
    ReleaseError,
    read_budget,
    tagged_plan,
    tagged_presentation,
    tagged_quiz,
)
from course_harness.core.slide_preview import (
    PresentationPreview,
    PreviewContext,
    build_preview,
    render_layout_backgrounds,
    renderer_capability,
)

logger = logging.getLogger(__name__)

SNAPSHOT_FILE = "release.json"


class ServedArtifact(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str
    size: int


class ServedLecture(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str
    title: str
    group: str | None = None
    presentation: Presentation | None = None
    preview: PresentationPreview | None = None
    # Present only when the Release files were read and verified.
    artifact: ServedArtifact | None = None
    quiz: Quiz | None = None


class ServedSnapshot(BaseModel):
    """Everything the Classroom serves for one Release; Quizzes include their answer keys."""

    model_config = ConfigDict(extra="forbid")

    manifest: CourseRelease
    title: str
    lectures: list[ServedLecture]
    files_available: bool
    backgrounds_rendered: bool


@dataclass(frozen=True)
class PreparedRelease:
    directory: Path
    snapshot: ServedSnapshot
    warnings: list[str]


def snapshot_name(release: CourseRelease) -> str:
    return f"{release.slug}-{release.commit_oid[:12]}"


def asset_url_prefix(course_slug: str, name: str) -> str:
    return f"/api/c/{course_slug}/assets/{name}"


def prepare_release(
    mirror: Path, release: CourseRelease, *, served_dir: Path, course_slug: str
) -> PreparedRelease:
    """Read, verify and render one Release into a fresh directory below ``served_dir``.

    Raises ``ReleaseError`` when the Release's Course content cannot be read; missing or invalid
    Release files and a missing or failing renderer only add warnings.
    """
    warnings: list[str] = []
    name = snapshot_name(release)
    staging = served_dir / f".staging-{uuid4().hex}"
    staging.mkdir(parents=True)
    try:
        files: ReleaseFiles | None
        try:
            files = read_release_files(mirror, release)
        except ReleaseError as error:
            files = None
            warnings.append(f"Release files are unavailable, so there is no PPTX download: {error}")
        template_path: Path | None = None
        if files is not None:
            if files.template is not None:
                template_path = staging / "template.pptx"
                template_path.write_bytes(files.template)
            (staging / "artifacts").mkdir()
            for artifact_id, content in files.artifacts.items():
                (staging / "artifacts" / f"{artifact_id}.pptx").write_bytes(content)

        profile = release.template_profile.definition
        custom_template = release.template_profile.template_storage_path is not None
        can_preview = not custom_template or template_path is not None
        prefix = asset_url_prefix(course_slug, name)
        context = PreviewContext(
            profile=profile,
            template_path=template_path,
            cache_dir=staging / "previews",
            asset_url=lambda relative: f"{prefix}/previews/{relative}",
        )
        backgrounds_rendered = False
        if can_preview and renderer_capability().available:
            try:
                render_layout_backgrounds(context)
                backgrounds_rendered = True
            except Exception as error:  # LibreOffice failures must not block serving
                logger.warning("Rendering backgrounds of %s failed: %s", release.tag, error)
                warnings.append("Slide backgrounds could not be rendered; plain ones are shown.")

        with read_budget(seconds=RELEASE_READ_SECONDS, max_bytes=MAX_RELEASE_READ_BYTES):
            plan = tagged_plan(mirror, release.commit_oid)
            lectures = _served_lectures(
                mirror,
                release,
                plan,
                files_available=files is not None,
                context=context,
                can_preview=can_preview,
            )
        snapshot = ServedSnapshot(
            manifest=release,
            title=plan.title,
            lectures=lectures,
            files_available=files is not None,
            backgrounds_rendered=backgrounds_rendered,
        )
        (staging / SNAPSHOT_FILE).write_text(snapshot.model_dump_json(), encoding="utf-8")
        target = served_dir / name
        if target.exists():
            shutil.rmtree(target)
        staging.rename(target)
    except BaseException:
        shutil.rmtree(staging, ignore_errors=True)
        raise
    return PreparedRelease(directory=target, snapshot=snapshot, warnings=warnings)


def _served_lectures(
    mirror: Path,
    release: CourseRelease,
    plan: CoursePlan,
    *,
    files_available: bool,
    context: PreviewContext,
    can_preview: bool,
) -> list[ServedLecture]:
    artifacts = {artifact.lecture_id: artifact for artifact in release.artifacts}
    quizzes = {quiz.lecture_id for quiz in release.quizzes}
    lectures: list[ServedLecture] = []
    for lecture in plan.lectures:
        if lecture.id not in release.included_lecture_ids:
            continue
        artifact = artifacts.get(lecture.id)
        presentation = (
            tagged_presentation(mirror, release.commit_oid, artifact.id) if artifact else None
        )
        preview = build_preview(presentation, context) if presentation and can_preview else None
        lectures.append(
            ServedLecture(
                id=lecture.id,
                title=lecture.title,
                group=lecture.group,
                presentation=presentation,
                preview=preview,
                artifact=(
                    ServedArtifact(id=artifact.id, size=artifact.size)
                    if artifact and files_available
                    else None
                ),
                quiz=(
                    tagged_quiz(mirror, release.commit_oid, lecture.id)
                    if lecture.id in quizzes
                    else None
                ),
            )
        )
    return lectures


def load_snapshot(directory: Path) -> ServedSnapshot:
    return ServedSnapshot.model_validate_json((directory / SNAPSHOT_FILE).read_bytes())


def remove_other_snapshots(served_dir: Path, keep: str) -> None:
    for entry in served_dir.iterdir():
        if entry.name != keep and entry.is_dir():
            shutil.rmtree(entry, ignore_errors=True)
