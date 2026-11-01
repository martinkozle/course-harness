"""The slide linter reports what will not fit the template, without rendering."""

import shutil

import pytest

from course_harness.presentation import (
    BulletsSlide,
    CodeSlide,
    TitleSlide,
    TwoColumnSlide,
)
from course_harness.slide_lint import format_findings, lint_slides

LONG_BULLET = (
    "Every host application needs its own custom integration for every tool it wants "
    "to call, which multiplies the maintenance work for everyone"
)


def _codes(findings) -> set[tuple[str, str]]:
    return {(finding.code, finding.field) for finding in findings}


def test_slides_that_fit_have_no_findings() -> None:
    findings = lint_slides(
        [
            TitleSlide(id="slide-000000000001", title="Intro to MCP", subtitle="Why it exists"),
            BulletsSlide(
                id="slide-000000000002",
                title="The problem",
                bullets=["Custom integrations everywhere", "N×M maintenance"],
            ),
        ]
    )

    assert findings == []
    assert format_findings(findings) == "Layout check: every Slide fits its template."


def test_a_crammed_bullets_slide_reports_overflow_count_and_long_items(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(shutil, "which", lambda _name: None)  # no LibreOffice
    slide = BulletsSlide(id="slide-0000000000ab", title="The problem", bullets=[LONG_BULLET] * 12)

    findings = lint_slides([slide])

    assert {
        ("overflow", "bullets"),
        ("too-many-items", "bullets"),
        ("long-item", "bullets"),
    } <= _codes(findings)
    overflow = next(f for f in findings if f.code == "overflow")
    assert str(overflow).startswith("slide-0000000000ab bullets ~")
    assert "% of box even at min size — cut to ~" in str(overflow)
    assert "words or split" in str(overflow)
    long_items = next(f for f in findings if f.code == "long-item")
    assert long_items.message.startswith("items 1, 2, 3,")


def test_empty_required_slots_and_long_titles_are_reported() -> None:
    findings = lint_slides(
        [
            TwoColumnSlide(
                id="slide-000000000003",
                title="A title that is much too long for any slide",
                left_content=["Before", "Slow builds"],
            ),
        ]
    )

    assert _codes(findings) == {("empty-slot", "right_content"), ("long-title", "title")}


def test_long_code_is_reported_in_lines() -> None:
    code = "\n".join(f"value_{i} = compute(value_{i - 1}, factor={i})" for i in range(40))

    findings = lint_slides([CodeSlide(id="slide-000000000004", title="Code", code=code)])

    assert ("long-code", "code") in _codes(findings)
    overflow = next(f for f in findings if f.code == "overflow")
    assert overflow.field == "code"
    assert "lines or split" in overflow.message


def test_archived_slides_are_not_checked() -> None:
    slide = BulletsSlide(id="slide-000000000005", bullets=[], archived=True)

    assert lint_slides([slide]) == []
