"""Estimate whether Slide text fits a template placeholder, without font files.

The estimate is a deliberate heuristic: an average glyph is about half an em wide
(six tenths for monospace), lines are 1.2 em apart and every paragraph adds a little
spacing before it. It is conservative enough to catch real overflow and cheap enough
to run on every Slide mutation.
"""

from __future__ import annotations

import math
from dataclasses import dataclass

from pptx.enum.text import MSO_AUTO_SIZE
from pptx.oxml.ns import qn
from pptx.util import Emu, Pt

EMU_PER_POINT = 12_700
PROPORTIONAL_CHAR_WIDTH_EM = 0.52
MONOSPACE_CHAR_WIDTH_EM = 0.6
LINE_HEIGHT_EM = 1.2
PARAGRAPH_SPACING_EM = 0.2
HORIZONTAL_INSET_PT = 14.4  # python-pptx/PowerPoint default: 0.1in on each side
VERTICAL_INSET_PT = 7.2  # 0.05in on top and bottom
BULLET_INDENT_PT = 27.0  # default level-one bullet margin (0.375in)
MIN_FONT_SIZE_PT = 12.0
MIN_FONT_SCALE = 0.6
DEFAULT_FONT_SIZE_PT = 18.0
_TITLE_TYPES = {1, 3}  # TITLE, CENTER_TITLE


@dataclass(frozen=True)
class TextSlot:
    """A placeholder's text area in points, with the font size it inherits."""

    width_pt: float
    height_pt: float
    font_size_pt: float


def min_font_size(template_size_pt: float) -> float:
    """The smallest size fitting may shrink to: ~60% of the template size, never under 12pt."""
    return min(template_size_pt, max(MIN_FONT_SIZE_PT, round(template_size_pt * MIN_FONT_SCALE)))


def fit_estimate(
    slot: TextSlot,
    paragraphs: list[str],
    *,
    font_size_pt: float | None = None,
    monospace: bool = False,
    indent_pt: float = 0.0,
) -> float:
    """Return the estimated text height divided by the available height.

    A ratio at or below 1.0 means the text is expected to fit.
    """
    size = font_size_pt or slot.font_size_pt
    usable_width = max(slot.width_pt - HORIZONTAL_INSET_PT - indent_pt, size)
    usable_height = max(slot.height_pt - VERTICAL_INSET_PT, 1.0)
    char_width = size * (MONOSPACE_CHAR_WIDTH_EM if monospace else PROPORTIONAL_CHAR_WIDTH_EM)
    chars_per_line = max(1, int(usable_width / char_width))
    lines = sum(_wrapped_line_count(text, chars_per_line, monospace) for text in paragraphs)
    spacing = max(len(paragraphs) - 1, 0) * PARAGRAPH_SPACING_EM * size
    return (lines * LINE_HEIGHT_EM * size + spacing) / usable_height


def fitted_font_size(
    slot: TextSlot,
    paragraphs: list[str],
    *,
    monospace: bool = False,
    indent_pt: float = 0.0,
) -> float | None:
    """Return a reduced font size that fits, the floor if nothing fits, or None if no change."""
    options = {"monospace": monospace, "indent_pt": indent_pt}
    if fit_estimate(slot, paragraphs, **options) <= 1.0:
        return None
    floor = min_font_size(slot.font_size_pt)
    size = math.floor(slot.font_size_pt) - 1.0
    while size > floor:
        if fit_estimate(slot, paragraphs, font_size_pt=size, **options) <= 1.0:
            return size
        size -= 1.0
    return floor if floor < slot.font_size_pt else None


def _wrapped_line_count(text: str, chars_per_line: int, monospace: bool) -> int:
    total = 0
    for line in text.replace("\v", "\n").split("\n"):
        if monospace or not line.strip():
            total += max(1, math.ceil(len(line) / chars_per_line))
            continue
        count, current = 1, 0
        for word in line.split():
            length = len(word)
            if current == 0:
                count += (length - 1) // chars_per_line
                current = length % chars_per_line or chars_per_line
            elif current + 1 + length <= chars_per_line:
                current += 1 + length
            else:
                count += 1 + (length - 1) // chars_per_line
                current = length % chars_per_line or chars_per_line
        total += count
    return total


def placeholder_font_size(placeholder) -> float:
    """Resolve the level-one font size a slide placeholder inherits from its template."""
    shape = placeholder
    while shape is not None:
        size = _explicit_level_one_size(shape._element)
        if size is not None:
            return size
        shape = getattr(shape, "_base_placeholder", None)
    try:
        placeholder_type = int(placeholder.placeholder_format.type)
    except Exception:
        placeholder_type = 0
    style = "titleStyle" if placeholder_type in _TITLE_TYPES else "bodyStyle"
    master = _slide_master(placeholder)
    if master is not None:
        sizes = master._element.xpath(f"./p:txStyles/p:{style}/a:lvl1pPr/a:defRPr/@sz")
        if sizes:
            return int(sizes[0]) / 100
    return DEFAULT_FONT_SIZE_PT


def _explicit_level_one_size(element) -> float | None:
    sizes = element.xpath("./p:txBody/a:lstStyle/a:lvl1pPr/a:defRPr/@sz")
    return int(sizes[0]) / 100 if sizes else None


def _slide_master(placeholder):
    part = getattr(placeholder, "part", None)
    slide = getattr(part, "slide", None) or getattr(part, "slide_layout", None)
    layout = getattr(slide, "slide_layout", slide)
    return getattr(layout, "slide_master", None)


def placeholder_slot(placeholder, *, height_emu: int | None = None) -> TextSlot:
    height = placeholder.height if height_emu is None else height_emu
    return TextSlot(
        width_pt=Emu(placeholder.width).pt,
        height_pt=Emu(height).pt,
        font_size_pt=placeholder_font_size(placeholder),
    )


def fit_placeholder_text(
    placeholder,
    *,
    monospace: bool = False,
    bulleted: bool = False,
    height_emu: int | None = None,
) -> float | None:
    """Shrink a populated placeholder's runs until the text is expected to fit.

    It also turns on word wrap and shrink-on-overflow so PowerPoint and LibreOffice
    can shrink further. Returns the applied font size, or None when the template size fits.
    """
    text_frame = placeholder.text_frame
    text_frame.word_wrap = True
    text_frame.auto_size = MSO_AUTO_SIZE.TEXT_TO_FIT_SHAPE
    paragraphs = [paragraph.text for paragraph in text_frame.paragraphs]
    if not any(text.strip() for text in paragraphs):
        return None
    size = fitted_font_size(
        placeholder_slot(placeholder, height_emu=height_emu),
        paragraphs,
        monospace=monospace,
        indent_pt=BULLET_INDENT_PT if bulleted else 0.0,
    )
    if size is None:
        return None
    for paragraph in text_frame.paragraphs:
        for run in paragraph.runs:
            run.font.size = Pt(size)
        end = paragraph._p.find(qn("a:endParaRPr"))
        if end is not None:
            end.set("sz", str(int(size * 100)))
    return size
