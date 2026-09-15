import os
import subprocess
from pathlib import Path
from typing import Annotated, Literal, Self
from uuid import uuid4

import yaml
from pydantic import BaseModel, ConfigDict, Field, ValidationError, model_validator

from course_harness.canonical_mutation import CanonicalFile, apply_canonical_mutation

Goal = Annotated[str, Field(min_length=1, max_length=500)]
Outcome = Annotated[str, Field(min_length=1, max_length=500)]


class LectureInput(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    title: str = Field(min_length=1, max_length=200)
    group: str | None = Field(default=None, min_length=1, max_length=100)
    source_focus: list[str] | None = Field(default=None)


class CoursePlanInput(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    title: str = Field(min_length=1, max_length=200)
    audience: str = Field(min_length=1, max_length=1000)
    goals: list[Goal] = Field(default_factory=list)
    outcomes: list[Outcome] = Field(default_factory=list)
    lectures: list[LectureInput] = Field(min_length=1)


class Lecture(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    id: str = Field(pattern=r"^lecture-[0-9a-f]{12}$")
    title: str = Field(min_length=1, max_length=200)
    group: str | None = Field(default=None, min_length=1, max_length=100)
    source_focus: list[str] | None = Field(default=None)
    presentation_id: str | None = Field(default=None)


class CoursePlan(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    schema_version: Literal[1] = 1
    id: str = Field(pattern=r"^course-[0-9a-f]{12}$")
    title: str = Field(min_length=1, max_length=200)
    audience: str = Field(min_length=1, max_length=1000)
    goals: list[Goal] = Field(default_factory=list)
    outcomes: list[Outcome] = Field(default_factory=list)
    lectures: list[Lecture] = Field(min_length=1)
    template_profile_id: str | None = Field(default=None)
    template_profile_version: int | None = Field(default=None)

    @model_validator(mode="after")
    def lecture_identities_are_unique(self) -> Self:
        identities = [lecture.id for lecture in self.lectures]
        if len(identities) != len(set(identities)):
            raise ValueError("Lecture IDs must be unique")
        return self


class InvalidCoursePlan(ValueError):
    """Canonical Course state exists but does not satisfy the schema."""


def create_course_plan(course_input: CoursePlanInput) -> CoursePlan:
    return CoursePlan(
        id=f"course-{uuid4().hex[:12]}",
        title=course_input.title,
        audience=course_input.audience,
        goals=course_input.goals,
        outcomes=course_input.outcomes,
        lectures=[
            Lecture(
                id=f"lecture-{uuid4().hex[:12]}",
                title=lecture.title,
                group=lecture.group,
                source_focus=lecture.source_focus,
            )
            for lecture in course_input.lectures
        ],
    )


def read_course_plan(workspace: Path) -> CoursePlan | None:
    course_path = workspace / "course.yaml"
    if not course_path.exists():
        return None
    try:
        payload = yaml.safe_load(course_path.read_text(encoding="utf-8"))
        return CoursePlan.model_validate(payload)
    except (OSError, UnicodeError, yaml.YAMLError, ValidationError) as error:
        raise InvalidCoursePlan(str(error)) from error


def initialize_workspace_history(workspace: Path) -> None:
    subprocess.run(
        ["git", "init", "--quiet", "--initial-branch=main", str(workspace)],
        check=True,
        capture_output=True,
        text=True,
    )


def _write_temporary_plan(workspace: Path, plan: CoursePlan) -> Path:
    temporary_path = workspace / f".course-{uuid4().hex}.yaml.tmp"
    serialized = serialize_course_plan(plan).decode("utf-8")
    with temporary_path.open("x", encoding="utf-8") as stream:
        stream.write(serialized)
        stream.flush()
        os.fsync(stream.fileno())
    return temporary_path


def serialize_course_plan(plan: CoursePlan) -> bytes:
    """Return the exact canonical bytes written for a Course Plan."""
    return yaml.safe_dump(
        plan.model_dump(mode="json"), allow_unicode=True, sort_keys=False, width=100
    ).encode("utf-8")


def create_course_plan_file(
    workspace: Path, plan: CoursePlan, *, expected: CanonicalFile | None = None
) -> None:
    if expected is not None:
        apply_canonical_mutation(
            workspace,
            expected={"course.yaml": expected},
            updates={"course.yaml": serialize_course_plan(plan)},
        )
        return
    temporary_path = _write_temporary_plan(workspace, plan)
    try:
        os.link(temporary_path, workspace / "course.yaml")
    finally:
        temporary_path.unlink(missing_ok=True)


def write_course_plan(
    workspace: Path, plan: CoursePlan, *, expected: CanonicalFile | None = None
) -> None:
    if expected is not None:
        apply_canonical_mutation(
            workspace,
            expected={"course.yaml": expected},
            updates={"course.yaml": serialize_course_plan(plan)},
        )
        return
    course_path = workspace / "course.yaml"
    temporary_path = _write_temporary_plan(workspace, plan)
    try:
        temporary_path.replace(course_path)
    finally:
        temporary_path.unlink(missing_ok=True)
