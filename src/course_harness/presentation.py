import os
from pathlib import Path
from typing import Literal
from uuid import uuid4

import yaml
from pydantic import BaseModel, ConfigDict, Field, ValidationError, model_validator


class SlideCitation(BaseModel):
    model_config = ConfigDict(extra="forbid")

    source_id: str = Field(min_length=1)
    label: str = Field(min_length=1, max_length=200)
    url: str | None = None
    line_start: int | None = None
    line_end: int | None = None


class SlideBase(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str = Field(pattern=r"^slide-[0-9a-f]{12}$")
    archived: bool = False


class TitleSlide(SlideBase):
    layout: Literal["title"] = "title"
    title: str | None = None
    subtitle: str | None = None
    speaker_notes: str | None = None
    purpose: str | None = None
    citations: list[SlideCitation] = Field(default_factory=list)


class SectionSlide(SlideBase):
    layout: Literal["section"] = "section"
    title: str | None = None
    speaker_notes: str | None = None
    purpose: str | None = None
    citations: list[SlideCitation] = Field(default_factory=list)


class BulletsSlide(SlideBase):
    layout: Literal["bullets"] = "bullets"
    title: str | None = None
    bullets: list[str] = Field(default_factory=list)
    speaker_notes: str | None = None
    purpose: str | None = None
    citations: list[SlideCitation] = Field(default_factory=list)


class TwoColumnSlide(SlideBase):
    layout: Literal["two_column"] = "two_column"
    title: str | None = None
    left_content: str = ""
    right_content: str = ""
    speaker_notes: str | None = None
    purpose: str | None = None
    citations: list[SlideCitation] = Field(default_factory=list)


class BigStatementSlide(SlideBase):
    layout: Literal["big_statement"] = "big_statement"
    title: str | None = None
    statement: str = ""
    speaker_notes: str | None = None
    purpose: str | None = None
    citations: list[SlideCitation] = Field(default_factory=list)


class ClosingSlide(SlideBase):
    layout: Literal["closing"] = "closing"
    title: str | None = None
    text: str = ""
    speaker_notes: str | None = None
    purpose: str | None = None
    citations: list[SlideCitation] = Field(default_factory=list)


class CodeSlide(SlideBase):
    layout: Literal["code"] = "code"
    title: str | None = None
    code: str = ""
    language: str | None = None
    speaker_notes: str | None = None
    purpose: str | None = None
    citations: list[SlideCitation] = Field(default_factory=list)


class ImageSlide(SlideBase):
    layout: Literal["image"] = "image"
    title: str | None = None
    image_url: str | None = None
    caption: str | None = None
    speaker_notes: str | None = None
    purpose: str | None = None
    citations: list[SlideCitation] = Field(default_factory=list)


class QuoteSlide(SlideBase):
    layout: Literal["quote"] = "quote"
    title: str | None = None
    quote: str = ""
    attribution: str | None = None
    speaker_notes: str | None = None
    purpose: str | None = None
    citations: list[SlideCitation] = Field(default_factory=list)


Slide = (
    TitleSlide
    | SectionSlide
    | BulletsSlide
    | TwoColumnSlide
    | BigStatementSlide
    | ClosingSlide
    | CodeSlide
    | ImageSlide
    | QuoteSlide
)


SLIDE_CLASSES_BY_LAYOUT: dict[str, type] = {
    "title": TitleSlide,
    "section": SectionSlide,
    "bullets": BulletsSlide,
    "two_column": TwoColumnSlide,
    "big_statement": BigStatementSlide,
    "closing": ClosingSlide,
    "code": CodeSlide,
    "image": ImageSlide,
    "quote": QuoteSlide,
}

VALID_LAYOUTS = list(SLIDE_CLASSES_BY_LAYOUT.keys())


def _dict_to_slide(data: dict[str, object], slide_id: str) -> Slide:
    layout = data.get("layout")
    if not isinstance(layout, str):
        raise ValueError(f"Missing or invalid layout in slide data: {layout}")
    cls = SLIDE_CLASSES_BY_LAYOUT.get(layout)
    if cls is None:
        raise ValueError(f"Unknown slide layout: {layout}")
    fields = {k: v for k, v in data.items() if k in cls.model_fields}  # type: ignore[ty:unresolved-attribute]
    fields["id"] = slide_id
    return cls(**fields)


class Presentation(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    schema_version: Literal[1] = 1
    id: str = Field(pattern=r"^presentation-[0-9a-f]{12}$")
    lecture_id: str = Field(min_length=1)
    slides: list[Slide] = Field(default_factory=list)

    @model_validator(mode="before")
    @classmethod
    def _deserialize_slides(cls, data: object) -> object:
        if not isinstance(data, dict):
            return data
        raw_slides = data.get("slides")
        if not isinstance(raw_slides, list):
            return data
        hydrated: list[BaseModel] = []
        seen: set[str] = set()
        for entry in raw_slides:
            if isinstance(entry, BaseModel):
                slide_entry: BaseModel = entry
                slide_identity = str(getattr(slide_entry, "id", ""))
                if slide_identity in seen:
                    raise ValueError(f"Duplicate slide ID: {slide_identity}")
                seen.add(slide_identity)  # type: ignore[arg-type]
                hydrated.append(entry)  # type: ignore[arg-type]
            elif isinstance(entry, dict):
                slide_id = entry.get("id", "")
                if not isinstance(slide_id, str):
                    continue
                if slide_id in seen:
                    raise ValueError(f"Duplicate slide ID: {slide_id}")
                seen.add(slide_id)
                slide = _dict_to_slide(entry, slide_id)  # type: ignore
                hydrated.append(slide)
        return {**data, "slides": hydrated}

    @model_validator(mode="after")
    def slide_identities_are_unique(self) -> Presentation:
        identities = [slide.id for slide in self.slides if hasattr(slide, "id")]
        if len(identities) != len(set(identities)):
            raise ValueError("Slide IDs must be unique")
        return self


class InvalidPresentation(ValueError):
    """A presentation file exists but does not satisfy the schema."""


def presentations_dir(workspace: Path) -> Path:
    return workspace / "presentations"


def read_presentation(workspace: Path, presentation_id: str) -> Presentation | None:
    path = presentations_dir(workspace) / f"{presentation_id}.yaml"
    if not path.exists():
        return None
    try:
        payload = yaml.safe_load(path.read_text(encoding="utf-8"))
        return Presentation.model_validate(payload)
    except (OSError, UnicodeError, yaml.YAMLError, ValidationError, ValueError) as error:
        raise InvalidPresentation(str(error)) from error


def read_presentation_for_lecture(workspace: Path, lecture_id: str) -> Presentation | None:
    directory = presentations_dir(workspace)
    if not directory.is_dir():
        return None
    for path in sorted(directory.glob("*.yaml")):
        try:
            payload = yaml.safe_load(path.read_text(encoding="utf-8"))
            if payload.get("lecture_id") == lecture_id:
                return Presentation.model_validate(payload)
        except OSError, UnicodeError, yaml.YAMLError, ValidationError, ValueError:
            continue
    return None


def list_presentations(workspace: Path) -> list[Presentation]:
    directory = presentations_dir(workspace)
    if not directory.is_dir():
        return []
    results: list[Presentation] = []
    for path in sorted(directory.glob("*.yaml")):
        try:
            payload = yaml.safe_load(path.read_text(encoding="utf-8"))
            results.append(Presentation.model_validate(payload))
        except OSError, UnicodeError, yaml.YAMLError, ValidationError, ValueError:
            continue
    return results


def _write_temporary_presentation(workspace: Path, presentation: Presentation) -> Path:
    directory = presentations_dir(workspace)
    directory.mkdir(parents=True, exist_ok=True)
    temporary_path = directory / f".{presentation.id}-{uuid4().hex}.yaml.tmp"
    serialized = yaml.safe_dump(
        presentation.model_dump(mode="json"), allow_unicode=True, sort_keys=False, width=100
    )
    with temporary_path.open("x", encoding="utf-8") as stream:
        stream.write(serialized)
        stream.flush()
        os.fsync(stream.fileno())
    return temporary_path


def write_presentation(workspace: Path, presentation: Presentation) -> None:
    target = presentations_dir(workspace) / f"{presentation.id}.yaml"
    temporary_path = _write_temporary_presentation(workspace, presentation)
    try:
        temporary_path.replace(target)
    finally:
        temporary_path.unlink(missing_ok=True)


def delete_presentation_file(workspace: Path, presentation_id: str) -> bool:
    path = presentations_dir(workspace) / f"{presentation_id}.yaml"
    if not path.exists():
        return False
    path.unlink()
    return True


def slide_by_id(presentation: Presentation, slide_id: str) -> Slide | None:
    for slide in presentation.slides:
        if slide.id == slide_id:
            return slide
    return None


class SlideArchiveRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    archived: bool


class SlideOrderRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    slide_ids: list[str] = Field(min_length=1)


def fill_slide_layout_fields(
    layout: str, fields: dict[str, object], cmd: object
) -> dict[str, object]:
    result = dict(fields)
    if layout == "title":
        result["subtitle"] = getattr(cmd, "subtitle", None)
    elif layout == "bullets":
        result["bullets"] = getattr(cmd, "bullets", []) or []
    elif layout == "two_column":
        result["left_content"] = getattr(cmd, "left_content", "") or ""
        result["right_content"] = getattr(cmd, "right_content", "") or ""
    elif layout == "big_statement":
        result["statement"] = getattr(cmd, "statement", "") or ""
    elif layout == "closing":
        result["text"] = getattr(cmd, "text", "") or ""
    elif layout == "code":
        result["code"] = getattr(cmd, "code", "") or ""
        result["language"] = getattr(cmd, "language", None)
    elif layout == "image":
        result["image_url"] = getattr(cmd, "image_url", None)
        result["caption"] = getattr(cmd, "caption", None)
    elif layout == "quote":
        result["quote"] = getattr(cmd, "quote", "") or ""
        result["attribution"] = getattr(cmd, "attribution", None)
    return result


def reorder_slides(presentation: Presentation, slide_ids: list[str]) -> Presentation:
    active_ids = {slide.id for slide in presentation.slides if not slide.archived}
    if len(slide_ids) != len(active_ids) or set(slide_ids) != active_ids:
        raise ValueError("Slide order must contain every non-archived Slide ID exactly once")
    by_id: dict[str, Slide] = {slide.id: slide for slide in presentation.slides}
    active = [by_id[sid] for sid in slide_ids]
    archived = [slide for slide in presentation.slides if slide.archived]
    return presentation.model_copy(update={"slides": [*active, *archived]})
