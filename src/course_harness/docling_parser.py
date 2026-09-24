"""Local document conversion and model provisioning for the Library."""

import io
from collections.abc import Callable
from pathlib import Path

from pydantic import BaseModel

PDF_MEDIA_TYPE = "application/pdf"
OFFICE_MEDIA_TYPES = {
    "application/vnd.openxmlformats-officedocument.presentationml.presentation": ".pptx",
    "application/vnd.openxmlformats-officedocument.wordprocessingml.document": ".docx",
}
HTML_MEDIA_TYPES = {"text/html": ".html", "application/xhtml+xml": ".xhtml"}


def models_ready(cache_dir: Path) -> bool:
    return (cache_dir / "docling-models.ready").is_file()


ModelProgress = Callable[[str, int], None]


class ModelDownloadStatus(BaseModel):
    ready: bool
    downloading: bool = False
    stage: str | None = None
    completed_steps: int = 0
    total_steps: int = 3
    error: str | None = None


def download_models(cache_dir: Path, on_progress: ModelProgress | None = None) -> None:
    """Prefetch only the layout, table, and OCR models used for PDF conversion."""
    from docling.utils.model_downloader import download_models as docling_download_models

    models_dir = cache_dir / "docling-models"
    stages = (
        "Downloading layout models",
        "Downloading table model",
        "Downloading text recognition models",
    )
    for completed, stage in enumerate(stages):
        if on_progress is not None:
            on_progress(stage, completed)
        docling_download_models(
            output_dir=models_dir,
            with_layout=completed == 0,
            with_tableformer=completed == 1,
            with_rapidocr=completed == 2,
            with_code_formula=False,
            with_picture_classifier=False,
        )
    (cache_dir / "docling-models.ready").write_text("ready\n", encoding="utf-8")
    if on_progress is not None:
        on_progress("Ready", len(stages))


def convert_document(media_type: str, content: bytes, cache_dir: Path) -> tuple[str, str]:
    """Return searchable Markdown and the structured Docling document JSON."""
    from docling.datamodel.base_models import DocumentStream, InputFormat
    from docling.datamodel.pipeline_options import PdfPipelineOptions
    from docling.document_converter import DocumentConverter, PdfFormatOption

    if media_type == PDF_MEDIA_TYPE:
        if not models_ready(cache_dir):
            raise ValueError("PDF processing models have not been downloaded.")
        extension = ".pdf"
        options = PdfPipelineOptions(artifacts_path=cache_dir / "docling-models")
        converter = DocumentConverter(
            allowed_formats=[InputFormat.PDF],
            format_options={InputFormat.PDF: PdfFormatOption(pipeline_options=options)},
        )
    elif media_type in HTML_MEDIA_TYPES:
        extension = HTML_MEDIA_TYPES[media_type]
        converter = DocumentConverter(allowed_formats=[InputFormat.HTML])
    else:
        extension = OFFICE_MEDIA_TYPES[media_type]
        converter = DocumentConverter(
            allowed_formats=[InputFormat.PPTX if extension == ".pptx" else InputFormat.DOCX]
        )

    result = converter.convert(
        DocumentStream(name=f"resource{extension}", stream=io.BytesIO(content)),
        max_file_size=100 * 1024 * 1024,
    )
    document = result.document
    if document.pages:
        label = "Slide" if extension == ".pptx" else "Page"
        notes = _powerpoint_notes(content) if extension == ".pptx" else {}
        sections = [
            f"# {label} {page_no}\n\n{document.export_to_markdown(page_no=page_no).strip()}"
            + (f"\n\nNotes: {notes[page_no]}" if page_no in notes else "")
            for page_no in sorted(document.pages)
        ]
        markdown = "\n\n".join(section for section in sections if section.strip())
    else:
        markdown = document.export_to_markdown().strip()
    return markdown + ("\n" if markdown else ""), document.model_dump_json()


def _powerpoint_notes(content: bytes) -> dict[int, str]:
    from pptx import Presentation

    presentation = Presentation(io.BytesIO(content))
    notes: dict[int, str] = {}
    for page_no, slide in enumerate(presentation.slides, start=1):
        if slide.has_notes_slide:
            note_frame = slide.notes_slide.notes_text_frame
            if note_frame is not None and note_frame.text.strip():
                notes[page_no] = note_frame.text.strip()
    return notes
