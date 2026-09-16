"""Read-only runtime capability reporting for the local application."""

from __future__ import annotations

from pathlib import Path

from pydantic import BaseModel, ConfigDict

from course_harness import resources
from course_harness.providers import RuntimeProviderStatus, runtime_provider_status
from course_harness.runtime_paths import RuntimePaths
from course_harness.slide_preview import renderer_capability


class ParserCapability(BaseModel):
    model_config = ConfigDict(extra="forbid")

    processors: dict[str, list[str]]
    remediation: str


class RendererDiagnostic(BaseModel):
    model_config = ConfigDict(extra="forbid")

    available: bool
    name: str
    detail: str
    remediation: str


class RuntimeDiagnostics(BaseModel):
    model_config = ConfigDict(extra="forbid")

    paths: dict[str, str]
    provider: RuntimeProviderStatus
    parser: ParserCapability
    renderer: RendererDiagnostic


def runtime_diagnostics(
    *,
    runtime_paths: RuntimePaths,
    recent_store_path: Path,
    chat_store_path: Path,
    library_data_path: Path,
    library_cache_path: Path,
    templates_data_path: Path,
    templates_cache_path: Path,
    release_data_path: Path,
    provider_store_path: Path,
) -> RuntimeDiagnostics:
    """Build a local report without network activity or durable mutations."""
    processors: dict[str, list[str]] = {}
    for media_type, processor in sorted(resources.MEDIA_TYPE_PROCESSORS.items()):
        processors.setdefault(processor, []).append(media_type)

    renderer = renderer_capability()
    return RuntimeDiagnostics(
        paths={
            "recent_workspaces": str(recent_store_path),
            "chat_history": str(chat_store_path),
            "library_data": str(library_data_path),
            "library_cache": str(library_cache_path),
            "templates_data": str(templates_data_path),
            "templates_cache": str(templates_cache_path),
            "releases": str(release_data_path),
            "provider_configuration": str(provider_store_path),
            "provider_credentials": str(provider_store_path / "credentials.json"),
        },
        provider=runtime_provider_status(provider_store_path),
        parser=ParserCapability(
            processors=processors,
            remediation=(
                "Text, Markdown, CSV, and supported code files are searchable. Convert other "
                "documents to one of those formats before upload; the original file remains "
                "available as an immutable Snapshot."
            ),
        ),
        renderer=RendererDiagnostic(
            available=renderer.available,
            name=renderer.name,
            detail=renderer.detail,
            remediation=(
                "LibreOffice is ready for high-fidelity thumbnails."
                if renderer.available
                else (
                    "Install LibreOffice and restart Course Harness to enable "
                    "high-fidelity thumbnails."
                )
            ),
        ),
    )
