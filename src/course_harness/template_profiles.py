from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path
from typing import Literal
from uuid import uuid4

import yaml
from pptx import Presentation as PPTXPresentation
from pydantic import BaseModel, ConfigDict, Field

from course_harness.presentation import VALID_LAYOUTS

BUILTIN_DEFAULT_ID = "_builtin-default"
TEMPLATE_MEDIA_TYPE = "application/vnd.openxmlformats-officedocument.presentationml.presentation"

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

BUILTIN_DEFAULT_PROFILE_NAME = "Built-in default"
BUILTIN_LAYOUT_NAMES: dict[int, str] = {
    0: "Title Slide",
    1: "Title and Content",
    2: "Section Header",
    3: "Two Content",
    4: "Comparison",
    5: "Blank",
    6: "Content with Caption",
    7: "Vertical Title and Text",
    8: "Picture with Caption",
    9: "Title and Vertical Text",
    10: "Vertical Title and Vertical Text",
}


class TemplateLayoutMapping(BaseModel):
    model_config = ConfigDict(extra="forbid")

    semantic_layout: str = Field(
        pattern=(
            r"^(title|section|bullets|two_column"
            r"|big_statement|closing|code|image|quote)$"
        )
    )
    template_layout_index: int = Field(ge=0)
    confidence: float = Field(ge=0.0, le=1.0)
    rationale: str = Field(min_length=1)


class TemplateProfile(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    schema_version: Literal[1] = 1
    id: str = Field(pattern=r"^(tpl-[0-9a-f]{12}|_builtin-default)$")
    name: str = Field(min_length=1, max_length=200)
    version: int = Field(ge=1)
    template_filename: str = Field(min_length=1)
    slide_width: int
    slide_height: int
    slide_count: int
    layouts: list[TemplateLayoutMapping] = Field(min_length=1)


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


def templates_data_dir() -> Path:
    if sys.platform == "win32":
        root = Path(os.environ.get("LOCALAPPDATA", Path.home() / "AppData" / "Local"))
        return root / "Course Harness" / "templates"
    if sys.platform == "darwin":
        return Path.home() / "Library" / "Application Support" / "Course Harness" / "templates"
    root = Path(os.environ.get("XDG_DATA_HOME", Path.home() / ".local" / "share"))
    return root / "course-harness" / "templates"


def templates_cache_dir() -> Path:
    if sys.platform == "win32":
        root = Path(os.environ.get("LOCALAPPDATA", Path.home() / "AppData" / "Local"))
        return root / "Course Harness" / "cache" / "templates"
    if sys.platform == "darwin":
        return Path.home() / "Library" / "Caches" / "Course Harness" / "templates"
    root = Path(os.environ.get("XDG_CACHE_HOME", Path.home() / ".cache"))
    return root / "course-harness" / "templates"


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
        return _builtin_default_profile()
    path = _profile_yaml_path(profile_dir(data_dir, profile_id))
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


def _builtin_default_profile() -> TemplateProfile:
    return TemplateProfile(
        id=BUILTIN_DEFAULT_ID,
        name=BUILTIN_DEFAULT_PROFILE_NAME,
        version=1,
        template_filename="python-pptx built-in Office Theme",
        slide_width=12192000,
        slide_height=6858000,
        slide_count=11,
        layouts=[
            TemplateLayoutMapping(
                semantic_layout=layout,
                template_layout_index=idx,
                confidence=1.0,
                rationale=f"Built-in layout '{BUILTIN_LAYOUT_NAMES.get(idx, f'Index {idx}')}' "
                f"mapped to {layout}",
            )
            for layout, idx in DEFAULT_LAYOUT_MAPPING.items()
        ],
    )


def resolve_profile(data_dir: Path, profile_id: str | None) -> TemplateProfile:
    if profile_id is None or profile_id == BUILTIN_DEFAULT_ID:
        return _builtin_default_profile()
    profile = read_profile(data_dir, profile_id)
    if profile is None:
        raise ValueError(f"Template profile '{profile_id}' not found")
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
    import contextlib
    import io

    prs = PPTXPresentation(str(template_path))
    mapping = {m.semantic_layout: m.template_layout_index for m in profile.layouts}
    layouts = prs.slide_layouts

    for semantic, idx in mapping.items():
        if idx >= len(layouts):
            continue
        slide_layout = layouts[idx]
        slide = prs.slides.add_slide(slide_layout)

        for shape in slide.placeholders:
            if shape.placeholder_format.idx == 0:
                shape.text_frame.text = f"[{semantic}] Sample Title"
            elif shape.placeholder_format.idx == 1:
                shape.text_frame.text = (
                    "Sample subtitle or body text.\nThis is rendered for calibration."
                )
            elif shape.placeholder_format.idx == 2:
                tf = shape.text_frame
                tf.clear()
                p = tf.paragraphs[0]
                p.text = "\u2022 Sample bullet one"
                p2 = tf.add_paragraph()
                p2.text = "\u2022 Sample bullet two"
            else:
                with contextlib.suppress(Exception):
                    shape.text_frame.text = f"[placeholder idx={shape.placeholder_format.idx}]"

    buffer = io.BytesIO()
    prs.save(buffer)
    return buffer.getvalue()


def _generate_calibration_slides(
    profile: TemplateProfile,
    template_path: Path,
) -> list[tuple[str, int, bytes]]:
    import contextlib
    import io

    mapping = {m.semantic_layout: m.template_layout_index for m in profile.layouts}
    slides: list[tuple[str, int, bytes]] = []

    for semantic, idx in mapping.items():
        prs = PPTXPresentation(str(template_path))
        layouts = prs.slide_layouts
        if idx >= len(layouts):
            continue
        slide_layout = layouts[idx]
        slide = prs.slides.add_slide(slide_layout)

        for shape in slide.placeholders:
            if shape.placeholder_format.idx == 0:
                shape.text_frame.text = f"[{semantic}] Sample Title"
            elif shape.placeholder_format.idx == 1:
                shape.text_frame.text = (
                    "Sample subtitle or body text.\nThis is rendered for calibration."
                )
            elif shape.placeholder_format.idx == 2:
                tf = shape.text_frame
                tf.clear()
                p = tf.paragraphs[0]
                p.text = "\u2022 Sample bullet one"
                p2 = tf.add_paragraph()
                p2.text = "\u2022 Sample bullet two"
            else:
                with contextlib.suppress(Exception):
                    shape.text_frame.text = f"[placeholder idx={shape.placeholder_format.idx}]"

        buffer = io.BytesIO()
        prs.save(buffer)
        slides.append((semantic, idx, buffer.getvalue()))

    return slides


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
