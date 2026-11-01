from __future__ import annotations

import os
import tempfile
from pathlib import Path
from typing import Literal
from uuid import uuid4

import yaml
from pptx import Presentation as PPTXPresentation
from pydantic import BaseModel, ConfigDict, Field

from course_harness.core.presentation import VALID_LAYOUTS
from course_harness.core.template_profiles import (
    BUILTIN_DEFAULT_ID,
    BUILTIN_DEFAULT_PROFILE_NAME,
    SEMANTIC_SLOTS,
    TemplateLayoutMapping,
    TemplateProfile,
    builtin_default_profile,
)
from course_harness.core.template_slots import (
    find_body_placeholder,
    find_column_placeholder_groups,
    find_content_placeholders,
    find_subtitle_or_body_placeholder,
    find_title_placeholder,
)
from course_harness.runtime_paths import RuntimePaths

TEMPLATE_MEDIA_TYPE = "application/vnd.openxmlformats-officedocument.presentationml.presentation"
TEMPLATE_POTX_MEDIA_TYPE = "application/vnd.openxmlformats-officedocument.presentationml.template"


class TemplateProfileSummary(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str
    name: str
    version: int
    slide_count: int
    mapped_layouts: int


class TemplateProfileRegistry(BaseModel):
    model_config = ConfigDict(extra="forbid")

    schema_version: Literal[1] = 1
    profiles: list[TemplateProfileSummary] = Field(default_factory=list)


class CalibrationSlide(BaseModel):
    model_config = ConfigDict(extra="forbid")

    semantic_layout: str
    template_layout_index: int
    image_url: str


def unique_profile_name(
    requested_name: str,
    registry: TemplateProfileRegistry,
    *,
    exclude_profile_id: str | None = None,
) -> str:
    """Return a unique, human-readable profile name for the global catalog."""
    base = requested_name.strip() or "Imported template"
    base = base[:200].rstrip()
    existing = {
        summary.name.casefold() for summary in registry.profiles if summary.id != exclude_profile_id
    }
    existing.add(BUILTIN_DEFAULT_PROFILE_NAME.casefold())
    if base.casefold() not in existing:
        return base

    suffix_number = 2
    while True:
        suffix = f" ({suffix_number})"
        candidate = f"{base[: 200 - len(suffix)].rstrip()}{suffix}"
        if candidate.casefold() not in existing:
            return candidate
        suffix_number += 1


def templates_data_dir() -> Path:
    return RuntimePaths.platform().templates_data_path


def templates_cache_dir() -> Path:
    return RuntimePaths.platform().templates_cache_path


def profile_dir(data_dir: Path, profile_id: str) -> Path:
    return data_dir / profile_id


def calibration_dir(cache_dir: Path, profile_id: str) -> Path:
    return cache_dir / profile_id / "calibration"


def registry_path(data_dir: Path) -> Path:
    return data_dir / "profiles.json"


def _profile_yaml_path(profile_dir_path: Path) -> Path:
    return profile_dir_path / "profile.yaml"


def _profile_version_yaml_path(profile_dir_path: Path, version: int) -> Path:
    return profile_dir_path / f"profile-v{version}.yaml"


def read_registry(data_dir: Path) -> TemplateProfileRegistry:
    path = registry_path(data_dir)
    if not path.exists():
        return TemplateProfileRegistry()
    return TemplateProfileRegistry.model_validate_json(path.read_text(encoding="utf-8"))


def write_registry(data_dir: Path, registry: TemplateProfileRegistry) -> None:
    data_dir.mkdir(parents=True, exist_ok=True)
    path = registry_path(data_dir)
    temporary_path = path.with_suffix(f"{path.suffix}.tmp")
    temporary_path.write_text(registry.model_dump_json(indent=2) + "\n", encoding="utf-8")
    try:
        temporary_path.replace(path)
    finally:
        temporary_path.unlink(missing_ok=True)


def read_profile(data_dir: Path, profile_id: str) -> TemplateProfile | None:
    if profile_id == BUILTIN_DEFAULT_ID:
        return builtin_default_profile()
    path = _profile_yaml_path(profile_dir(data_dir, profile_id))
    if not path.exists():
        return None
    raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    return TemplateProfile.model_validate(raw)


def read_profile_version(data_dir: Path, profile_id: str, version: int) -> TemplateProfile | None:
    if profile_id == BUILTIN_DEFAULT_ID:
        profile = builtin_default_profile()
        return profile if version == profile.version else None
    path = _profile_version_yaml_path(profile_dir(data_dir, profile_id), version)
    if not path.exists():
        return None
    raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    return TemplateProfile.model_validate(raw)


def write_profile(data_dir: Path, profile: TemplateProfile) -> None:
    pd = profile_dir(data_dir, profile.id)
    pd.mkdir(parents=True, exist_ok=True)
    _write_profile_atomically(pd, profile, _profile_yaml_path(pd))


def write_profile_version(data_dir: Path, profile: TemplateProfile) -> None:
    pd = profile_dir(data_dir, profile.id)
    pd.mkdir(parents=True, exist_ok=True)
    _write_profile_atomically(pd, profile, _profile_version_yaml_path(pd, profile.version))
    write_profile(data_dir, profile)


def _write_profile_atomically(pd: Path, profile: TemplateProfile, target: Path) -> None:
    temporary_path = pd / f".profile-{uuid4().hex}.yaml.tmp"
    serialized = yaml.safe_dump(
        profile.model_dump(mode="json"), allow_unicode=True, sort_keys=False, width=100
    )
    with temporary_path.open("x", encoding="utf-8") as stream:
        stream.write(serialized)
        stream.flush()
        os.fsync(stream.fileno())
    try:
        temporary_path.replace(target)
    finally:
        temporary_path.unlink(missing_ok=True)


def delete_profile(data_dir: Path, cache_dir: Path, profile_id: str) -> None:
    import shutil

    pd = profile_dir(data_dir, profile_id)
    if pd.exists():
        shutil.rmtree(pd)
    cd = cache_dir / profile_id
    if cd.exists():
        shutil.rmtree(cd)
    registry = read_registry(data_dir)
    registry.profiles = [p for p in registry.profiles if p.id != profile_id]
    write_registry(data_dir, registry)


def validate_template_content(content: bytes) -> object:
    import io

    return PPTXPresentation(io.BytesIO(content))


def resolve_profile(
    data_dir: Path, profile_id: str | None, version: int | None = None
) -> TemplateProfile:
    if profile_id is None or profile_id == BUILTIN_DEFAULT_ID:
        profile = builtin_default_profile()
        if version is not None and version != profile.version:
            raise ValueError(f"Template profile '{profile.id}' version {version} not found")
        return profile
    profile = (
        read_profile_version(data_dir, profile_id, version)
        if version is not None
        else read_profile(data_dir, profile_id)
    )
    if profile is None:
        suffix = f" version {version}" if version is not None else ""
        raise ValueError(f"Template profile '{profile_id}'{suffix} not found")
    return profile


def render_calibration(
    data_dir: Path,
    cache_dir: Path,
    profile: TemplateProfile,
) -> list[CalibrationSlide]:

    if profile.id == BUILTIN_DEFAULT_ID:
        return _render_builtin_calibration(profile)

    template_path = profile_dir(data_dir, profile.id) / "template.pptx"
    if not template_path.exists():
        raise ValueError(f"Template file not found for profile {profile.id}")

    cal_dir = calibration_dir(cache_dir, profile.id)
    existing = list(cal_dir.glob("*.png"))
    if existing and _calibration_meta_matches(cal_dir, profile):
        return [
            CalibrationSlide(
                semantic_layout=_parse_semantic_from_filename(p.name),
                template_layout_index=_parse_index_from_filename(p.name),
                image_url=f"/api/templates/{profile.id}/calibration/{p.name}",
            )
            for p in sorted(existing)
        ]

    cal_dir.mkdir(parents=True, exist_ok=True)

    for f in cal_dir.glob("*.png"):
        f.unlink()

    slides = _generate_calibration_slides(profile, template_path)

    for semantic, idx, pptx_bytes in slides:
        slide_name = f"{idx:02d}-{semantic}"
        _render_single_slide(cal_dir, slide_name, pptx_bytes)

    final_images = sorted(cal_dir.glob("*.png"))
    if len(final_images) < len(profile.layouts):
        raise RuntimeError(
            f"Expected {len(profile.layouts)} calibration images, got {len(final_images)}"
        )

    _write_calibration_meta(cal_dir, profile)

    return [
        CalibrationSlide(
            semantic_layout=_parse_semantic_from_filename(p.name),
            template_layout_index=_parse_index_from_filename(p.name),
            image_url=f"/api/templates/{profile.id}/calibration/{p.name}",
        )
        for p in final_images
    ]


def _render_single_slide(cal_dir: Path, slide_name: str, pptx_bytes: bytes) -> None:
    import shutil
    import subprocess

    with tempfile.TemporaryDirectory() as tmpdir:
        src = Path(tmpdir) / f"{slide_name}.pptx"
        src.write_bytes(pptx_bytes)

        result = subprocess.run(
            [
                "libreoffice",
                "--headless",
                "--convert-to",
                "png",
                "--outdir",
                str(tmpdir),
                str(src),
            ],
            capture_output=True,
            text=True,
            timeout=15,
        )
        if result.returncode != 0:
            raise RuntimeError(
                f"LibreOffice render failed for {slide_name}: {result.stderr.strip()}"
            )

        rendered = sorted(Path(tmpdir).glob("*.png"))
        if not rendered:
            raise RuntimeError(f"No PNG output for {slide_name}")

        target = cal_dir / f"{slide_name}.png"
        shutil.move(str(rendered[0]), str(target))


def _render_builtin_calibration(profile: TemplateProfile) -> list[CalibrationSlide]:
    return [
        CalibrationSlide(
            semantic_layout=m.semantic_layout,
            template_layout_index=m.template_layout_index,
            image_url="",
        )
        for m in profile.layouts
    ]


def _generate_calibration_deck(profile: TemplateProfile, template_path: Path) -> bytes:
    import io

    prs = PPTXPresentation(str(template_path))
    layouts = prs.slide_layouts

    for mapping in profile.layouts:
        idx = mapping.template_layout_index
        if idx >= len(layouts):
            continue
        slide_layout = layouts[idx]
        slide = prs.slides.add_slide(slide_layout)
        _populate_calibration_slide(slide, mapping)

    buffer = io.BytesIO()
    prs.save(buffer)
    return buffer.getvalue()


def _generate_calibration_slides(
    profile: TemplateProfile,
    template_path: Path,
) -> list[tuple[str, int, bytes]]:
    import io

    slides: list[tuple[str, int, bytes]] = []

    for mapping in profile.layouts:
        semantic = mapping.semantic_layout
        idx = mapping.template_layout_index
        prs = PPTXPresentation(str(template_path))
        layouts = prs.slide_layouts
        if idx >= len(layouts):
            continue
        _delete_all_slides(prs)
        slide_layout = layouts[idx]
        slide = prs.slides.add_slide(slide_layout)
        _populate_calibration_slide(slide, mapping)

        buffer = io.BytesIO()
        prs.save(buffer)
        slides.append((semantic, idx, buffer.getvalue()))

    return slides


def _populate_calibration_slide(slide, mapping: TemplateLayoutMapping) -> None:
    import contextlib

    if mapping.slot_mappings:
        sample_text = {
            "title": f"[{mapping.semantic_layout}] Sample Title",
            "subtitle": "Sample subtitle text.",
            "body": "Sample body text.\nThis is rendered for calibration.",
            "left": "Sample left-column content.",
            "right": "Sample right-column content.",
            "statement": "A sample big statement.",
            "image": "[Sample image or caption]",
        }
        placeholders = {shape.placeholder_format.idx: shape for shape in slide.placeholders}
        for slot in SEMANTIC_SLOTS[mapping.semantic_layout]:
            placeholder_index = mapping.slot_mappings.get(slot)
            shape = (
                placeholders.get(placeholder_index)
                if placeholder_index is not None
                else _calibration_fallback_placeholder(slide, mapping.semantic_layout, slot)
            )
            if shape is not None:
                with contextlib.suppress(Exception):
                    shape.text_frame.text = sample_text[slot]
        return

    for shape in slide.placeholders:
        if shape.placeholder_format.idx == 0:
            shape.text_frame.text = f"[{mapping.semantic_layout}] Sample Title"
        elif shape.placeholder_format.idx == 1:
            shape.text_frame.text = (
                "Sample subtitle or body text.\nThis is rendered for calibration."
            )
        elif shape.placeholder_format.idx == 2:
            shape.text_frame.text = "\u2022 Sample bullet one\n\u2022 Sample bullet two"
        else:
            with contextlib.suppress(Exception):
                shape.text_frame.text = f"[placeholder idx={shape.placeholder_format.idx}]"


def _calibration_fallback_placeholder(slide, semantic: str, slot: str):
    if slot in {"title", "statement"}:
        return find_title_placeholder(slide)
    elif slot == "subtitle" or (slot == "body" and semantic == "closing"):
        return find_subtitle_or_body_placeholder(slide)
    elif slot == "body":
        return find_body_placeholder(slide)
    elif slot == "image":
        placeholders = find_content_placeholders(slide)
        return placeholders[0] if placeholders else None
    elif slot in {"left", "right"}:
        groups = find_column_placeholder_groups(slide)
        column_index = 0 if slot == "left" else 1
        return groups[column_index][0] if len(groups) > column_index else None
    return None


def _delete_all_slides(prs) -> None:
    ns = "{http://schemas.openxmlformats.org/officeDocument/2006/relationships}id"
    sldIdLst = prs.slides._sldIdLst
    while len(sldIdLst) > 0:
        rId = sldIdLst[0].get(ns)
        if rId is not None:
            prs.part.drop_rel(rId)
        sldIdLst.remove(sldIdLst[0])


def _calibration_meta_matches(cal_dir: Path, profile: TemplateProfile) -> bool:
    meta = cal_dir / ".meta"
    if not meta.exists():
        return False
    try:
        data = yaml.safe_load(meta.read_text(encoding="utf-8"))
        return (
            data.get("profile_version") == profile.version and data.get("profile_id") == profile.id
        )
    except Exception:
        return False


def _write_calibration_meta(cal_dir: Path, profile: TemplateProfile) -> None:
    meta = cal_dir / ".meta"
    meta.write_text(
        yaml.safe_dump({"profile_id": profile.id, "profile_version": profile.version}),
        encoding="utf-8",
    )


def _parse_semantic_from_filename(filename: str) -> str:
    stem = Path(filename).stem
    parts = stem.split("-", 1)
    if len(parts) == 2 and parts[1] in VALID_LAYOUTS:
        return parts[1]
    return filename


def _parse_index_from_filename(filename: str) -> int:
    stem = Path(filename).stem
    try:
        return int(stem.split("-")[0])
    except ValueError, IndexError:
        return 0
