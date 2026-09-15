from __future__ import annotations

from collections.abc import Mapping
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from course_harness.course_plan import CoursePlan
from course_harness.presentation import Presentation
from course_harness.sources import SourcesIndex

FindingSeverity = Literal["error", "warning"]
FindingScope = Literal["release", "lecture", "artifact", "slide", "content_block", "citation"]
LectureId = Annotated[str, Field(pattern=r"^lecture-[0-9a-f]{12}$")]
ArtifactId = Annotated[str, Field(pattern=r"^presentation-[0-9a-f]{12}$")]


class ReleaseSelection(BaseModel):
    """The Course material proposed for one partial Release."""

    model_config = ConfigDict(extra="forbid")

    lecture_ids: list[LectureId] = Field(min_length=1, max_length=1_000)
    artifact_ids: list[ArtifactId] = Field(default_factory=list, max_length=1_000)

    @model_validator(mode="after")
    def selected_identities_are_unique(self) -> ReleaseSelection:
        if len(self.lecture_ids) != len(set(self.lecture_ids)):
            raise ValueError("Selected Lecture IDs must be unique")
        if len(self.artifact_ids) != len(set(self.artifact_ids)):
            raise ValueError("Selected Artifact IDs must be unique")
        return self


class FindingTarget(BaseModel):
    model_config = ConfigDict(extra="forbid")

    scope: FindingScope
    lecture_id: str | None = None
    artifact_id: str | None = None
    slide_id: str | None = None
    content_block: str | None = None
    citation_index: int | None = Field(default=None, ge=0)
    source_id: str | None = None
    line_start: int | None = None
    line_end: int | None = None


class ValidationFinding(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str = Field(min_length=1, max_length=500)
    code: str = Field(min_length=1, max_length=100)
    severity: FindingSeverity
    message: str = Field(min_length=1, max_length=1000)
    target: FindingTarget
    waived: bool = False


class Waiver(BaseModel):
    """An explicit Course Author decision about one current warning."""

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    finding_id: str = Field(min_length=1, max_length=500)
    justification: str = Field(min_length=1, max_length=1000)


class InvalidWaiver(ValueError):
    """A Waiver is stale, duplicated, or targets a non-warning finding."""


class ReleaseValidationResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    selection: ReleaseSelection
    findings: list[ValidationFinding] = Field(default_factory=list)
    waivers: list[Waiver] = Field(default_factory=list)
    structurally_valid: bool
    can_publish: bool


def validate_release(
    *,
    plan: CoursePlan,
    presentations: list[Presentation],
    sources: SourcesIndex,
    selection: ReleaseSelection,
    waivers: list[Waiver] | None = None,
    evidence_line_counts: Mapping[str, int] | None = None,
) -> ReleaseValidationResult:
    """Validate a proposed partial Release without mutating Course state."""

    findings: list[ValidationFinding] = []
    lectures_by_id = {lecture.id: lecture for lecture in plan.lectures}
    presentations_by_id = {presentation.id: presentation for presentation in presentations}
    source_ids = {source.id for source in sources.sources}
    sources_by_id = {source.id: source for source in sources.sources}

    for lecture_id in selection.lecture_ids:
        if lecture_id not in lectures_by_id:
            code = "selection.missing-lecture"
            findings.append(
                ValidationFinding(
                    id=f"{code}:{lecture_id}",
                    code=code,
                    severity="error",
                    message=f"Selected Lecture {lecture_id} does not exist in the Course Plan.",
                    target=FindingTarget(scope="lecture", lecture_id=lecture_id),
                )
            )

    for artifact_id in selection.artifact_ids:
        presentation = presentations_by_id.get(artifact_id)
        if presentation is None:
            code = "selection.missing-artifact"
            findings.append(
                ValidationFinding(
                    id=f"{code}:{artifact_id}",
                    code=code,
                    severity="error",
                    message=(
                        f"Selected Artifact {artifact_id} does not exist in the Course Workspace."
                    ),
                    target=FindingTarget(scope="artifact", artifact_id=artifact_id),
                )
            )
            continue
        if presentation.lecture_id not in selection.lecture_ids:
            code = "selection.artifact-lecture-not-selected"
            findings.append(
                ValidationFinding(
                    id=f"{code}:{artifact_id}:{presentation.lecture_id}",
                    code=code,
                    severity="error",
                    message=(
                        f"Selected Artifact {artifact_id} belongs to Lecture "
                        f"{presentation.lecture_id}, which is not selected."
                    ),
                    target=FindingTarget(
                        scope="artifact",
                        lecture_id=presentation.lecture_id,
                        artifact_id=artifact_id,
                    ),
                )
            )
            continue
        lecture = lectures_by_id.get(presentation.lecture_id)
        if lecture is not None and lecture.presentation_id != presentation.id:
            code = "selection.presentation-not-attached"
            findings.append(
                ValidationFinding(
                    id=f"{code}:{presentation.lecture_id}:{presentation.id}",
                    code=code,
                    severity="error",
                    message=(
                        f"Selected Presentation {presentation.id} is not attached to "
                        f"Lecture {presentation.lecture_id} in the Course Plan."
                    ),
                    target=FindingTarget(
                        scope="artifact",
                        lecture_id=presentation.lecture_id,
                        artifact_id=presentation.id,
                    ),
                )
            )
        for slide in presentation.slides:
            if slide.archived:
                continue
            for citation_index, citation in enumerate(slide.citations):
                if citation.source_id not in source_ids:
                    code = "citation.missing-source"
                    message = (
                        f"Citation {citation_index + 1} on Slide {slide.id} references "
                        f"missing Source {citation.source_id}."
                    )
                    identity_suffix = f":{citation.source_id}"
                elif _citation_coordinates_are_invalid(citation):
                    code = "citation.invalid-coordinates"
                    message = (
                        f"Citation {citation_index + 1} on Slide {slide.id} has invalid "
                        "Evidence coordinates."
                    )
                    identity_suffix = ""
                elif _citation_cannot_be_resolved(
                    citation, sources_by_id[citation.source_id], evidence_line_counts
                ):
                    code = "citation.unresolvable-evidence"
                    message = (
                        f"Citation {citation_index + 1} on Slide {slide.id} does not resolve "
                        "against the admitted Source Version's extracted Evidence."
                    )
                    identity_suffix = ""
                else:
                    continue
                findings.append(
                    ValidationFinding(
                        id=(
                            f"{code}:{presentation.id}:{slide.id}:{citation_index}{identity_suffix}"
                        ),
                        code=code,
                        severity="error",
                        message=message,
                        target=FindingTarget(
                            scope="citation",
                            lecture_id=presentation.lecture_id,
                            artifact_id=presentation.id,
                            slide_id=slide.id,
                            citation_index=citation_index,
                            source_id=citation.source_id,
                            line_start=citation.line_start,
                            line_end=citation.line_end,
                        ),
                    )
                )
            if slide.citations or not _has_substantive_content(slide):
                continue
            if getattr(slide, "layout", "") == "quote":
                code = "grounding.uncited-quotation"
                message = f"Quotation on Slide {slide.id} has no supporting Citation."
                target = FindingTarget(
                    scope="content_block",
                    lecture_id=presentation.lecture_id,
                    artifact_id=presentation.id,
                    slide_id=slide.id,
                    content_block="quote",
                )
            else:
                code = "grounding.uncited-slide"
                message = f"Slide {slide.id} has substantive content but no supporting Citation."
                target = FindingTarget(
                    scope="slide",
                    lecture_id=presentation.lecture_id,
                    artifact_id=presentation.id,
                    slide_id=slide.id,
                )
            findings.append(
                ValidationFinding(
                    id=f"{code}:{presentation.id}:{slide.id}",
                    code=code,
                    severity="warning",
                    message=message,
                    target=target,
                )
            )

    applied_waivers = _apply_waivers(findings, waivers or [])
    structurally_valid = not any(finding.severity == "error" for finding in findings)

    return ReleaseValidationResult(
        selection=selection,
        findings=findings,
        waivers=applied_waivers,
        structurally_valid=structurally_valid,
        can_publish=structurally_valid
        and not any(finding.severity == "warning" and not finding.waived for finding in findings),
    )


def _apply_waivers(findings: list[ValidationFinding], waivers: list[Waiver]) -> list[Waiver]:
    waiver_ids = [waiver.finding_id for waiver in waivers]
    if len(waiver_ids) != len(set(waiver_ids)):
        raise InvalidWaiver("Each Validation Finding may be waived only once.")
    warnings_by_id = {finding.id: finding for finding in findings if finding.severity == "warning"}
    for waiver in waivers:
        finding = warnings_by_id.get(waiver.finding_id)
        if finding is None:
            raise InvalidWaiver(f"Waiver {waiver.finding_id} does not match a current warning.")
        finding.waived = True
    return list(waivers)


def _has_substantive_content(slide: object) -> bool:
    layout = getattr(slide, "layout", "")
    if layout == "bullets":
        return any(str(item).strip() for item in getattr(slide, "bullets", []))
    if layout == "two_column":
        return bool(
            str(getattr(slide, "left_content", "") or "").strip()
            or str(getattr(slide, "right_content", "") or "").strip()
        )
    if layout == "big_statement":
        return bool(str(getattr(slide, "statement", "") or "").strip())
    if layout == "quote":
        return bool(str(getattr(slide, "quote", "") or "").strip())
    if layout == "code":
        return bool(str(getattr(slide, "code", "") or "").strip())
    if layout == "closing":
        return bool(str(getattr(slide, "text", "") or "").strip())
    if layout == "image":
        # An image may be purely decorative; warn only when the Course Author
        # has attached a substantive caption that can make a factual claim.
        return bool(str(getattr(slide, "caption", "") or "").strip())
    return False


def _citation_coordinates_are_invalid(citation: object) -> bool:
    line_start = getattr(citation, "line_start", None)
    line_end = getattr(citation, "line_end", None)
    if line_start is not None and line_start < 0:
        return True
    if line_end is not None and line_end < 0:
        return True
    if line_end is not None and line_start is None:
        return True
    return line_start is not None and line_end is not None and line_end < line_start


def _citation_cannot_be_resolved(
    citation: object, source: object, evidence_line_counts: Mapping[str, int] | None
) -> bool:
    """Check inclusive Evidence coordinates when an exact extracted catalog is available."""
    if evidence_line_counts is None:
        return False
    line_start = getattr(citation, "line_start", None)
    line_end = getattr(citation, "line_end", None)
    if line_start is None and line_end is None:
        return False
    line_count = evidence_line_counts.get(getattr(source, "source_version_id", ""))
    if not isinstance(line_count, int) or isinstance(line_count, bool) or line_count < 0:
        return True
    # Coordinates are zero-based and inclusive, matching the Citation display
    # and search result convention.  A single-line citation has equal bounds.
    return (
        line_start is None
        or line_start >= line_count
        or (line_end is not None and line_end >= line_count)
    )
