"""Slide previews take their text colours from the template, or from the rendered background."""

from __future__ import annotations

from pathlib import Path

import pytest
from PIL import Image
from pptx import Presentation as PPTXPresentation
from pptx.oxml import parse_xml

from course_harness.core.presentation import BulletsSlide, Presentation
from course_harness.core.slide_preview import PreviewContext, PreviewSlot, build_preview
from course_harness.core.template_inspect import inspect_template
from course_harness.core.template_profiles import TemplateLayoutMapping, TemplateProfile

A = "http://schemas.openxmlformats.org/drawingml/2006/main"
P = "http://schemas.openxmlformats.org/presentationml/2006/main"
NS = {"a": A, "p": P}
TITLE_AND_CONTENT = 1


def _fill(color_xml: str):
    return parse_xml(f'<a:solidFill xmlns:a="{A}">{color_xml}</a:solidFill>')


def _set_text_style_fill(presentation, style: str, color_xml: str | None) -> None:
    """Set or remove the first-level text colour of a master text style."""
    default_run = presentation.slide_master.element.find(
        f"p:txStyles/p:{style}/a:lvl1pPr/a:defRPr", NS
    )
    for fill in default_run.findall("a:solidFill", NS):
        default_run.remove(fill)
    if color_xml is not None:
        default_run.insert(0, _fill(color_xml))


def _dark_template(path: Path) -> Path:
    """A dark theme as PowerPoint makes one: the master's colour map swaps light and dark, so
    the background (bg1) is the theme's black and the text (tx1) its white."""
    presentation = PPTXPresentation()
    color_map = presentation.slide_master.element.find("p:clrMap", NS)
    color_map.set("bg1", "dk1")
    color_map.set("tx1", "lt1")
    color_map.set("bg2", "dk2")
    color_map.set("tx2", "lt2")
    presentation.save(str(path))
    return path


def _uncoloured_template(path: Path) -> Path:
    """A template that leaves the colour of its title and body text unset."""
    presentation = PPTXPresentation()
    _set_text_style_fill(presentation, "titleStyle", None)
    _set_text_style_fill(presentation, "bodyStyle", None)
    presentation.save(str(path))
    return path


def _bullets_profile(template_path: Path) -> TemplateProfile:
    inspection = inspect_template(template_path)
    return TemplateProfile(
        id="tpl-000000000001",
        name="Colour template",
        version=1,
        template_filename=template_path.name,
        slide_width=inspection["slide_width"],
        slide_height=inspection["slide_height"],
        slide_count=inspection["slide_count"],
        layouts=[
            TemplateLayoutMapping(
                semantic_layout="bullets",
                template_layout_index=TITLE_AND_CONTENT,
                confidence=1,
                rationale="Test mapping",
                slot_mappings={"title": 0, "body": 1},
            )
        ],
    )


def _bullets_presentation() -> Presentation:
    return Presentation(
        id="presentation-abc123def456",
        lecture_id="lecture-abc123def456",
        slides=[BulletsSlide(id="slide-abc123def456", title="Colours", bullets=["One"])],
    )


def _preview_slots(template_path: Path, cache_dir: Path) -> dict[str, PreviewSlot]:
    context = PreviewContext(_bullets_profile(template_path), template_path, cache_dir)
    return build_preview(_bullets_presentation(), context).slides[0].slots


def _layout_colors(template_path: Path, layout_index: int) -> dict[int, str | None]:
    layout = inspect_template(template_path)["layouts"][layout_index]
    return {placeholder["idx"]: placeholder["color"] for placeholder in layout["placeholders"]}


def _write_background(cache_dir: Path, color: tuple[int, int, int]) -> None:
    target = cache_dir / "tpl-000000000001" / "backgrounds" / "v1" / "bullets.png"
    target.parent.mkdir(parents=True)
    Image.new("RGB", (160, 90), color).save(target)


def test_a_dark_template_gives_white_text_through_the_colour_map(tmp_path: Path) -> None:
    template = _dark_template(tmp_path / "dark.pptx")

    slots = _preview_slots(template, tmp_path / "cache")

    assert slots["title"].color == "#FFFFFF"
    assert slots["body"].color == "#FFFFFF"
    # The subtitle's own tint of tx1 stays white too.
    assert _layout_colors(template, 0)[1] == "#FFFFFF"


def test_a_light_template_gives_its_dark_theme_text_colour(tmp_path: Path) -> None:
    template = tmp_path / "light.pptx"
    PPTXPresentation().save(str(template))

    slots = _preview_slots(template, tmp_path / "cache")

    assert slots["title"].color == "#000000"
    assert slots["body"].color == "#000000"
    # The default subtitle is the text colour tinted to 75%.
    assert _layout_colors(template, 0)[1] == "#404040"


def test_luminance_modifiers_and_a_layout_colour_map_override_apply(tmp_path: Path) -> None:
    presentation = PPTXPresentation()
    _set_text_style_fill(
        presentation, "titleStyle", '<a:schemeClr val="bg1"><a:lumMod val="75000"/></a:schemeClr>'
    )
    layout = presentation.slide_layouts[TITLE_AND_CONTENT].element
    override = layout.find("p:clrMapOvr", NS)
    override.getparent().replace(
        override,
        parse_xml(
            f'<p:clrMapOvr xmlns:p="{P}" xmlns:a="{A}"><a:overrideClrMapping bg1="dk1" '
            'tx1="lt1" bg2="dk2" tx2="lt2" accent1="accent1" accent2="accent2" '
            'accent3="accent3" accent4="accent4" accent5="accent5" accent6="accent6" '
            'hlink="hlink" folHlink="folHlink"/></p:clrMapOvr>'
        ),
    )
    template = tmp_path / "modified.pptx"
    presentation.save(str(template))

    assert _layout_colors(template, 0)[0] == "#BFBFBF"
    # The layout swaps light and dark, so its title's bg1 is now the theme's black and its body
    # text (tx1) white.
    colors = _layout_colors(template, TITLE_AND_CONTENT)
    assert colors[0] == "#000000"
    assert colors[1] == "#FFFFFF"


def test_a_placeholder_list_style_colour_wins_over_the_master(tmp_path: Path) -> None:
    presentation = PPTXPresentation()
    title = presentation.slide_layouts[TITLE_AND_CONTENT].placeholders[0].element
    body = title.find("p:txBody", NS)
    list_style = body.find("a:lstStyle", NS)
    list_style.append(
        parse_xml(
            f'<a:lvl1pPr xmlns:a="{A}"><a:defRPr><a:solidFill><a:srgbClr val="c00000"/>'
            "</a:solidFill></a:defRPr></a:lvl1pPr>"
        )
    )
    template = tmp_path / "red-title.pptx"
    presentation.save(str(template))

    assert _layout_colors(template, TITLE_AND_CONTENT)[0] == "#C00000"


@pytest.mark.parametrize(
    ("background", "expected"),
    [((20, 24, 40), "#FFFFFF"), ((245, 240, 230), "#000000")],
)
def test_without_a_template_colour_text_contrasts_with_the_rendered_background(
    tmp_path: Path, background: tuple[int, int, int], expected: str
) -> None:
    template = _uncoloured_template(tmp_path / "uncoloured.pptx")
    assert _layout_colors(template, TITLE_AND_CONTENT)[0] is None
    cache_dir = tmp_path / "cache"
    _write_background(cache_dir, background)

    slots = _preview_slots(template, cache_dir)

    assert slots["title"].color == expected
    assert slots["body"].color == expected


def test_without_a_template_colour_or_background_the_colour_stays_unset(tmp_path: Path) -> None:
    template = _uncoloured_template(tmp_path / "uncoloured.pptx")

    slots = _preview_slots(template, tmp_path / "cache")

    assert slots["title"].color is None


def test_slot_geometry_saved_before_colours_still_reads() -> None:
    slot = PreviewSlot.model_validate(
        {
            "left": 0.1,
            "top": 0.1,
            "width": 0.8,
            "height": 0.2,
            "font_family": "Aptos",
            "font_size": 32,
            "bold": False,
            "alignment": None,
        }
    )

    assert slot.color is None
