"""Document Processors and the Processing Quality Check (ADR 0015).

Every PDF, PowerPoint, Word, and HTML Snapshot is first converted by the quick local
processor. When its result fails the Processing Quality Check, the Course Author is
offered a better processor; nothing is upgraded without their choice or standing opt-in.
"""

from __future__ import annotations

import math
from abc import ABC, abstractmethod
from dataclasses import dataclass
from importlib.metadata import PackageNotFoundError, version
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from course_harness import docling_parser
from course_harness.docling_parser import (
    HTML_MEDIA_TYPES,
    OFFICE_MEDIA_TYPES,
    PDF_MEDIA_TYPE,
    PPTX_MEDIA_TYPE,
    PageContent,
    convert_document,
    count_garbled,
)

DOCUMENT_MEDIA_TYPES = frozenset({PDF_MEDIA_TYPE, *OFFICE_MEDIA_TYPES, *HTML_MEDIA_TYPES})
QUICK_LOCAL = "local-quick"
FULL_DOCLING = "docling"

QualityFindingKind = Literal["little-text", "image-heavy", "garbled-text"]
UnavailableAction = Literal["install", "download-models", "add-key", "choose-model"]


class QualityFinding(BaseModel):
    model_config = ConfigDict(extra="forbid")

    kind: QualityFindingKind
    message: str
    pages: list[int] = Field(default_factory=list)


class QualityReport(BaseModel):
    model_config = ConfigDict(extra="forbid")

    passed: bool
    page_count: int = Field(ge=0)
    findings: list[QualityFinding] = Field(default_factory=list)


class ProcessingRecord(BaseModel):
    """Which Document Processor produced a Derived Representation, and how it fared."""

    model_config = ConfigDict(extra="forbid")

    processor: str
    processor_label: str
    processor_version: str
    # Whether the Snapshot left the device to produce this representation.
    hosted: bool
    processed_at: str
    quality: QualityReport | None = None


class ProcessorView(BaseModel):
    """A Document Processor as the Course Author chooses among them."""

    model_config = ConfigDict(extra="forbid")

    id: str
    label: str
    description: str
    hosted: bool
    vendor: str | None = None
    media_types: list[str]
    available: bool
    unavailable_reason: str | None = None
    unavailable_action: UnavailableAction | None = None
    # A hosted processor is used only after the Course Author agrees to send documents.
    consented: bool = True


@dataclass(frozen=True)
class Unavailable:
    reason: str
    action: UnavailableAction


@dataclass(frozen=True)
class Conversion:
    markdown: str
    pages: list[PageContent]
    # Structured output worth keeping beside the Markdown, such as Docling JSON.
    structured: str | None = None
    structured_name: str = "structured.json"


class DocumentProcessor(ABC):
    id: str
    label: str
    description: str
    media_types: frozenset[str]
    hosted: bool = False
    vendor: str | None = None

    def supports(self, media_type: str) -> bool:
        return media_type in self.media_types

    @abstractmethod
    def version(self) -> str: ...

    def unavailable(self) -> Unavailable | None:
        return None

    @abstractmethod
    def convert(self, media_type: str, content: bytes) -> Conversion: ...


def _package_version(name: str) -> str:
    try:
        return version(name)
    except PackageNotFoundError:
        return "unknown"


class QuickLocalProcessor(DocumentProcessor):
    id = QUICK_LOCAL
    label = "Quick local"
    description = (
        "Reads the text already in the file on this device, in seconds. It does not read "
        "text in images, tables, or reading order across columns."
    )
    media_types = DOCUMENT_MEDIA_TYPES

    def __init__(self, cache_dir: Path) -> None:
        self.cache_dir = cache_dir

    def version(self) -> str:
        return _package_version("docling-slim")

    def convert(self, media_type: str, content: bytes) -> Conversion:
        result = convert_document(media_type, content, self.cache_dir)
        return Conversion(
            markdown=result.markdown,
            pages=result.pages,
            structured=result.structured,
            structured_name="docling.json",
        )


class FullDoclingProcessor(DocumentProcessor):
    id = FULL_DOCLING
    label = "Docling"
    description = (
        "Runs layout, table, and text recognition models on this device. It reads scans and "
        "tables, but takes minutes per document and needs gigabytes of memory."
    )
    media_types = frozenset({PDF_MEDIA_TYPE})

    def __init__(self, cache_dir: Path) -> None:
        self.cache_dir = cache_dir

    def version(self) -> str:
        return _package_version("docling")

    def unavailable(self) -> Unavailable | None:
        if not docling_parser.full_docling_installed():
            return Unavailable(
                "Install it with: uv tool install --torch-backend cpu 'course-harness[docling]' "
                "(or pip install 'course-harness[docling]'), then restart Course Harness.",
                "install",
            )
        if not docling_parser.models_ready(self.cache_dir):
            return Unavailable("Download its models before using it.", "download-models")
        return None

    def convert(self, media_type: str, content: bytes) -> Conversion:
        result = convert_document(media_type, content, self.cache_dir, use_models=True)
        return Conversion(
            markdown=result.markdown,
            pages=result.pages,
            structured=result.structured,
            structured_name="docling.json",
        )


def local_processors(cache_dir: Path) -> list[DocumentProcessor]:
    return [QuickLocalProcessor(cache_dir), FullDoclingProcessor(cache_dir)]


# A page with less text than this is probably a scan or text set as an image.
LITTLE_TEXT_CHARACTERS = 100
LITTLE_TEXT_MIN_COVERAGE = 0.1
# A page mostly covered by images holds content the quick processor cannot read.
IMAGE_HEAVY_COVERAGE = {PDF_MEDIA_TYPE: 0.5, PPTX_MEDIA_TYPE: 0.4}
IMAGE_HEAVY_CHARACTERS = {PDF_MEDIA_TYPE: 400, PPTX_MEDIA_TYPE: 300}
GARBLED_SHARE = 0.02


def check_quality(media_type: str, pages: list[PageContent]) -> QualityReport:
    """Run the Processing Quality Check over a fresh conversion."""
    findings: list[QualityFinding] = []
    count = len(pages)
    unit = "slides" if media_type == PPTX_MEDIA_TYPE else "pages"

    little_text: list[int] = []
    if media_type == PDF_MEDIA_TYPE:
        little_text = [
            page.number
            for page in pages
            if page.characters < LITTLE_TEXT_CHARACTERS
            # A short page without images is a title or a blank page, not a scan.
            and page.image_coverage >= LITTLE_TEXT_MIN_COVERAGE
        ]
        if len(little_text) >= _share(count, 0.25):
            findings.append(
                QualityFinding(
                    kind="little-text",
                    message=(
                        f"{len(little_text)} of {count} {unit} "
                        f"{'has' if len(little_text) == 1 else 'have'} little or no text. "
                        "It may be scanned, or hold its text as images."
                    ),
                    pages=little_text,
                )
            )

    if media_type in IMAGE_HEAVY_COVERAGE:
        image_heavy = [
            page.number
            for page in pages
            if page.image_coverage >= IMAGE_HEAVY_COVERAGE[media_type]
            and page.characters < IMAGE_HEAVY_CHARACTERS[media_type]
            and page.number not in little_text
        ]
        if len(image_heavy) >= _share(count, 0.2):
            findings.append(
                QualityFinding(
                    kind="image-heavy",
                    message=(
                        f"{len(image_heavy)} of {count} {unit} "
                        f"{'is' if len(image_heavy) == 1 else 'are'} mostly images. "
                        "Text inside images is not read."
                    ),
                    pages=image_heavy,
                )
            )

    characters = sum(page.characters for page in pages)
    garbled = sum(page.garbled_characters for page in pages)
    if characters and garbled / characters > GARBLED_SHARE:
        findings.append(
            QualityFinding(
                kind="garbled-text",
                message="Some text could not be decoded and appears garbled.",
                pages=[
                    page.number
                    for page in pages
                    if page.characters and page.garbled_characters / page.characters > GARBLED_SHARE
                ],
            )
        )
    return QualityReport(passed=not findings, page_count=count, findings=findings)


def _share(count: int, share: float) -> int:
    return max(1, math.ceil(count * share))


def markdown_pages(markdown: str) -> list[PageContent]:
    """Page contents of page-sectioned Markdown, for processors without structured output."""
    pages: list[PageContent] = []
    number: int | None = None
    body: list[str] = []

    def flush() -> None:
        if number is not None:
            text = "\n".join(body).strip()
            pages.append(
                PageContent(
                    number=number,
                    characters=len(text),
                    garbled_characters=count_garbled(text),
                    image_coverage=0.0,
                )
            )

    for line in markdown.split("\n"):
        heading = line.removeprefix("# ").split(" ")
        if (
            line.startswith("# ")
            and len(heading) == 2
            and heading[0] in {"Page", "Slide"}
            and heading[1].isdigit()
        ):
            flush()
            number = int(heading[1])
            body = []
            continue
        body.append(line)
    flush()
    return pages


class DocumentProcessors:
    """The Document Processors this installation can use, and the Course Author's choices."""

    def __init__(
        self,
        processors: list[DocumentProcessor],
        *,
        preferred: str | None = None,
        auto_upgrade: bool = False,
        consented_vendors: frozenset[str] = frozenset(),
    ) -> None:
        self.processors = processors
        self.preferred = preferred
        self.auto_upgrade = auto_upgrade
        self.consented_vendors = consented_vendors

    @classmethod
    def local(cls, cache_dir: Path) -> DocumentProcessors:
        return cls(local_processors(cache_dir))

    def get(self, processor_id: str) -> DocumentProcessor | None:
        return next((p for p in self.processors if p.id == processor_id), None)

    def default(self) -> DocumentProcessor:
        processor = self.get(QUICK_LOCAL)
        assert processor is not None
        return processor

    def consented(self, processor: DocumentProcessor) -> bool:
        return not processor.hosted or processor.vendor in self.consented_vendors

    def usable(self, processor: DocumentProcessor) -> bool:
        return processor.unavailable() is None and self.consented(processor)

    def views(self) -> list[ProcessorView]:
        views: list[ProcessorView] = []
        for processor in self.processors:
            unavailable = processor.unavailable()
            views.append(
                ProcessorView(
                    id=processor.id,
                    label=processor.label,
                    description=processor.description,
                    hosted=processor.hosted,
                    vendor=processor.vendor,
                    media_types=sorted(processor.media_types),
                    available=unavailable is None,
                    unavailable_reason=unavailable.reason if unavailable else None,
                    unavailable_action=unavailable.action if unavailable else None,
                    consented=self.consented(processor),
                )
            )
        return views

    def suggestion(self, media_type: str, record: ProcessingRecord | None) -> str | None:
        """The better processor to offer for a result that failed its quality check."""
        if record is None or record.quality is None or record.quality.passed:
            return None
        candidates = [self.get(self.preferred)] if self.preferred else []
        candidates.append(self.get(FULL_DOCLING))
        for candidate in candidates:
            if (
                candidate is not None
                and candidate.id != record.processor
                and candidate.supports(media_type)
                and _offerable(candidate)
            ):
                return candidate.id
        return None

    def automatic_upgrade(
        self, media_type: str, record: ProcessingRecord | None
    ) -> DocumentProcessor | None:
        """The preferred processor, when the Course Author opted in to applying it unasked."""
        if not self.auto_upgrade or self.preferred is None:
            return None
        if record is None or record.quality is None or record.quality.passed:
            return None
        processor = self.get(self.preferred)
        if (
            processor is None
            or processor.id == record.processor
            or not processor.supports(media_type)
            or not self.usable(processor)
        ):
            return None
        return processor


def _offerable(processor: DocumentProcessor) -> bool:
    """A processor is worth offering unless the Course Author must install it first."""
    unavailable = processor.unavailable()
    return unavailable is None or unavailable.action != "install"
