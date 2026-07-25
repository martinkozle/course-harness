from __future__ import annotations

from io import BytesIO

from pptx import Presentation as PPTXPresentation
from pptx.enum.text import PP_ALIGN
from pptx.util import Inches, Pt

from course_harness.presentation import (
    BigStatementSlide,
    BulletsSlide,
    ClosingSlide,
    CodeSlide,
    ImageSlide,
    Presentation,
    QuoteSlide,
    SectionSlide,
    SlideCitation,
    TitleSlide,
    TwoColumnSlide,
)

DEFAULT_LAYOUT_MAPPING: dict[str, int] = {
    "title": 0,
    "section": 2,
    "bullets": 1,
    "two_column": 3,
    "big_statement": 5,
    "closing": 2,
    "code": 1,
    "image": 8,
    "quote": 1,
}

TITLE_PH = 1
BODY_PH = 2
SUBTITLE_PH = 4
OBJECT_PH = 7

CITATION_FONT_SIZE = Pt(9)
CITATION_BOX_HEIGHT = Inches(0.45)
CITATION_BOX_TOP = Inches(7.0)
SLIDE_WIDTH = Inches(13.333)


class ExportError(Exception):
    pass


def export_presentation(presentation: Presentation) -> bytes:
    prs = PPTXPresentation()
    active_slides = [s for s in presentation.slides if not s.archived]

    for slide in active_slides:
        layout_index = DEFAULT_LAYOUT_MAPPING.get(slide.layout)
        if layout_index is None:
            raise ExportError(f"Unsupported slide layout: {slide.layout}")

        slide_layouts = prs.slide_layouts
        if layout_index >= len(slide_layouts):
            raise ExportError(
                f"Slide layout index {layout_index} is out of range "
                f"(only {len(slide_layouts)} layouts available)"
            )

        pptx_slide = prs.slides.add_slide(slide_layouts[layout_index])
        _populate_slide(slide, pptx_slide)
        _add_citations(slide, pptx_slide)
        _add_speaker_notes(slide, pptx_slide)

    buffer = BytesIO()
    prs.save(buffer)
    return buffer.getvalue()


def _ph_by_idx(pptx_slide, idx: int):
    for shape in pptx_slide.placeholders:
        if shape.placeholder_format.idx == idx:
            return shape
    return None


def _populate_slide(slide, pptx_slide) -> None:
    handlers = {
        "title": _populate_title_slide,
        "section": _populate_section_slide,
        "bullets": _populate_bullets_slide,
        "two_column": _populate_two_column_slide,
        "big_statement": _populate_big_statement_slide,
        "closing": _populate_closing_slide,
        "code": _populate_code_slide,
        "image": _populate_image_slide,
        "quote": _populate_quote_slide,
    }
    handler = handlers.get(slide.layout)
    if handler:
        handler(slide, pptx_slide)


def _populate_title_slide(slide: TitleSlide, pptx_slide) -> None:
    title_ph = _ph_by_idx(pptx_slide, 0)
    subtitle_ph = _ph_by_idx(pptx_slide, 1)
    if title_ph and slide.title:
        title_ph.text_frame.text = slide.title
    if subtitle_ph and slide.subtitle:
        subtitle_ph.text_frame.text = slide.subtitle


def _populate_section_slide(slide: SectionSlide, pptx_slide) -> None:
    title_ph = _ph_by_idx(pptx_slide, 0)
    subtitle_ph = _ph_by_idx(pptx_slide, 1)
    if title_ph and slide.title:
        title_ph.text_frame.text = slide.title
    if subtitle_ph:
        subtitle_ph.text_frame.text = ""


def _populate_bullets_slide(slide: BulletsSlide, pptx_slide) -> None:
    title_ph = _ph_by_idx(pptx_slide, 0)
    body_ph = _ph_by_idx(pptx_slide, 1)
    if title_ph and slide.title:
        title_ph.text_frame.text = slide.title
    if body_ph and slide.bullets:
        tf = body_ph.text_frame
        tf.clear()
        for i, bullet in enumerate(slide.bullets):
            p = tf.paragraphs[0] if i == 0 else tf.add_paragraph()
            p.text = bullet
            p.level = 0


def _populate_two_column_slide(slide: TwoColumnSlide, pptx_slide) -> None:
    title_ph = _ph_by_idx(pptx_slide, 0)
    if title_ph and slide.title:
        title_ph.text_frame.text = slide.title
    phs = [s for s in pptx_slide.placeholders if s.placeholder_format.type in (BODY_PH, OBJECT_PH)]
    phs.sort(key=lambda s: s.placeholder_format.idx)
    if len(phs) >= 1 and slide.left_content:
        phs[0].text_frame.text = slide.left_content
    if len(phs) >= 2 and slide.right_content:
        phs[1].text_frame.text = slide.right_content


def _populate_big_statement_slide(slide: BigStatementSlide, pptx_slide) -> None:
    title_ph = _ph_by_idx(pptx_slide, 0)
    if title_ph and slide.statement:
        title_ph.text_frame.text = slide.statement
        for paragraph in title_ph.text_frame.paragraphs:
            paragraph.alignment = PP_ALIGN.CENTER


def _populate_closing_slide(slide: ClosingSlide, pptx_slide) -> None:
    title_ph = _ph_by_idx(pptx_slide, 0)
    subtitle_ph = _ph_by_idx(pptx_slide, 1)
    if title_ph and slide.title:
        title_ph.text_frame.text = slide.title
    if subtitle_ph and slide.text:
        subtitle_ph.text_frame.text = slide.text


def _populate_code_slide(slide: CodeSlide, pptx_slide) -> None:
    title_ph = _ph_by_idx(pptx_slide, 0)
    body_ph = _ph_by_idx(pptx_slide, 1)
    if title_ph and slide.title:
        title_ph.text_frame.text = slide.title
    if body_ph and slide.code:
        tf = body_ph.text_frame
        tf.clear()
        header = slide.language or "code"
        p = tf.paragraphs[0]
        p.text = f"[{header}]"
        for line in slide.code.split("\n"):
            p = tf.add_paragraph()
            p.text = line
            for run in p.runs:
                run.font.name = "Courier New"


def _populate_image_slide(slide: ImageSlide, pptx_slide) -> None:
    title_ph = _ph_by_idx(pptx_slide, 0)
    if title_ph and slide.title:
        title_ph.text_frame.text = slide.title
    phs = [s for s in pptx_slide.placeholders if s.placeholder_format.type in (BODY_PH, OBJECT_PH)]
    phs.sort(key=lambda s: s.placeholder_format.idx)
    if phs and slide.caption:
        phs[0].text_frame.text = slide.caption
    elif phs and slide.image_url:
        phs[0].text_frame.text = slide.image_url


def _populate_quote_slide(slide: QuoteSlide, pptx_slide) -> None:
    title_ph = _ph_by_idx(pptx_slide, 0)
    body_ph = _ph_by_idx(pptx_slide, 1)
    if title_ph and slide.title:
        title_ph.text_frame.text = slide.title
    if body_ph:
        tf = body_ph.text_frame
        tf.clear()
        if slide.quote:
            p = tf.paragraphs[0]
            p.text = slide.quote
            p.alignment = PP_ALIGN.CENTER
        if slide.attribution:
            p = tf.add_paragraph()
            run = p.add_run()
            run.text = f"— {slide.attribution}"
            run.font.italic = True
            p.alignment = PP_ALIGN.CENTER


def _add_citations(slide, pptx_slide) -> None:
    if not slide.citations:
        return
    left = Inches(0.5)
    top = CITATION_BOX_TOP
    width = SLIDE_WIDTH - Inches(1.0)
    height = CITATION_BOX_HEIGHT
    textbox = pptx_slide.shapes.add_textbox(left, top, width, height)
    tf = textbox.text_frame
    tf.word_wrap = True
    p = tf.paragraphs[0]
    p.text = "Sources: "
    for i, citation in enumerate(slide.citations):
        if i > 0:
            p.add_run().text = ", "
        run = p.add_run()
        run.text = citation.label
        run.font.size = CITATION_FONT_SIZE
        if citation.url:
            run.hyperlink.address = citation.url
    for paragraph in tf.paragraphs:
        for run in paragraph.runs:
            run.font.size = CITATION_FONT_SIZE


def _add_speaker_notes(slide, pptx_slide) -> None:
    note_lines: list[str] = []
    if slide.speaker_notes:
        note_lines.append(slide.speaker_notes)
    if slide.citations:
        citation_lines = _citation_note_lines(slide.citations)
        if note_lines and citation_lines:
            note_lines.append("")
        note_lines.extend(citation_lines)
    if not note_lines:
        return
    try:
        notes = pptx_slide.notes_slide
        notes.notes_text_frame.text = "\n".join(note_lines)
    except Exception:
        pass


def _citation_note_lines(citations: list[SlideCitation]) -> list[str]:
    lines: list[str] = []
    for c in citations:
        parts = [c.label]
        if c.url:
            parts.append(c.url)
        if c.line_start is not None:
            parts.append(
                f"lines {c.line_start + 1}"
                + (f"–{c.line_end + 1}" if c.line_end is not None else "")
            )
        lines.append(" | ".join(parts))
    return lines
