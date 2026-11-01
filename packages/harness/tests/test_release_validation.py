import pytest
from pydantic import ValidationError

from course_harness.course_plan import CoursePlan, Lecture
from course_harness.presentation import (
    BulletsSlide,
    ClosingSlide,
    CodeSlide,
    ImageSlide,
    Presentation,
    QuoteSlide,
    SlideCitation,
)
from course_harness.release_validation import (
    InvalidWaiver,
    ReleaseSelection,
    Waiver,
    validate_release,
)
from course_harness.sources import Source, SourcesIndex


def test_missing_selected_lecture_is_a_blocking_structural_finding() -> None:
    plan = CoursePlan(
        id="course-aaaaaaaaaaaa",
        title="Causal inference",
        audience="Graduate students",
        lectures=[Lecture(id="lecture-aaaaaaaaaaaa", title="Foundations")],
    )

    result = validate_release(
        plan=plan,
        presentations=[],
        sources=SourcesIndex(),
        selection=ReleaseSelection(lecture_ids=["lecture-bbbbbbbbbbbb"]),
    )

    assert result.can_publish is False
    assert [finding.model_dump() for finding in result.findings] == [
        {
            "id": "selection.missing-lecture:lecture-bbbbbbbbbbbb",
            "code": "selection.missing-lecture",
            "severity": "error",
            "message": "Selected Lecture lecture-bbbbbbbbbbbb does not exist in the Course Plan.",
            "target": {
                "scope": "lecture",
                "lecture_id": "lecture-bbbbbbbbbbbb",
                "artifact_id": None,
                "slide_id": None,
                "content_block": None,
                "citation_index": None,
                "source_id": None,
                "line_start": None,
                "line_end": None,
            },
            "waived": False,
        }
    ]


def test_uncited_substantive_slide_emits_one_slide_level_warning_not_one_per_bullet() -> None:
    lecture = Lecture(
        id="lecture-aaaaaaaaaaaa",
        title="Foundations",
        presentation_id="presentation-aaaaaaaaaaaa",
    )
    plan = CoursePlan(
        id="course-aaaaaaaaaaaa",
        title="Causal inference",
        audience="Graduate students",
        lectures=[lecture],
    )
    presentation = Presentation(
        id="presentation-aaaaaaaaaaaa",
        lecture_id=lecture.id,
        slides=[
            BulletsSlide(
                id="slide-aaaaaaaaaaaa",
                title="Core assumptions",
                bullets=["Consistency", "Exchangeability", "Positivity"],
            )
        ],
    )

    result = validate_release(
        plan=plan,
        presentations=[presentation],
        sources=SourcesIndex(),
        selection=ReleaseSelection(lecture_ids=[lecture.id], artifact_ids=[presentation.id]),
    )

    assert result.structurally_valid is True
    assert result.can_publish is False
    assert len(result.findings) == 1
    finding = result.findings[0]
    assert finding.code == "grounding.uncited-slide"
    assert finding.severity == "warning"
    assert finding.target.scope == "slide"
    assert finding.target.slide_id == "slide-aaaaaaaaaaaa"


def test_selected_artifact_must_exist_and_belong_to_a_selected_lecture() -> None:
    lecture = Lecture(id="lecture-aaaaaaaaaaaa", title="Foundations")
    other_lecture = Lecture(id="lecture-bbbbbbbbbbbb", title="Applications")
    plan = CoursePlan(
        id="course-aaaaaaaaaaaa",
        title="Causal inference",
        audience="Graduate students",
        lectures=[lecture, other_lecture],
    )
    presentation = Presentation(id="presentation-aaaaaaaaaaaa", lecture_id=other_lecture.id)

    result = validate_release(
        plan=plan,
        presentations=[presentation],
        sources=SourcesIndex(),
        selection=ReleaseSelection(
            lecture_ids=[lecture.id],
            artifact_ids=[presentation.id, "presentation-bbbbbbbbbbbb"],
        ),
    )

    assert result.structurally_valid is False
    assert [finding.code for finding in result.findings] == [
        "selection.artifact-lecture-not-selected",
        "selection.missing-artifact",
    ]


def test_broken_citation_references_are_errors_but_one_valid_citation_grounds_the_slide() -> None:
    source = Source(
        id="source-aaaaaaaaaaaa",
        resource_id="resource-aaaaaaaaaaaa",
        source_version_id="a" * 64,
        label="Causal Foundations",
        admitted_at="2026-09-14T00:00:00+00:00",
    )
    lecture = Lecture(
        id="lecture-aaaaaaaaaaaa",
        title="Foundations",
        presentation_id="presentation-aaaaaaaaaaaa",
    )
    plan = CoursePlan(
        id="course-aaaaaaaaaaaa",
        title="Causal inference",
        audience="Graduate students",
        lectures=[lecture],
    )
    presentation = Presentation(
        id="presentation-aaaaaaaaaaaa",
        lecture_id=lecture.id,
        slides=[
            BulletsSlide(
                id="slide-aaaaaaaaaaaa",
                bullets=["Consistency"],
                citations=[SlideCitation(source_id=source.id, label="Foundations", line_start=8)],
            ),
            BulletsSlide(
                id="slide-bbbbbbbbbbbb",
                bullets=["Exchangeability"],
                citations=[
                    SlideCitation(source_id="source-bbbbbbbbbbbb", label="Missing", line_start=2)
                ],
            ),
        ],
    )

    result = validate_release(
        plan=plan,
        presentations=[presentation],
        sources=SourcesIndex(sources=[source]),
        selection=ReleaseSelection(lecture_ids=[lecture.id], artifact_ids=[presentation.id]),
    )

    assert [finding.code for finding in result.findings] == ["citation.missing-source"]
    assert result.findings[0].target.scope == "citation"
    assert result.findings[0].target.citation_index == 0
    assert result.structurally_valid is False


def test_direct_quote_gets_a_content_block_warning_while_navigational_slides_do_not() -> None:
    lecture = Lecture(
        id="lecture-aaaaaaaaaaaa",
        title="Foundations",
        presentation_id="presentation-aaaaaaaaaaaa",
    )
    plan = CoursePlan(
        id="course-aaaaaaaaaaaa",
        title="Causal inference",
        audience="Graduate students",
        lectures=[lecture],
    )
    presentation = Presentation(
        id="presentation-aaaaaaaaaaaa",
        lecture_id=lecture.id,
        slides=[
            QuoteSlide(id="slide-aaaaaaaaaaaa", quote="Correlation is not causation."),
            BulletsSlide(id="slide-bbbbbbbbbbbb", title="Questions", bullets=[]),
        ],
    )

    result = validate_release(
        plan=plan,
        presentations=[presentation],
        sources=SourcesIndex(),
        selection=ReleaseSelection(lecture_ids=[lecture.id], artifact_ids=[presentation.id]),
    )

    assert len(result.findings) == 1
    assert result.findings[0].code == "grounding.uncited-quotation"
    assert result.findings[0].target.scope == "content_block"
    assert result.findings[0].target.content_block == "quote"


def test_other_substantive_blocks_warn_without_treating_decorative_images_as_claims() -> None:
    lecture = Lecture(
        id="lecture-aaaaaaaaaaaa",
        title="Applications",
        presentation_id="presentation-aaaaaaaaaaaa",
    )
    plan = CoursePlan(
        id="course-aaaaaaaaaaaa",
        title="Causal inference",
        audience="Graduate students",
        lectures=[lecture],
    )
    presentation = Presentation(
        id="presentation-aaaaaaaaaaaa",
        lecture_id=lecture.id,
        slides=[
            CodeSlide(id="slide-aaaaaaaaaaaa", code="estimate = treated - control"),
            ClosingSlide(id="slide-bbbbbbbbbbbb", text="The intervention improved outcomes."),
            ImageSlide(id="slide-cccccccccccc", image_url="decorative.png"),
            ImageSlide(
                id="slide-dddddddddddd",
                image_url="result.png",
                caption="Treatment effects increased over time.",
            ),
        ],
    )

    result = validate_release(
        plan=plan,
        presentations=[presentation],
        sources=SourcesIndex(),
        selection=ReleaseSelection(lecture_ids=[lecture.id], artifact_ids=[presentation.id]),
    )

    assert [finding.target.slide_id for finding in result.findings] == [
        "slide-aaaaaaaaaaaa",
        "slide-bbbbbbbbbbbb",
        "slide-dddddddddddd",
    ]


def test_warning_can_be_waived_only_by_its_current_finding_id() -> None:
    lecture = Lecture(
        id="lecture-aaaaaaaaaaaa",
        title="Foundations",
        presentation_id="presentation-aaaaaaaaaaaa",
    )
    plan = CoursePlan(
        id="course-aaaaaaaaaaaa",
        title="Causal inference",
        audience="Graduate students",
        lectures=[lecture],
    )
    presentation = Presentation(
        id="presentation-aaaaaaaaaaaa",
        lecture_id=lecture.id,
        slides=[BulletsSlide(id="slide-aaaaaaaaaaaa", bullets=["Consistency"])],
    )
    selection = ReleaseSelection(lecture_ids=[lecture.id], artifact_ids=[presentation.id])
    first = validate_release(
        plan=plan,
        presentations=[presentation],
        sources=SourcesIndex(),
        selection=selection,
    )

    result = validate_release(
        plan=plan,
        presentations=[presentation],
        sources=SourcesIndex(),
        selection=selection,
        waivers=[Waiver(finding_id=first.findings[0].id, justification="Original analysis")],
    )

    assert result.findings[0].waived is True
    assert result.waivers == [
        Waiver(finding_id=first.findings[0].id, justification="Original analysis")
    ]
    assert result.can_publish is True

    with pytest.raises(InvalidWaiver, match="does not match a current warning"):
        validate_release(
            plan=plan,
            presentations=[presentation],
            sources=SourcesIndex(),
            selection=selection,
            waivers=[Waiver(finding_id="stale-finding", justification="Accepted")],
        )


def test_selection_and_waiver_models_reject_ambiguous_records() -> None:
    with pytest.raises(ValidationError, match="Selected Lecture IDs must be unique"):
        ReleaseSelection(lecture_ids=["lecture-aaaaaaaaaaaa", "lecture-aaaaaaaaaaaa"])

    with pytest.raises(ValidationError):
        ReleaseSelection(lecture_ids=["lecture-" + "a" * 1_000])

    with pytest.raises(ValidationError):
        Waiver(finding_id="finding", justification="   ")


def test_selected_presentation_must_match_the_course_plan_and_citation_coordinates() -> None:
    source = Source(
        id="source-aaaaaaaaaaaa",
        resource_id="resource-aaaaaaaaaaaa",
        source_version_id="a" * 64,
        label="Foundations",
        admitted_at="2026-09-14T00:00:00+00:00",
    )
    lecture = Lecture(id="lecture-aaaaaaaaaaaa", title="Foundations")
    plan = CoursePlan(
        id="course-aaaaaaaaaaaa",
        title="Causal inference",
        audience="Graduate students",
        lectures=[lecture],
    )
    presentation = Presentation(
        id="presentation-aaaaaaaaaaaa",
        lecture_id=lecture.id,
        slides=[
            BulletsSlide(
                id="slide-aaaaaaaaaaaa",
                bullets=["Consistency"],
                citations=[
                    SlideCitation(
                        source_id=source.id,
                        label="Foundations",
                        line_start=12,
                        line_end=4,
                    )
                ],
            )
        ],
    )

    result = validate_release(
        plan=plan,
        presentations=[presentation],
        sources=SourcesIndex(sources=[source]),
        selection=ReleaseSelection(lecture_ids=[lecture.id], artifact_ids=[presentation.id]),
    )

    assert [finding.code for finding in result.findings] == [
        "selection.presentation-not-attached",
        "citation.invalid-coordinates",
    ]


def test_evidence_catalog_enforces_inclusive_citation_coordinates_and_targets_evidence() -> None:
    source = Source(
        id="source-aaaaaaaaaaaa",
        resource_id="resource-aaaaaaaaaaaa",
        source_version_id="a" * 64,
        label="Foundations",
        admitted_at="2026-09-14T00:00:00+00:00",
    )
    lecture = Lecture(
        id="lecture-aaaaaaaaaaaa",
        title="Foundations",
        presentation_id="presentation-aaaaaaaaaaaa",
    )
    plan = CoursePlan(
        id="course-aaaaaaaaaaaa",
        title="Causal inference",
        audience="Graduate students",
        lectures=[lecture],
    )
    selection = ReleaseSelection(
        lecture_ids=[lecture.id], artifact_ids=["presentation-aaaaaaaaaaaa"]
    )

    valid = validate_release(
        plan=plan,
        presentations=[
            Presentation(
                id="presentation-aaaaaaaaaaaa",
                lecture_id=lecture.id,
                slides=[
                    BulletsSlide(
                        id="slide-aaaaaaaaaaaa",
                        bullets=["Consistency"],
                        citations=[
                            SlideCitation(
                                source_id=source.id,
                                label="Foundations",
                                line_start=2,
                                line_end=2,
                            )
                        ],
                    )
                ],
            )
        ],
        sources=SourcesIndex(sources=[source]),
        selection=selection,
        evidence_line_counts={source.source_version_id: 3},
    )

    assert valid.can_publish is True

    invalid = validate_release(
        plan=plan,
        presentations=[
            Presentation(
                id="presentation-aaaaaaaaaaaa",
                lecture_id=lecture.id,
                slides=[
                    BulletsSlide(
                        id="slide-aaaaaaaaaaaa",
                        bullets=["Consistency"],
                        citations=[
                            SlideCitation(
                                source_id=source.id,
                                label="Foundations",
                                line_start=3,
                            )
                        ],
                    )
                ],
            )
        ],
        sources=SourcesIndex(sources=[source]),
        selection=selection,
        evidence_line_counts={source.source_version_id: 3},
    )

    assert invalid.can_publish is False
    assert invalid.findings[0].code == "citation.unresolvable-evidence"
    assert invalid.findings[0].target.source_id == source.id
    assert invalid.findings[0].target.line_start == 3
    assert invalid.findings[0].target.line_end is None


def test_evidence_catalog_requires_extracted_evidence_for_coordinate_citations() -> None:
    source = Source(
        id="source-aaaaaaaaaaaa",
        resource_id="resource-aaaaaaaaaaaa",
        source_version_id="a" * 64,
        label="Foundations",
        admitted_at="2026-09-14T00:00:00+00:00",
    )
    lecture = Lecture(
        id="lecture-aaaaaaaaaaaa",
        title="Foundations",
        presentation_id="presentation-aaaaaaaaaaaa",
    )
    plan = CoursePlan(
        id="course-aaaaaaaaaaaa",
        title="Causal inference",
        audience="Graduate students",
        lectures=[lecture],
    )
    presentation = Presentation(
        id="presentation-aaaaaaaaaaaa",
        lecture_id=lecture.id,
        slides=[
            BulletsSlide(
                id="slide-aaaaaaaaaaaa",
                bullets=["Consistency"],
                citations=[SlideCitation(source_id=source.id, label="Foundations", line_start=0)],
            )
        ],
    )

    result = validate_release(
        plan=plan,
        presentations=[presentation],
        sources=SourcesIndex(sources=[source]),
        selection=ReleaseSelection(lecture_ids=[lecture.id], artifact_ids=[presentation.id]),
        evidence_line_counts={},
    )

    assert [finding.code for finding in result.findings] == ["citation.unresolvable-evidence"]
