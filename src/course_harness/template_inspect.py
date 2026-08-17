from __future__ import annotations

import re
import xml.etree.ElementTree as ET
import zipfile
from pathlib import Path
from typing import TYPE_CHECKING

from pptx import Presentation as PPTXPresentation
from pptx.enum.shapes import PP_PLACEHOLDER
from pptx.oxml.ns import qn

from course_harness.template_profiles import SEMANTIC_SLOTS

if TYPE_CHECKING:
    pass  # noqa: F811

TITLE_PH = PP_PLACEHOLDER.TITLE
CENTER_TITLE_PH = PP_PLACEHOLDER.CENTER_TITLE
BODY_PH = PP_PLACEHOLDER.BODY
SUBTITLE_PH = PP_PLACEHOLDER.SUBTITLE
OBJECT_PH = PP_PLACEHOLDER.OBJECT
PICTURE_PH = PP_PLACEHOLDER.PICTURE

_TITLE_LIKE = {int(TITLE_PH), int(CENTER_TITLE_PH)}

_A_NS = "http://schemas.openxmlformats.org/drawingml/2006/main"

SEMANTIC_KEYWORDS: dict[str, list[str]] = {
    "title": [
        "title slide",
        "title",
        "opening",
        "cover",
        "front",
        "titelseite",
        "titel",
    ],
    "section": [
        "section header",
        "section",
        "divider",
        "chapter",
        "separator",
        "transition",
        "abschnitt",
        "kapitel",
    ],
    "bullets": [
        "title and content",
        "content",
        "bullet",
        "body",
        "text",
        "list",
        "paragraph",
        "inhalt",
        "textfolie",
    ],
    "two_column": [
        "two content",
        "comparison",
        "two column",
        "left right",
        "dual",
        "side by side",
        "2 column",
        "zwei spalten",
        "vergleich",
    ],
    "big_statement": ["blank", "empty", "title only", "big statement", "statement", "leer"],
    "closing": [
        "closing",
        "end",
        "thank you",
        "final",
        "conclusion",
        "ending",
        "summary slide",
        "danke",
        "abschluss",
        "schlussfolie",
    ],
    "code": ["code", "monospace", "source code", "programming"],
    "image": [
        "picture",
        "image",
        "photo",
        "graphic",
        "content with caption",
        "media",
        "bild",
        "foto",
    ],
    "quote": ["quote", "testimonial", "pull quote", "citation", "zitat"],
}


def _extract_theme(pptx_path: Path) -> dict:
    try:
        with zipfile.ZipFile(pptx_path) as zf:
            theme_files = [
                n for n in zf.namelist() if n.startswith("ppt/theme/theme") and n.endswith(".xml")
            ]
            if not theme_files:
                return {"name": "", "colors": {}, "fonts": {}}
            theme_xml = zf.read(theme_files[0])
            root = ET.fromstring(theme_xml)
            theme_name_el = root.attrib.get("name", "")
            clr_scheme = root.find(f".//{{{_A_NS}}}clrScheme")
            colors: dict[str, str] = {}
            if clr_scheme is not None:
                for child in clr_scheme:
                    tag = child.tag.split("}")[-1]
                    srgb = child.find(f"{{{_A_NS}}}srgbClr")
                    if srgb is not None:
                        val = srgb.attrib.get("val", "")
                        if val:
                            colors[tag] = f"#{val}"
                    else:
                        sys_clr = child.find(f"{{{_A_NS}}}sysClr")
                        if sys_clr is not None:
                            val = sys_clr.attrib.get("lastClr", "")
                            if val:
                                colors[tag] = f"#{val}"
            fonts: dict[str, str] = {}
            font_scheme = root.find(f".//{{{_A_NS}}}fontScheme")
            if font_scheme is not None:
                for key, tag in (("major", "majorFont"), ("minor", "minorFont")):
                    family = font_scheme.find(f"{{{_A_NS}}}{tag}/{{{_A_NS}}}latin")
                    if family is not None and family.attrib.get("typeface"):
                        fonts[key] = family.attrib["typeface"]
            return {"name": theme_name_el, "colors": colors, "fonts": fonts}
    except Exception:
        return {"name": "", "colors": {}, "fonts": {}}


def _extract_masters(prs, theme: dict) -> list[dict]:
    masters = []
    for index, master in enumerate(prs.slide_masters):
        masters.append(
            {
                "index": index,
                "name": getattr(master, "name", "") or f"Master {index + 1}",
                "preserved": getattr(master, "preserved", False),
                "layout_count": len(master.slide_layouts),
                "placeholders": [
                    _inspect_placeholder(ph, _placeholder_typography(master, ph, theme))
                    for ph in master.placeholders
                ],
            }
        )
    return masters


def _placeholder_typography(container, placeholder, theme: dict) -> dict:
    try:
        placeholder_type = int(placeholder.placeholder_format.type)
    except Exception:
        placeholder_type = 0
    style_name = "titleStyle" if placeholder_type in _TITLE_LIKE else "bodyStyle"
    master = getattr(container, "slide_master", container)
    levels = master._element.xpath(f"./p:txStyles/p:{style_name}/a:lvl1pPr")
    level = levels[0] if levels else None
    default_run = level.find(qn("a:defRPr")) if level is not None else None
    size = None
    family = None
    bold = False
    if default_run is not None:
        raw_size = default_run.get("sz")
        size = int(raw_size) / 100 if raw_size else None
        bold = default_run.get("b") in {"1", "true"}
        latin = default_run.find(qn("a:latin"))
        family = latin.get("typeface") if latin is not None else None
    fonts = theme.get("fonts", {})
    if not family or family == "+mj-lt":
        family = fonts.get("major") if placeholder_type in _TITLE_LIKE else fonts.get("minor")
    elif family == "+mn-lt":
        family = fonts.get("minor")
    alignment = level.get("algn") if level is not None else None
    alignment = {"ctr": "center", "r": "right", "l": "left"}.get(alignment, alignment)
    return {
        "font_family": family,
        "font_size": size,
        "bold": bold,
        "alignment": alignment,
    }


def _inspect_placeholder(ph, inherited: dict | None = None) -> dict:
    try:
        ph_type = int(ph.placeholder_format.type) if ph.placeholder_format.type is not None else 0
    except Exception:
        ph_type = 0
    font_size = None
    font_family = None
    bold = False
    alignment = None
    try:
        paragraph = ph.text_frame.paragraphs[0]
        font_size = paragraph.font.size.pt if paragraph.font.size is not None else None
        font_family = paragraph.font.name
        bold = bool(paragraph.font.bold)
        alignment = (
            str(paragraph.alignment).split(".")[-1].lower()
            if paragraph.alignment is not None
            else None
        )
    except Exception:
        pass
    inherited = inherited or {}
    if font_size is None:
        font_size = inherited.get("font_size") or (32 if ph_type in _TITLE_LIKE else 18)
    if not font_family:
        font_family = inherited.get("font_family") or "Aptos"
    bold = bold or bool(inherited.get("bold"))
    alignment = alignment or inherited.get("alignment")
    return {
        "idx": ph.placeholder_format.idx,
        "type": ph_type,
        "name": getattr(ph, "name", ""),
        "left": ph.left,
        "top": ph.top,
        "width": ph.width,
        "height": ph.height,
        "font_family": font_family,
        "font_size": font_size,
        "bold": bold,
        "alignment": alignment,
    }


def _extract_example_slides(prs) -> list[dict]:
    example_slides = []
    for i, slide in enumerate(prs.slides):
        layout_name = slide.slide_layout.name
        layout_index_in_master = list(slide.slide_layout.slide_master.slide_layouts).index(
            slide.slide_layout
        )
        text_parts: list[str] = []
        image_count = 0
        for shape in slide.shapes:
            if shape.shape_type == 13:  # MSO_SHAPE_TYPE.PICTURE
                image_count += 1
            if shape.has_text_frame:
                text = shape.text_frame.text.strip()
                if text:
                    text_parts.append(text)
        text_sample = " ".join(text_parts)[:200]
        example_slides.append(
            {
                "index": i,
                "layout_name": layout_name,
                "layout_index": layout_index_in_master,
                "text_sample": text_sample,
                "image_count": image_count,
            }
        )
    return example_slides


def inspect_template(pptx_path: Path) -> dict:
    prs = PPTXPresentation(str(pptx_path))
    theme = _extract_theme(pptx_path)
    masters = _extract_masters(prs, theme)
    layout_infos = []
    for i, layout in enumerate(prs.slide_layouts):
        master_index = list(prs.slide_masters).index(layout.slide_master)
        master = masters[master_index]
        layout_infos.append(
            {
                "index": i,
                "name": layout.name,
                "placeholders": [
                    _inspect_placeholder(ph, _placeholder_typography(layout, ph, theme))
                    for ph in layout.placeholders
                ],
                "master_index": master_index,
                "master_name": master["name"],
                "master_placeholders": master["placeholders"],
            }
        )
    return {
        "slide_width": prs.slide_width,
        "slide_height": prs.slide_height,
        "slide_count": len(prs.slide_layouts),
        "layouts": layout_infos,
        "masters": masters,
        "theme": theme,
        "example_slides": _extract_example_slides(prs),
        "semantic_slots": {semantic: list(slots) for semantic, slots in SEMANTIC_SLOTS.items()},
    }


def map_semantic_layouts(inspection: dict) -> list[dict]:
    layouts = inspection["layouts"]
    results: list[dict] = []

    for semantic in [
        "title",
        "section",
        "bullets",
        "two_column",
        "big_statement",
        "closing",
        "code",
        "image",
        "quote",
    ]:
        scores = [(i, _score_layout(layout, semantic)) for i, layout in enumerate(layouts)]
        best_idx, best_score = max(scores, key=lambda x: x[1])
        best_layout = layouts[best_idx]
        matched_keyword = _matched_keyword(best_layout["name"], semantic)
        results.append(
            {
                "semantic_layout": semantic,
                "template_layout_index": best_idx,
                "confidence": min(best_score, 1.0),
                "rationale": _build_rationale(best_layout, semantic, best_score, matched_keyword),
                "slot_mappings": infer_slot_mappings(best_layout, semantic),
            }
        )

    return results


def infer_slot_mappings(layout: dict, semantic: str) -> dict[str, int]:
    placeholders = layout["placeholders"]
    title = [ph for ph in placeholders if ph["type"] in _TITLE_LIKE]
    subtitle = [ph for ph in placeholders if ph["type"] == SUBTITLE_PH]
    content = [ph for ph in placeholders if ph["type"] in (BODY_PH, OBJECT_PH)]
    pictures = [ph for ph in placeholders if ph["type"] == PICTURE_PH]
    content.sort(key=lambda ph: (ph["left"], ph["top"], ph["idx"]))
    result: dict[str, int] = {}

    def assign(slot: str, candidates: list[dict]) -> None:
        if candidates:
            result[slot] = candidates[0]["idx"]

    if semantic == "big_statement":
        assign("statement", title or content or placeholders)
        return result
    assign("title", title)
    if semantic == "title":
        assign("subtitle", subtitle or content)
    elif semantic in {"bullets", "code", "quote"}:
        assign("body", content)
    elif semantic == "closing":
        assign("body", subtitle or content)
    elif semantic == "two_column":
        assign("left", content)
        assign("right", content[1:])
    elif semantic == "image":
        assign("image", pictures or content)
    return result


def _matched_keyword(layout_name: str, semantic: str) -> str | None:
    name_lower = layout_name.lower().strip()
    keywords = SEMANTIC_KEYWORDS.get(semantic, [])
    best_kw = None
    best_score = 0.0
    for kw in keywords:
        if kw == name_lower:
            return kw
        if name_lower.startswith(kw) and best_score < 0.9:
            best_score = 0.9
            best_kw = kw
        elif kw in name_lower and best_score < 0.7:
            best_score = 0.7
            best_kw = kw
        else:
            words = set(re.split(r"[\s\-_]+", name_lower))
            kw_words = set(re.split(r"[\s\-_]+", kw))
            overlap = words & kw_words
            if overlap:
                s = 0.5 * len(overlap) / len(kw_words)
                if s > best_score:
                    best_score = s
                    best_kw = kw
    return best_kw


def _score_layout(layout: dict, semantic: str) -> float:
    name_score = _name_score(layout["name"], semantic)
    placeholder_score = _placeholder_score(layout["placeholders"], semantic)

    if name_score > 0.7 and placeholder_score > 0.7:
        return 0.9
    if placeholder_score > 0.7:
        return 0.7
    if name_score > 0.7:
        return 0.5
    if name_score > 0.3 and placeholder_score > 0.3:
        return 0.4
    return 0.3


def _name_score(layout_name: str, semantic: str) -> float:
    name_lower = layout_name.lower().strip()
    keywords = SEMANTIC_KEYWORDS.get(semantic, [])
    if not keywords:
        return 0.0

    best = 0.0
    for kw in keywords:
        if kw == name_lower:
            return 1.0
        if name_lower.startswith(kw):
            best = max(best, 0.9)
        elif kw in name_lower:
            best = max(best, 0.7)
        else:
            words = set(re.split(r"[\s\-_]+", name_lower))
            kw_words = set(re.split(r"[\s\-_]+", kw))
            overlap = words & kw_words
            if overlap:
                best = max(best, 0.5 * len(overlap) / len(kw_words))
    return best


def _placeholder_score(placeholders: list[dict], semantic: str) -> float:
    if not placeholders:
        return 0.0

    types = [ph["type"] for ph in placeholders]
    tops = [ph.get("top", 0) for ph in placeholders]
    title_count = sum(1 for t in types if t in _TITLE_LIKE)
    body_count = sum(1 for t in types if t == BODY_PH)
    subtitle_count = sum(1 for t in types if t == SUBTITLE_PH)
    object_or_pic_count = sum(1 for t in types if t in (OBJECT_PH, PICTURE_PH))
    total = len(placeholders)

    if semantic == "title":
        if title_count >= 1 and (subtitle_count >= 1 or body_count >= 1):
            return 0.9
        if title_count >= 1 and total >= 2:
            return 0.7
        if title_count >= 1:
            return 0.5
        return 0.2

    if semantic == "section":
        if title_count >= 1 and total <= 2 and body_count == 0:
            return 0.9
        if title_count >= 1 and total == 1:
            return 0.9
        if title_count >= 1 and total <= 2:
            return 0.6
        return 0.2

    if semantic == "bullets":
        if title_count >= 1 and body_count >= 1:
            return 0.9
        if body_count >= 1:
            return 0.7
        if title_count >= 1:
            return 0.4
        return 0.2

    if semantic == "two_column":
        body_or_obj = sum(1 for t in types if t in (BODY_PH, OBJECT_PH))
        if title_count >= 1 and body_or_obj >= 2:
            body_obj_indices = [i for i, t in enumerate(types) if t in (BODY_PH, OBJECT_PH)]
            if len(body_obj_indices) >= 2:
                y_values = [tops[i] for i in body_obj_indices[:2]]
                if abs(y_values[0] - y_values[1]) < 500000:
                    return 0.95
            return 0.9
        if body_or_obj >= 2:
            body_obj_indices = [i for i, t in enumerate(types) if t in (BODY_PH, OBJECT_PH)]
            if len(body_obj_indices) >= 2:
                y_values = [tops[i] for i in body_obj_indices[:2]]
                if abs(y_values[0] - y_values[1]) < 500000:
                    return 0.8
            return 0.7
        if title_count >= 1 and body_or_obj >= 1:
            return 0.5
        return 0.2

    if semantic == "big_statement":
        if total == 1 and title_count == 1:
            return 0.9
        if total == 1:
            return 0.8
        if total <= 2 and title_count + subtitle_count >= 1:
            return 0.5
        return 0.2

    if semantic == "closing":
        if title_count >= 1 and (subtitle_count >= 1 or body_count >= 1):
            return 0.8
        if title_count >= 1 and total >= 2:
            return 0.6
        if title_count >= 1:
            return 0.4
        return 0.2

    if semantic == "code":
        if title_count >= 1 and body_count >= 1:
            return 0.8
        if body_count >= 1:
            return 0.6
        if title_count >= 1:
            return 0.4
        return 0.2

    if semantic == "image":
        if object_or_pic_count >= 1:
            return 0.9
        if title_count >= 1 and body_count >= 1:
            return 0.5
        if body_count >= 1:
            return 0.3
        return 0.2

    if semantic == "quote":
        if title_count >= 1 and body_count >= 1:
            return 0.8
        if body_count >= 1:
            return 0.6
        if title_count >= 1:
            return 0.4
        return 0.2

    return 0.2


def _build_rationale(
    layout: dict, semantic: str, score: float, matched_keyword: str | None = None
) -> str:
    name = layout["name"] or f"Layout {layout['index']}"
    kw_note = f' (keyword "{matched_keyword}")' if matched_keyword else ""
    if score >= 0.8:
        return f"Layout '{name}' strongly matches {semantic}{kw_note}"
    if score >= 0.5:
        return f"Layout '{name}' partially matches {semantic}{kw_note}"
    return f"Best-available match for {semantic} in layout '{name}'{kw_note}"


async def suggest_mappings_with_llm(
    inspection: dict,
    model: object,
) -> list[dict]:
    import json

    from pydantic_ai import Agent as PydanticAgent  # noqa: F811, PLC0415

    layout_descs: list[str] = []
    for layout in inspection["layouts"]:
        placeholders_desc = (
            ", ".join(f"idx={ph['idx']} type={ph['type']}" for ph in layout["placeholders"])
            if layout["placeholders"]
            else "none"
        )
        layout_descs.append(
            f"  Layout {layout['index']}: "
            f"name='{layout['name']}', "
            f"placeholders=[{placeholders_desc}]"
        )

    prompt = (
        "Analyze a PowerPoint template and map each semantic layout "
        "to the best template layout index.\n\n"
        "Semantic layouts to map: title, section, bullets, two_column, "
        "big_statement, closing, code, image, quote.\n\n"
        "Template layouts:\n" + "\n".join(layout_descs) + "\n\n"
        "For each semantic layout, output the template layout index that best matches. "
        "Return ONLY a JSON array of objects with keys: semantic_layout, "
        "template_layout_index, confidence (0.0-1.0), rationale (string explaining why).\n\n"
        "Placeholder type reference: 1=TITLE, 3=CENTER_TITLE, "
        "4=SUBTITLE, 5=BODY, 7=OBJECT, 8=PICTURE\n\n"
        "Rules:\n"
        "- title: needs a TITLE or CENTER_TITLE placeholder with a subtitle or body\n"
        "- section: needs a TITLE or CENTER_TITLE placeholder, "
        "preferably few other placeholders\n"
        "- bullets: needs a TITLE or CENTER_TITLE and a BODY placeholder\n"
        "- two_column: needs a TITLE and two BODY or OBJECT placeholders\n"
        "- big_statement: simple layout with few placeholders, preferably just a title\n"
        "- closing: similar to title layout\n"
        "- code: needs a TITLE and BODY placeholder\n"
        "- image: needs a PICTURE or OBJECT placeholder\n"
        "- quote: needs a TITLE and BODY placeholder\n\n"
        "Use layout names as strong signals. Output JSON array only, no other text."
    )

    agent = PydanticAgent(model, output_type=str)
    result = await agent.run(prompt)
    try:
        suggestions: list[dict] = json.loads(str(result.output))
    except json.JSONDecodeError, TypeError:
        return _fallback_mappings(inspection)

    validated = []
    layout_count = len(inspection["layouts"])
    all_semantics = {
        "title",
        "section",
        "bullets",
        "two_column",
        "big_statement",
        "closing",
        "code",
        "image",
        "quote",
    }
    seen: set[str] = set()

    for s in suggestions:
        semantic = s.get("semantic_layout", "")
        idx = s.get("template_layout_index", -1)
        if not isinstance(idx, int) or idx < 0 or idx >= layout_count:
            continue
        if semantic not in all_semantics or semantic in seen:
            continue
        seen.add(semantic)
        confidence = s.get("confidence", 0.5)
        if not isinstance(confidence, (int, float)):
            confidence = 0.5
        validated.append(
            {
                "semantic_layout": semantic,
                "template_layout_index": idx,
                "confidence": max(0.0, min(float(confidence), 1.0)),
                "rationale": str(s.get("rationale", "")),
                "slot_mappings": infer_slot_mappings(inspection["layouts"][idx], semantic),
            }
        )

    for semantic in all_semantics - seen:
        heuristic = map_semantic_layouts(inspection)
        match = next((m for m in heuristic if m["semantic_layout"] == semantic), None)
        if match:
            validated.append(match)

    return validated


def _fallback_mappings(inspection: dict) -> list[dict]:
    return map_semantic_layouts(inspection)
