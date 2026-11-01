"""Ask a model to map a PowerPoint template's layouts to semantic Slide layouts."""

from __future__ import annotations

from course_harness.core.template_inspect import infer_slot_mappings, map_semantic_layouts


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
