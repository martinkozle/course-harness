"""Hosted Document Processors: LlamaParse, Mistral OCR, and a Model Preset that reads PDFs.

Each sends the document off the device, so it is used only after the Course Author agrees
to send documents to that vendor (ADR 0015). Every one emits the same ``# Page N``
sections as the local processors, so search and Citations do not depend on the vendor.
"""

from __future__ import annotations

import asyncio
import base64
import io
import json
import threading
import time
from collections.abc import Coroutine
from pathlib import Path
from typing import Any

import httpx2

from course_harness.docling_parser import (
    DOCX_MEDIA_TYPE,
    PDF_MEDIA_TYPE,
    PPTX_MEDIA_TYPE,
    page_section,
)
from course_harness.document_processing_settings import DocumentProcessingStore
from course_harness.document_processors import (
    Conversion,
    DocumentProcessor,
    DocumentProcessors,
    Unavailable,
    local_processors,
    markdown_pages,
)
from course_harness.product_connectors import product_connector_client

LLAMAPARSE_URL = "https://api.cloud.llamaindex.ai"
MISTRAL_URL = "https://api.mistral.ai"
# Model Preset kinds whose APIs accept a PDF as message content.
PDF_CAPABLE_KINDS = frozenset({"anthropic", "openai", "openrouter", "bedrock"})
REQUEST_TIMEOUT = 300
JOB_TIMEOUT = 30 * 60


class HostedProcessingError(RuntimeError):
    """A hosted processor answered, but not with a usable result."""


def _run[T](coroutine: Coroutine[Any, Any, T]) -> T:
    """Run a coroutine from synchronous processing code, even under a running event loop."""
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        return asyncio.run(coroutine)
    result: list[T] = []
    failure: list[BaseException] = []

    def target() -> None:
        try:
            result.append(asyncio.run(coroutine))
        except BaseException as error:
            failure.append(error)

    thread = threading.Thread(target=target)
    thread.start()
    thread.join()
    if failure:
        raise failure[0]
    return result[0]


def _vendor_error(vendor: str, response: httpx2.Response) -> HostedProcessingError:
    try:
        detail = response.json()
        message = detail.get("detail") or detail.get("message") or detail.get("error")
    except ValueError, AttributeError:
        message = None
    if response.status_code in {401, 403}:
        return HostedProcessingError(f"{vendor} rejected the API key.")
    if response.status_code == 402 or response.status_code == 429:
        return HostedProcessingError(
            f"{vendor} refused the request: you may have run out of credits or hit a rate limit."
        )
    suffix = f": {message}" if isinstance(message, str) and message else ""
    return HostedProcessingError(f"{vendor} answered with HTTP {response.status_code}{suffix}")


class LlamaParseProcessor(DocumentProcessor):
    id = "llamaparse"
    label = "LlamaParse"
    description = (
        "LlamaIndex's hosted parser reads layout, tables, and scans. The free plan includes "
        "10,000 credits a month; this uses the Cost-effective tier, 3 credits per page."
    )
    media_types = frozenset({PDF_MEDIA_TYPE, PPTX_MEDIA_TYPE, DOCX_MEDIA_TYPE})
    hosted = True
    vendor = "llamaparse"

    def __init__(self, api_key: str | None, *, base_url: str = LLAMAPARSE_URL) -> None:
        self.api_key = api_key
        self.base_url = base_url

    def version(self) -> str:
        return "v2-cost_effective"

    def unavailable(self) -> Unavailable | None:
        if not self.api_key:
            return Unavailable("Add a LlamaParse API key in Settings.", "add-key")
        return None

    def convert(self, media_type: str, content: bytes) -> Conversion:
        return _run(self._convert(media_type, content))

    async def _convert(self, media_type: str, content: bytes) -> Conversion:
        headers = {"Authorization": f"Bearer {self.api_key}"}
        extension = {PDF_MEDIA_TYPE: "pdf", PPTX_MEDIA_TYPE: "pptx", DOCX_MEDIA_TYPE: "docx"}
        async with product_connector_client(timeout=REQUEST_TIMEOUT) as client:
            created = await client.post(
                f"{self.base_url}/api/v2/parse/upload",
                headers=headers,
                data={"configuration": json.dumps({"tier": "cost_effective", "version": "latest"})},
                files={"file": (f"resource.{extension[media_type]}", content, media_type)},
            )
            if created.is_error:
                raise _vendor_error(self.label, created)
            job_id = created.json()["id"]
            deadline = time.monotonic() + JOB_TIMEOUT
            delay = 1.0
            while True:
                polled = await client.get(
                    f"{self.base_url}/api/v2/parse/{job_id}",
                    headers=headers,
                    params={"expand": "markdown"},
                )
                if polled.is_error:
                    raise _vendor_error(self.label, polled)
                result = polled.json()
                status = result["job"]["status"]
                if status == "COMPLETED":
                    break
                if status in {"FAILED", "CANCELLED"}:
                    reason = result["job"].get("error_message") or status.lower()
                    raise HostedProcessingError(f"LlamaParse could not parse the file: {reason}")
                if time.monotonic() > deadline:
                    raise HostedProcessingError("LlamaParse did not finish in 30 minutes.")
                await asyncio.sleep(delay)
                delay = min(delay * 1.5, 10.0)
        label = "Slide" if media_type == PPTX_MEDIA_TYPE else "Page"
        sections = [
            page_section(label, page["page_number"], page.get("markdown", ""))
            for page in sorted(
                (result.get("markdown") or {}).get("pages", []),
                key=lambda page: page["page_number"],
            )
        ]
        markdown = "\n\n".join(sections)
        return Conversion(
            markdown=markdown + ("\n" if markdown else ""),
            pages=markdown_pages(markdown),
            structured=json.dumps(result.get("markdown")),
            structured_name="llamaparse.json",
        )


class MistralOcrProcessor(DocumentProcessor):
    id = "mistral-ocr"
    label = "Mistral OCR"
    description = (
        "Mistral's hosted OCR model reads scans, tables, and layout, page by page. It costs "
        "about $4 per 1,000 pages."
    )
    media_types = frozenset({PDF_MEDIA_TYPE})
    hosted = True
    vendor = "mistral"

    def __init__(self, api_key: str | None, *, base_url: str = MISTRAL_URL) -> None:
        self.api_key = api_key
        self.base_url = base_url

    def version(self) -> str:
        return "mistral-ocr-latest"

    def unavailable(self) -> Unavailable | None:
        if not self.api_key:
            return Unavailable("Add a Mistral API key in Settings.", "add-key")
        return None

    def convert(self, media_type: str, content: bytes) -> Conversion:
        return _run(self._convert(content))

    async def _convert(self, content: bytes) -> Conversion:
        # Sent inline, so the document never enters Mistral's separate file storage.
        document = "data:application/pdf;base64," + base64.b64encode(content).decode("ascii")
        async with product_connector_client(timeout=REQUEST_TIMEOUT) as client:
            response = await client.post(
                f"{self.base_url}/v1/ocr",
                headers={"Authorization": f"Bearer {self.api_key}"},
                json={
                    "model": "mistral-ocr-latest",
                    "document": {"type": "document_url", "document_url": document},
                },
            )
        if response.is_error:
            raise _vendor_error(self.label, response)
        result = response.json()
        sections = [
            page_section("Page", page["index"] + 1, page.get("markdown", ""))
            for page in sorted(result.get("pages", []), key=lambda page: page["index"])
        ]
        markdown = "\n\n".join(sections)
        return Conversion(
            markdown=markdown + ("\n" if markdown else ""),
            pages=markdown_pages(markdown),
            structured=json.dumps(
                {"model": result.get("model"), "usage_info": result.get("usage_info")}
            ),
            structured_name="mistral-ocr.json",
        )


TRANSCRIPTION_PROMPT = (
    "Transcribe this one-page PDF into Markdown. Keep every word of its text, in reading "
    "order, with headings, lists, and tables as Markdown. Describe each chart, diagram, or "
    "photo in one short sentence in square brackets. Output only the Markdown, with no "
    "commentary, and do not add a page heading."
)


class ModelPresetProcessor(DocumentProcessor):
    """Transcribe each PDF page with a Model Preset that accepts PDFs."""

    media_types = frozenset({PDF_MEDIA_TYPE})
    hosted = True
    concurrency = 4

    def __init__(self, provider_path: Path, preset_id: str, name: str, model: str, kind: str):
        self.provider_path = provider_path
        self.preset_id = preset_id
        self.model = model
        self.id = f"preset:{preset_id}"
        self.label = f"{name} (AI transcription)"
        self.vendor = kind
        self.description = (
            f"Sends each page to {model}, which transcribes it, including text in images. "
            "A model can paraphrase or omit content, so check what it produced. It bills "
            "your Provider Account per page."
        )

    def version(self) -> str:
        return self.model

    def convert(self, media_type: str, content: bytes) -> Conversion:
        from course_harness.course_agent import build_provider_model  # noqa: PLC0415
        from course_harness.providers import resolve_model_preset  # noqa: PLC0415

        resolved = resolve_model_preset(self.provider_path, self.preset_id)
        if resolved is None:
            raise HostedProcessingError("The Model Preset or its Provider Account is missing.")
        configuration, api_key = resolved
        pages = _split_pdf(content)
        transcripts = _run(self._transcribe(build_provider_model(configuration, api_key), pages))
        markdown = "\n\n".join(
            page_section("Page", number, text) for number, text in enumerate(transcripts, 1)
        )
        return Conversion(
            markdown=markdown + ("\n" if markdown else ""),
            pages=markdown_pages(markdown),
        )

    async def _transcribe(self, model: Any, pages: list[bytes]) -> list[str]:
        from pydantic_ai import Agent, BinaryContent  # noqa: PLC0415

        agent = Agent(model, output_type=str)
        limit = asyncio.Semaphore(self.concurrency)

        async def transcribe(page: bytes) -> str:
            async with limit:
                result = await agent.run(
                    [
                        TRANSCRIPTION_PROMPT,
                        BinaryContent(data=page, media_type=PDF_MEDIA_TYPE),
                    ]
                )
                return result.output

        return list(await asyncio.gather(*(transcribe(page) for page in pages)))


def _split_pdf(content: bytes) -> list[bytes]:
    """One single-page PDF per page, so every transcript maps to exactly one page."""
    import pypdfium2  # noqa: PLC0415

    document = pypdfium2.PdfDocument(content)
    try:
        pages: list[bytes] = []
        for index in range(len(document)):
            single = pypdfium2.PdfDocument.new()
            try:
                single.import_pages(document, [index])
                buffer = io.BytesIO()
                single.save(buffer)
                pages.append(buffer.getvalue())
            finally:
                single.close()
        return pages
    finally:
        document.close()


def installed_processors(
    cache_dir: Path, provider_path: Path, store: DocumentProcessingStore
) -> DocumentProcessors:
    """Every Document Processor this installation offers, with the Course Author's choices."""
    from course_harness.providers import read_model_catalog  # noqa: PLC0415

    settings = store.read()
    processors: list[DocumentProcessor] = [
        *local_processors(cache_dir),
        LlamaParseProcessor(store.key("llamaparse")),
        MistralOcrProcessor(store.key("mistral")),
    ]
    try:
        catalog = read_model_catalog(provider_path)
    except Exception:
        catalog = None
    if catalog is not None:
        kinds = {account.id: account.kind for account in catalog.provider_accounts}
        for preset in catalog.model_presets:
            kind = kinds.get(preset.provider_account_id)
            if kind in PDF_CAPABLE_KINDS and preset.capabilities.vision:
                processors.append(
                    ModelPresetProcessor(provider_path, preset.id, preset.name, preset.model, kind)
                )
    return DocumentProcessors(
        processors,
        preferred=settings.preferred_processor,
        auto_upgrade=settings.auto_upgrade,
        consented_vendors=frozenset(settings.consented_vendors),
    )
