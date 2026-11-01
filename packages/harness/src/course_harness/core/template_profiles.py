"""Template Profiles: how a PowerPoint template's layouts map to semantic Slide layouts."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

BUILTIN_DEFAULT_ID = "_builtin-default"

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

SEMANTIC_SLOTS: dict[str, tuple[str, ...]] = {
    "title": ("title", "subtitle"),
    "section": ("title",),
    "bullets": ("title", "body"),
    "two_column": ("title", "left", "right"),
    "big_statement": ("statement",),
    "closing": ("title", "body"),
    "code": ("title", "body"),
    "image": ("title", "image"),
    "quote": ("title", "body"),
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
    slot_mappings: dict[str, int] = Field(default_factory=dict)

    @model_validator(mode="after")
    def validate_slot_mappings(self) -> TemplateLayoutMapping:
        allowed = SEMANTIC_SLOTS[self.semantic_layout]
        unknown = self.slot_mappings.keys() - set(allowed)
        if unknown:
            raise ValueError(
                f"Unknown slots for {self.semantic_layout}: {', '.join(sorted(unknown))}"
            )
        if any(index < 0 for index in self.slot_mappings.values()):
            raise ValueError("Template placeholder indices cannot be negative")
        return self


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


def builtin_default_profile() -> TemplateProfile:
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
