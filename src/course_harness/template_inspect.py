from __future__ import annotations

import re
from pathlib import Path

from pptx import Presentation as PPTXPresentation
from pptx.enum.shapes import PP_PLACEHOLDER

TITLE_PH = PP_PLACEHOLDER.TITLE
CENTER_TITLE_PH = PP_PLACEHOLDER.CENTER_TITLE
BODY_PH = PP_PLACEHOLDER.BODY
SUBTITLE_PH = PP_PLACEHOLDER.SUBTITLE
OBJECT_PH = PP_PLACEHOLDER.OBJECT
PICTURE_PH = PP_PLACEHOLDER.PICTURE

_TITLE_LIKE = {int(TITLE_PH), int(CENTER_TITLE_PH)}

SEMANTIC_KEYWORDS: dict[str, list[str]] = {
    "title": ["title slide", "title", "opening", "cover", "front"],
    "section": ["section header", "section", "divider", "chapter", "separator", "transition"],
    "bullets": ["title and content", "content", "bullet", "body", "text", "list", "paragraph"],
    "two_column": [
        "two content",
        "comparison",
        "two column",
        "left right",
        "dual",
        "side by side",
        "2 column",
    ],
    "big_statement": ["blank", "empty", "title only", "big statement", "statement"],
    "closing": ["closing", "end", "thank you", "final", "conclusion", "ending", "summary slide"],
    "code": ["code", "monospace", "source code", "programming"],
    "image": ["picture", "image", "photo", "graphic", "content with caption", "media"],
    "quote": ["quote", "testimonial", "pull quote", "citation"],
}


def inspect_template(pptx_path: Path) -> dict:
    prs = PPTXPresentation(str(pptx_path))
    layout_infos = []
    for i, layout in enumerate(prs.slide_layouts):
        placeholders = []
        for ph in layout.placeholders:
            try:
                ph_type = (
                    int(ph.placeholder_format.type) if ph.placeholder_format.type is not None else 0
                )
            except Exception:
                ph_type = 0
            placeholders.append(
                {
                    "idx": ph.placeholder_format.idx,
                    "type": ph_type,
                    "name": getattr(ph, "name", ""),
                    "width": ph.width,
                    "height": ph.height,
                }
            )
        layout_infos.append(
            {
                "index": i,
                "name": layout.name,
                "placeholders": placeholders,
            }
        )
    return {
        "slide_width": prs.slide_width,
        "slide_height": prs.slide_height,
        "slide_count": len(prs.slide_layouts),
        "layouts": layout_infos,
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
        results.append(
            {
                "semantic_layout": semantic,
                "template_layout_index": best_idx,
                "confidence": min(best_score, 1.0),
                "rationale": _build_rationale(layouts[best_idx], semantic, best_score),
            }
        )

    return results


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
            return 0.9
        if body_or_obj >= 2:
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


def _build_rationale(layout: dict, semantic: str, score: float) -> str:
    name = layout["name"] or f"Layout {layout['index']}"
    if score >= 0.8:
        return f"Layout '{name}' strongly matches {semantic}"
    if score >= 0.5:
        return f"Layout '{name}' partially matches {semantic}"
    return f"Best-available match for {semantic} in layout '{name}'"
