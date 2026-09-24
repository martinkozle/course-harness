"""Read-only runtime capability reporting for the local application."""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict

from course_harness import resources
from course_harness.providers import RuntimeProviderStatus, runtime_provider_status
from course_harness.runtime_paths import RuntimeLocations
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
    locations: RuntimeLocations,
) -> RuntimeDiagnostics:
    """Build a local report without network activity or durable mutations."""
    processors: dict[str, list[str]] = {}
    for media_type, processor in sorted(resources.MEDIA_TYPE_PROCESSORS.items()):
        processors.setdefault(processor, []).append(media_type)

    renderer = renderer_capability()
    provider = runtime_provider_status(locations.provider_store_path)
    return RuntimeDiagnostics(
        paths={
            "recent_workspaces": str(locations.recent_store_path),
            "chat_history": str(locations.chat_store_path),
            "library_data": str(locations.library_data_path),
            "library_cache": str(locations.library_cache_path),
            "templates_data": str(locations.templates_data_path),
            "templates_cache": str(locations.templates_cache_path),
            "releases": str(locations.release_data_path),
            "provider_configuration": str(locations.provider_store_path),
            "provider_credentials": provider.credential_storage.location,
        },
        provider=provider,
        parser=ParserCapability(
            processors=processors,
            remediation=(
                "Text, Markdown, CSV, supported code, web pages, PDF, PowerPoint, and Word "
                "documents are searchable. PDF processing downloads local models with your "
                "permission; the original file remains available as an immutable Snapshot."
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
