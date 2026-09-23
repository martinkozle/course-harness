import asyncio
import json
import logging
import subprocess
from collections.abc import AsyncGenerator, Awaitable, Callable
from contextlib import asynccontextmanager, suppress
from dataclasses import replace
from pathlib import Path
from typing import Literal, cast
from uuid import uuid4

from fastapi import FastAPI, HTTPException, Response
from fastapi.exceptions import RequestValidationError
from pydantic import BaseModel, ConfigDict, Field
from pydantic_ai import AgentRunResult, DeferredToolRequests
from pydantic_ai.models import Model
from pydantic_ai.ui.ag_ui import AGUIAdapter
from starlette.background import BackgroundTask
from starlette.datastructures import Headers, UploadFile
from starlette.formparsers import MultiPartException, MultiPartParser
from starlette.requests import Request
from starlette.responses import Response as StarletteResponse
from starlette.responses import StreamingResponse
from starlette.staticfiles import StaticFiles

from course_harness import docling_parser, library
from course_harness import resources as res
from course_harness import search as search_module
from course_harness import sources as sources_module
from course_harness import template_profiles as tpl
from course_harness.canonical_mutation import (
    MAX_CANONICAL_FILES,
    CanonicalMutationConflict,
    apply_canonical_mutation,
    capture_canonical_file,
    capture_canonical_files,
)
from course_harness.chat_history import (
    ChatTranscript,
    CompactionPreview,
    ConversationCatalog,
    ConversationConflict,
    activate_conversation,
    active_conversation_id,
    clear_chat_history,
    compact_conversation,
    create_conversation,
    delete_conversation,
    list_conversations,
    preview_compaction,
    read_chat_history,
    read_chat_transcript,
    read_conversation_transcript,
    save_chat_history,
    update_conversation,
)
from course_harness.course_agent import (
    AgentMode,
    CourseAgentDeps,
    CourseAgentState,
    build_provider_model,
    create_autonomous_course_agent,
    create_course_agent,
    create_reconciliation_agent,
)
from course_harness.course_plan import (
    CoursePlan,
    CoursePlanInput,
    Goal,
    InvalidCoursePlan,
    Lecture,
    Outcome,
    create_course_plan,
    create_course_plan_file,
    initialize_workspace_history,
    read_course_plan,
    serialize_course_plan,
    write_course_plan,
)
from course_harness.credential_store import CredentialStoreError
from course_harness.export import ExportError, export_presentation, validate_export_mapping
from course_harness.presentation import (
    Presentation,
    Slide,
    SlideCitation,
    SlideOrderRequest,
    SlidePatchRequest,
    list_presentations,
    read_presentation_for_lecture,
    reorder_slides,
    serialize_presentation,
    slide_by_id,
    write_presentation,
)
from course_harness.providers import (
    ModelCatalog,
    ModelPreset,
    ModelPresetRequest,
    ModelSuggestion,
    ProviderAccount,
    ProviderAccountCredentialRequest,
    ProviderAccountInUseError,
    ProviderAccountRequest,
    ProviderAccountValidator,
    ProviderCapabilityError,
    ProviderCapabilityValidator,
    ProviderConfigurationRequest,
    ProviderStatus,
    ProviderValidationError,
    delete_provider_account,
    list_provider_models,
    provider_request_for_credential_rotation,
    provider_request_for_model,
    provider_status,
    read_model_catalog,
    replace_provider_account_credential,
    require_planning_capabilities,
    resolve_active_model,
    resolve_selected_model,
    save_model_preset,
    save_provider_account,
    save_provider_configuration,
    select_model_preset,
    validate_provider_account,
    validate_provider_capabilities,
)
from course_harness.release_service import (
    CourseRelease,
    PublishReleaseRequest,
    ReleaseConflict,
    ReleaseError,
    ReleaseValidationError,
    list_releases,
    publish_release,
    read_release,
    read_release_artifact,
    regenerate_release_artifact,
)
from course_harness.release_validation import (
    InvalidWaiver,
    ReleaseSelection,
    ReleaseValidationResult,
    Waiver,
    validate_release,
)
from course_harness.runtime_diagnostics import (
    RuntimeDiagnostics,
    runtime_diagnostics,
)
from course_harness.runtime_paths import RuntimeLocations, RuntimePaths
from course_harness.slide_preview import (
    PresentationPreview,
    PreviewContext,
    build_preview,
    render_layout_backgrounds,
    render_presentation_preview,
    renderer_capability,
)
from course_harness.template_inspect import (
    inspect_template,
    map_semantic_layouts,
    suggest_mappings_with_llm,
)
from course_harness.workspace_history import (
    CourseRevision,
    CurrentState,
    DriftAcceptRequest,
    ReconciliationApplyRequest,
    RevisionCreateRequest,
    SelectiveRevertRequest,
    WorkspaceDriftChangedError,
    WorkspaceHistoryError,
    WorkspaceHistoryNotInitializedError,
    abandon_run_boundary,
    accept_workspace_drift,
    apply_reconciliation,
    begin_run_boundary,
    capture_reconciliation_context,
    checkpoint_run_mutation,
    create_revision,
    finish_run_boundary,
    list_revisions,
    mark_run_mutation,
    read_current_state,
    record_app_authored_entries,
    record_app_authored_paths,
    record_app_authored_state,
    record_restored_revision,
    restore_revision,
    revert_current_path,
)
from course_harness.workspaces import (
    WorkspaceSelectionError,
    native_folder_picker,
    read_recent_workspaces,
    remember_workspace,
    validate_workspace_path,
)

STREAM_TERMINATION_CONFIRMATION_SECONDS = 0.5
MAX_UPLOAD_FILENAME_LENGTH = 255
MAX_UPLOAD_BYTES = 100 * 1024 * 1024
MAX_MULTIPART_FIELD_BYTES = 64 * 1024
# This covers the multipart boundary and a normal file disposition. The parser
# below remains the authoritative per-file limit, because Content-Length is
# optional and includes multipart framing.
MAX_MULTIPART_OVERHEAD_BYTES = 64 * 1024


def _upload_too_large() -> HTTPException:
    return HTTPException(
        status_code=413,
        detail=f"The upload exceeds the {MAX_UPLOAD_BYTES}-byte limit.",
    )


class _BoundedUploadParser(MultiPartParser):
    """Starlette's multipart limit excludes file parts; bound those explicitly."""

    def __init__(
        self,
        headers: Headers,
        stream: AsyncGenerator[bytes],
        *,
        maximum_file_bytes: int,
        max_files: int | float = 1000,
        max_fields: int | float = 1000,
        max_part_size: int = 1024 * 1024,
    ) -> None:
        super().__init__(
            headers,
            stream,
            max_files=max_files,
            max_fields=max_fields,
            max_part_size=max_part_size,
        )
        self.maximum_file_bytes = maximum_file_bytes
        self._current_file_bytes = 0

    def on_part_begin(self) -> None:
        super().on_part_begin()
        self._current_file_bytes = 0

    def on_part_data(self, data: bytes, start: int, end: int) -> None:
        if self._current_part.file is not None:
            self._current_file_bytes += end - start
            if self._current_file_bytes > self.maximum_file_bytes:
                raise MultiPartException("File exceeded the configured upload limit.")
        super().on_part_data(data, start, end)


def _reject_oversized_upload_request(request: Request) -> None:
    content_length = request.headers.get("content-length")
    if content_length is None:
        return
    try:
        request_bytes = int(content_length)
    except ValueError as error:
        raise HTTPException(
            status_code=400, detail="The upload Content-Length is invalid."
        ) from error
    if request_bytes < 0:
        raise HTTPException(status_code=400, detail="The upload Content-Length is invalid.")
    if request_bytes > MAX_UPLOAD_BYTES + MAX_MULTIPART_OVERHEAD_BYTES:
        raise _upload_too_large()


async def _bounded_upload_form(request: Request):
    """Parse one upload without allowing a file to grow an unbounded temp file."""

    _reject_oversized_upload_request(request)
    content_type = request.headers.get("content-type", "").lower()
    if not content_type.startswith("multipart/form-data"):
        raise HTTPException(
            status_code=415,
            detail="Uploads require multipart/form-data with one file attachment.",
        )

    parser = _BoundedUploadParser(
        request.headers,
        request.stream(),
        max_files=1,
        max_fields=1,
        max_part_size=MAX_MULTIPART_FIELD_BYTES,
        maximum_file_bytes=MAX_UPLOAD_BYTES,
    )
    try:
        return await parser.parse()
    except MultiPartException as error:
        if "configured upload limit" in str(error):
            raise _upload_too_large() from error
        raise HTTPException(status_code=422, detail=f"Invalid multipart upload: {error}") from error


def _validated_upload_filename(value: object, *, fallback: str) -> str:
    if not isinstance(value, str) or not value:
        return fallback
    if (
        len(value) > MAX_UPLOAD_FILENAME_LENGTH
        or value in {".", ".."}
        or "/" in value
        or "\\" in value
        or any(ord(character) < 32 or ord(character) == 127 for character in value)
    ):
        raise HTTPException(status_code=422, detail="The upload filename is not safe.")
    return value


async def _read_bounded_upload(uploaded_file: UploadFile) -> bytes:
    content = await uploaded_file.read(MAX_UPLOAD_BYTES + 1)
    if len(content) > MAX_UPLOAD_BYTES:
        raise _upload_too_large()
    return content


class HealthResponse(BaseModel):
    status: str


class WorkspaceResponse(BaseModel):
    name: str
    path: str


class RecentWorkspaceResponse(WorkspaceResponse):
    id: str


class LectureRename(BaseModel):
    title: str = Field(min_length=1, max_length=200)


class LectureOrder(BaseModel):
    lecture_ids: list[str] = Field(min_length=1)


class CourseDetailsUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    title: str | None = Field(default=None, min_length=1, max_length=200)
    audience: str | None = Field(default=None, min_length=1, max_length=1000)
    goals: list[Goal] | None = None
    outcomes: list[Outcome] | None = None


class LectureCreate(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    title: str = Field(min_length=1, max_length=200)


class WorkspaceEntry(BaseModel):
    path: str
    kind: Literal["file", "directory"]


class ModelSelection(BaseModel):
    model_id: str = Field(min_length=1)


class TemplateProfilePin(BaseModel):
    model_config = ConfigDict(extra="forbid")

    template_profile_id: str | None = None
    template_profile_version: int | None = Field(default=None, ge=1)


class SlideRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str | None = None
    layout: str = Field(min_length=1)
    title: str | None = None
    speaker_notes: str | None = None
    purpose: str | None = None
    citations: list[SlideCitation] = Field(default_factory=list)
    archived: bool = False
    subtitle: str | None = None
    bullets: list[str] | None = None
    left_content: str | None = None
    right_content: str | None = None
    statement: str | None = None
    text: str | None = None
    code: str | None = None
    language: str | None = None
    image_url: str | None = None
    caption: str | None = None
    quote: str | None = None
    attribution: str | None = None


class PresentationRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    slides: list[SlideRequest] = Field(min_length=1)


class ReleaseValidationRequest(BaseModel):
    """The selected Course material to check before publishing a Release."""

    model_config = ConfigDict(extra="forbid")

    selection: ReleaseSelection
    waivers: list[Waiver] = Field(default_factory=list, max_length=1_000)


def create_app(
    workspace: Path | None = None,
    *,
    folder_picker: Callable[[], Path | None] = native_folder_picker,
    recent_store_path: Path | None = None,
    runtime_paths: RuntimePaths | None = None,
    provider_store_path: Path | None = None,
    chat_store_path: Path | None = None,
    library_data_path: Path | None = None,
    library_cache_path: Path | None = None,
    templates_data_path: Path | None = None,
    templates_cache_path: Path | None = None,
    release_data_path: Path | None = None,
    agent_model: Model | None = None,
    provider_validator: ProviderCapabilityValidator = validate_provider_capabilities,
    provider_model_lister: Callable[
        [Path, str], Awaitable[list[ModelSuggestion]]
    ] = list_provider_models,
    provider_account_validator: ProviderAccountValidator = validate_provider_account,
    remote_host_resolver: res.RemoteHostResolver = res.resolve_remote_host,
    precompute_template_backgrounds: bool = True,
) -> FastAPI:
    """Create the HTTP application, optionally bound to one Course Workspace."""
    if not logging.getLogger("course-harness").handlers:
        logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    logger = logging.getLogger("course-harness")

    app = FastAPI(title="Course Harness")
    background_render_tasks: set[asyncio.Task[None]] = set()
    deferred_agent_cleanup_tasks: set[asyncio.Task[None]] = set()

    def finish_background_render(task: asyncio.Task[None]) -> None:
        background_render_tasks.discard(task)
        try:
            task.result()
        except Exception as error:
            logger.warning("Template background rendering failed: %s", error)

    def finish_deferred_agent_cleanup(task: asyncio.Task[None]) -> None:
        deferred_agent_cleanup_tasks.discard(task)
        try:
            task.result()
        except BaseException as error:
            logger.warning("Deferred Course Agent cleanup failed: %s", error)

    def schedule_template_background_render(
        profile: tpl.TemplateProfile,
        template_path: Path,
    ) -> None:
        if not precompute_template_backgrounds or not renderer_capability().available:
            return
        render_task = asyncio.create_task(
            asyncio.to_thread(
                render_layout_backgrounds,
                PreviewContext(profile, template_path, templates_cache),
            )
        )
        background_render_tasks.add(render_task)
        render_task.add_done_callback(finish_background_render)

    @app.exception_handler(HTTPException)
    async def _log_http_exception(request: Request, exc: HTTPException) -> StarletteResponse:
        if exc.status_code >= 500:
            logger.exception(
                "HTTP %d on %s %s: %s",
                exc.status_code,
                request.method,
                request.url.path,
                exc.detail,
            )
        elif exc.status_code >= 400:
            logger.warning(
                "HTTP %d on %s %s: %s",
                exc.status_code,
                request.method,
                request.url.path,
                exc.detail,
            )
        return StarletteResponse(
            content=json.dumps({"detail": exc.detail}).encode("utf-8"),
            status_code=exc.status_code,
            media_type="application/json",
        )

    @app.exception_handler(RequestValidationError)
    async def _log_validation_error(
        request: Request,
        exc: RequestValidationError,
    ) -> StarletteResponse:
        errors = [
            {key: value for key, value in error.items() if key != "input"} for error in exc.errors()
        ]
        logger.warning(
            "Validation error on %s %s: %s",
            request.method,
            request.url.path,
            errors,
        )
        return StarletteResponse(
            content=json.dumps({"detail": errors}, default=str).encode("utf-8"),
            status_code=422,
            media_type="application/json",
        )

    @app.exception_handler(CredentialStoreError)
    async def _credential_storage_error(
        _request: Request,
        _exc: CredentialStoreError,
    ) -> StarletteResponse:
        return StarletteResponse(
            content=json.dumps(
                {
                    "detail": (
                        "Provider credential storage is unavailable. Unlock the operating-system "
                        "keyring or repair the private credential file shown in Runtime "
                        "diagnostics, "
                        "then try again."
                    )
                }
            ).encode("utf-8"),
            status_code=503,
            media_type="application/json",
        )

    @app.exception_handler(CanonicalMutationConflict)
    async def _canonical_mutation_conflict(
        _request: Request,
        exc: CanonicalMutationConflict,
    ) -> StarletteResponse:
        return StarletteResponse(
            content=json.dumps({"detail": str(exc)}).encode("utf-8"),
            status_code=409,
            media_type="application/json",
        )

    default_locations = RuntimeLocations.from_runtime_paths(
        runtime_paths or RuntimePaths.platform()
    )
    locations = replace(
        default_locations,
        recent_store_path=recent_store_path or default_locations.recent_store_path,
        provider_store_path=provider_store_path or default_locations.provider_store_path,
        library_data_path=library_data_path or default_locations.library_data_path,
        library_cache_path=library_cache_path or default_locations.library_cache_path,
        templates_data_path=templates_data_path or default_locations.templates_data_path,
        templates_cache_path=templates_cache_path or default_locations.templates_cache_path,
        release_data_path=release_data_path or default_locations.release_data_path,
        chat_store_path=chat_store_path or default_locations.chat_store_path,
    )
    recent_path = locations.recent_store_path
    provider_path = locations.provider_store_path
    data_dir = locations.library_data_path
    cache_dir = locations.library_cache_path
    templates_data = locations.templates_data_path
    templates_cache = locations.templates_cache_path
    release_data = locations.release_data_path
    chat_path = locations.chat_store_path
    startup_diagnostics = runtime_diagnostics(locations)
    logger.info(
        "Runtime capabilities: provider=%s; parsers=%s; LibreOffice=%s",
        "configured" if startup_diagnostics.provider.configured else "not configured",
        ", ".join(startup_diagnostics.parser.processors),
        "available" if startup_diagnostics.renderer.available else "not found",
    )
    if startup_diagnostics.provider.remediation is not None:
        logger.warning("Provider remediation: %s", startup_diagnostics.provider.remediation)
    if not startup_diagnostics.renderer.available:
        logger.warning("Renderer remediation: %s", startup_diagnostics.renderer.remediation)
    course_agent = create_course_agent()
    autonomous_agent = create_autonomous_course_agent()
    reconciliation_agent = create_reconciliation_agent()
    mutation_locks: dict[Path, asyncio.Lock] = {}
    cancel_events: dict[Path, asyncio.Event] = {}
    recovered_workspaces: set[Path] = set()

    def has_canonical_files(active: Path) -> bool:
        """Return whether an uninitialized Workspace already contains Course state."""
        for name in ("course.yaml", "sources.yaml"):
            path = active / name
            if path.exists() or path.is_symlink():
                return True
        presentations = active / "presentations"
        if presentations.is_symlink():
            return True
        if not presentations.is_dir():
            return False
        try:
            return any(path.suffix == ".yaml" for path in presentations.iterdir())
        except OSError:
            return True

    def require_workspace() -> Path:
        if workspace is None:
            raise HTTPException(status_code=409, detail="No Course Workspace is active")
        resolved = workspace.resolve()
        if resolved not in recovered_workspaces:
            try:
                # Reading Current State completes only durable crash recovery.  It must not
                # establish provenance for an existing, unreviewed repository.
                read_current_state(workspace)
            except WorkspaceHistoryNotInitializedError:
                pass
            except WorkspaceHistoryError as error:
                raise HTTPException(status_code=409, detail=str(error)) from error
            recovered_workspaces.add(resolved)
        return workspace

    def require_bound_workspace() -> Path:
        """Return the selected Workspace without inspecting mutable Current State.

        Immutable Releases are read from their tagged Course Revisions, so their
        inspection remains available when the working Course has later drifted or
        become malformed.
        """
        if workspace is None:
            raise HTTPException(status_code=409, detail="No Course Workspace is active")
        return workspace

    def require_canonical_authoring(active: Path, *, allow_uninitialized: bool = False) -> bool:
        """Refuse to overwrite unresolved Workspace Drift.

        The boolean tells the caller that a successful mutation may refresh provenance.
        An empty, uninitialized Workspace is safe to establish as app-authored state.
        """
        try:
            state = read_current_state(active)
        except WorkspaceHistoryNotInitializedError as error:
            if allow_uninitialized and not has_canonical_files(active):
                return True
            if allow_uninitialized:
                raise HTTPException(
                    status_code=409,
                    detail=(
                        "Workspace history is not initialized for existing Course state; "
                        "initialize or review it before making Course changes."
                    ),
                ) from error
            raise HTTPException(status_code=404, detail=str(error)) from error
        except WorkspaceHistoryError as error:
            raise HTTPException(status_code=409, detail=str(error)) from error

        if state.drift == "drift":
            raise HTTPException(
                status_code=409,
                detail=(
                    "Workspace Drift must be accepted or reverted before making Course changes."
                ),
            )
        if state.drift == "unknown":
            raise HTTPException(
                status_code=409,
                detail=(
                    "Workspace provenance is not established for this Course state; "
                    "review or accept Workspace Drift before making Course changes."
                ),
            )
        return True

    def record_canonical_mutation(
        active: Path,
        permitted: bool,
        expected_entries: dict[str, bytes | None] | None = None,
    ) -> None:
        if not permitted:
            return
        try:
            if expected_entries is None:
                record_app_authored_state(active)
            else:
                record_app_authored_entries(active, expected_entries)
        except ValueError as error:
            raise HTTPException(status_code=422, detail=str(error)) from error
        except WorkspaceHistoryNotInitializedError as error:
            raise HTTPException(status_code=404, detail=str(error)) from error
        except WorkspaceHistoryError as error:
            raise HTTPException(status_code=409, detail=str(error)) from error

    def read_required_course_plan(active: Path) -> CoursePlan:
        try:
            plan = read_course_plan(active)
        except InvalidCoursePlan as error:
            raise HTTPException(
                status_code=422, detail=f"course.yaml is invalid: {error}"
            ) from error
        if plan is None:
            raise HTTPException(status_code=404, detail="This Workspace does not contain a Course")
        return plan

    def require_course_plan() -> tuple[Path, CoursePlan]:
        active = require_workspace()
        return active, read_required_course_plan(active)

    def mutation_lock(active: Path) -> asyncio.Lock:
        return mutation_locks.setdefault(active.resolve(), asyncio.Lock())

    def cancel_event(active: Path) -> asyncio.Event:
        return cancel_events.setdefault(active.resolve(), asyncio.Event())

    @asynccontextmanager
    async def exclusive_mutation(active: Path):
        lock = mutation_lock(active)
        if lock.locked():
            raise HTTPException(
                status_code=409,
                detail="Another Course change is already running in this Workspace.",
            )
        await lock.acquire()
        try:
            yield
        finally:
            lock.release()

    def bind_workspace(selection: Path) -> WorkspaceResponse:
        nonlocal workspace
        if workspace is not None:
            raise HTTPException(status_code=409, detail="A Course Workspace is already active")
        try:
            candidate = validate_workspace_path(selection)
        except WorkspaceSelectionError as error:
            raise HTTPException(status_code=422, detail=str(error)) from error
        try:
            remember_workspace(recent_path, candidate)
        except OSError as error:
            raise HTTPException(
                status_code=500,
                detail="Recent Workspace data could not be saved; the Workspace was not opened",
            ) from error
        workspace = candidate
        return WorkspaceResponse(name=candidate.name, path=str(candidate))

    def choose_folder() -> Path | None:
        try:
            return folder_picker()
        except RuntimeError as error:
            raise HTTPException(status_code=503, detail=str(error)) from error

    @app.get("/api/health", response_model=HealthResponse)
    async def health() -> HealthResponse:
        return HealthResponse(status="ok")

    @app.get(
        "/api/runtime-diagnostics",
        response_model=RuntimeDiagnostics,
        response_model_exclude_none=True,
    )
    async def read_runtime_diagnostics() -> RuntimeDiagnostics:
        return runtime_diagnostics(locations)

    @app.get("/api/workspace", response_model=WorkspaceResponse)
    async def active_workspace() -> WorkspaceResponse:
        active = require_workspace()
        return WorkspaceResponse(name=active.name, path=str(active))

    @app.post("/api/workspace/close", status_code=204)
    async def close_workspace() -> Response:
        nonlocal workspace
        active = require_workspace()
        if mutation_lock(active).locked():
            raise HTTPException(
                status_code=409,
                detail="Wait for the active Course Agent run before closing this Workspace.",
            )
        cancel_events.pop(active.resolve(), None)
        recovered_workspaces.discard(active.resolve())
        workspace = None
        return Response(status_code=204)

    @app.get("/api/workspace/files", response_model=list[WorkspaceEntry])
    async def workspace_files() -> list[WorkspaceEntry]:
        active = require_workspace()
        entries: list[WorkspaceEntry] = []
        for root, directories, filenames in active.walk():
            depth = len(root.relative_to(active).parts)
            directories[:] = sorted(
                directory
                for directory in directories
                if not directory.startswith(".") and not (root / directory).is_symlink()
            )
            if depth >= 3:
                directories.clear()
            for name in directories:
                path = root / name
                entries.append(
                    WorkspaceEntry(path=path.relative_to(active).as_posix(), kind="directory")
                )
                if len(entries) == 200:
                    return entries
            for name in sorted(filenames):
                path = root / name
                if name.startswith(".") or path.is_symlink():
                    continue
                entries.append(
                    WorkspaceEntry(path=path.relative_to(active).as_posix(), kind="file")
                )
                if len(entries) == 200:
                    return entries
        return entries

    @app.get("/api/workspace/current-state", response_model=CurrentState)
    async def workspace_current_state() -> CurrentState:
        active = require_workspace()
        try:
            return read_current_state(active)
        except WorkspaceHistoryNotInitializedError as error:
            raise HTTPException(status_code=404, detail=str(error)) from error
        except WorkspaceHistoryError as error:
            raise HTTPException(status_code=409, detail=str(error)) from error

    @app.post(
        "/api/workspace/drift/accept",
        response_model=CourseRevision,
        status_code=201,
    )
    async def accept_workspace_current_state(request: DriftAcceptRequest) -> CourseRevision:
        active = require_workspace()
        async with exclusive_mutation(active):
            try:
                return accept_workspace_drift(active, request)
            except WorkspaceDriftChangedError as error:
                raise HTTPException(status_code=409, detail=str(error)) from error
            except CanonicalMutationConflict as error:
                raise HTTPException(status_code=409, detail=str(error)) from error
            except ValueError as error:
                raise HTTPException(status_code=422, detail=str(error)) from error
            except WorkspaceHistoryNotInitializedError as error:
                raise HTTPException(status_code=404, detail=str(error)) from error
            except WorkspaceHistoryError as error:
                raise HTTPException(status_code=409, detail=str(error)) from error

    @app.get("/api/workspace/revisions", response_model=list[CourseRevision])
    async def workspace_revisions() -> list[CourseRevision]:
        active = require_workspace()
        try:
            return list_revisions(active)
        except WorkspaceHistoryNotInitializedError as error:
            raise HTTPException(status_code=404, detail=str(error)) from error
        except WorkspaceHistoryError as error:
            raise HTTPException(status_code=409, detail=str(error)) from error

    def release_error_status(error: ReleaseError) -> int:
        """Use a stable HTTP classification without exposing storage details."""
        message = str(error)
        if message in {"Course Release was not found", "Release Artifact was not found"}:
            return 404
        if isinstance(error, ReleaseValidationError):
            return 422
        # A plain ReleaseError means an immutable tag, Git operation, or durable
        # artifact could not be trusted.  Do not present that as a request error.
        return 409

    def release_http_error(error: ReleaseError) -> HTTPException:
        return HTTPException(status_code=release_error_status(error), detail=str(error))

    @app.post("/api/releases/validate", response_model=ReleaseValidationResult)
    async def validate_course_release(request: ReleaseValidationRequest) -> ReleaseValidationResult:
        active = require_workspace()
        async with exclusive_mutation(active):
            try:
                before = read_current_state(active)
            except WorkspaceHistoryNotInitializedError as error:
                raise HTTPException(status_code=404, detail=str(error)) from error
            except WorkspaceHistoryError as error:
                raise HTTPException(status_code=409, detail=str(error)) from error
            if not before.validation.valid:
                findings = "; ".join(before.validation.findings[:3])
                suffix = f": {findings}" if findings else ""
                raise HTTPException(
                    status_code=422,
                    detail=(
                        "Current State must be structurally valid before Release validation"
                        f"{suffix}"
                    ),
                )
            plan = read_required_course_plan(active)
            try:
                sources = sources_module.read_sources_index(active) or sources_module.SourcesIndex()
                result = validate_release(
                    plan=plan,
                    presentations=list_presentations(active),
                    sources=sources,
                    selection=request.selection,
                    waivers=request.waivers,
                    evidence_line_counts=sources_module.read_pinned_evidence_line_counts(
                        cache_dir, sources.sources
                    ),
                )
            except sources_module.InvalidSourcesIndex as error:
                raise HTTPException(status_code=422, detail="sources.yaml is invalid") from error
            except InvalidWaiver as error:
                raise HTTPException(status_code=422, detail=str(error)) from error
            try:
                after = read_current_state(active)
            except WorkspaceHistoryNotInitializedError as error:
                raise HTTPException(status_code=404, detail=str(error)) from error
            except WorkspaceHistoryError as error:
                raise HTTPException(status_code=409, detail=str(error)) from error
            if after != before:
                raise HTTPException(
                    status_code=409,
                    detail="Current State changed while Release validation was being prepared",
                )
            return result

    @app.post("/api/releases", response_model=CourseRelease, status_code=201)
    async def publish_course_release(request: PublishReleaseRequest) -> CourseRelease:
        active = require_workspace()
        async with exclusive_mutation(active):
            try:
                state = read_current_state(active)
                # Preserve the Current State precondition (and its stable 409)
                # before consulting the disposable derived cache.
                if not state.clean or not state.validation.valid or state.drift != "clean":
                    return publish_release(
                        workspace=active,
                        release_data_root=release_data,
                        templates_data_root=templates_data,
                        request=request,
                        evidence_line_counts={},
                        library_data_root=data_dir,
                    )
                sources = sources_module.read_sources_index(active) or sources_module.SourcesIndex()
                return publish_release(
                    workspace=active,
                    release_data_root=release_data,
                    templates_data_root=templates_data,
                    request=request,
                    evidence_line_counts=sources_module.read_pinned_evidence_line_counts(
                        cache_dir, sources.sources
                    ),
                    library_data_root=data_dir,
                )
            except sources_module.InvalidSourcesIndex as error:
                raise HTTPException(status_code=422, detail="sources.yaml is invalid") from error
            except ReleaseConflict as error:
                raise HTTPException(status_code=409, detail=str(error)) from error
            except WorkspaceHistoryNotInitializedError as error:
                raise HTTPException(status_code=404, detail=str(error)) from error
            except WorkspaceHistoryError as error:
                raise HTTPException(status_code=409, detail=str(error)) from error
            except ReleaseError as error:
                raise release_http_error(error) from error

    @app.get("/api/releases", response_model=list[CourseRelease])
    async def list_course_releases() -> list[CourseRelease]:
        active = require_bound_workspace()
        try:
            return list_releases(active)
        except WorkspaceHistoryNotInitializedError as error:
            raise HTTPException(status_code=404, detail=str(error)) from error
        except WorkspaceHistoryError as error:
            raise HTTPException(status_code=409, detail=str(error)) from error
        except ReleaseError as error:
            raise release_http_error(error) from error

    @app.get("/api/releases/{slug}", response_model=CourseRelease)
    async def get_course_release(slug: str) -> CourseRelease:
        active = require_bound_workspace()
        try:
            release = read_release(active, slug)
        except WorkspaceHistoryNotInitializedError as error:
            raise HTTPException(status_code=404, detail=str(error)) from error
        except WorkspaceHistoryError as error:
            raise HTTPException(status_code=409, detail=str(error)) from error
        except ReleaseError as error:
            raise release_http_error(error) from error
        if release is None:
            raise HTTPException(status_code=404, detail="Course Release was not found")
        return release

    def release_artifact_response(content: bytes, artifact_id: str) -> Response:
        return Response(
            content=content,
            media_type="application/vnd.openxmlformats-officedocument.presentationml.presentation",
            headers={
                "Content-Disposition": f'attachment; filename="{artifact_id}.pptx"',
                "X-Content-Type-Options": "nosniff",
            },
        )

    @app.get("/api/releases/{slug}/artifacts/{artifact_id}")
    async def get_course_release_artifact(slug: str, artifact_id: str) -> Response:
        active = require_bound_workspace()
        try:
            content = read_release_artifact(
                workspace=active,
                release_data_root=release_data,
                slug=slug,
                artifact_id=artifact_id,
            )
        except WorkspaceHistoryNotInitializedError as error:
            raise HTTPException(status_code=404, detail=str(error)) from error
        except WorkspaceHistoryError as error:
            raise HTTPException(status_code=409, detail=str(error)) from error
        except ReleaseError as error:
            raise release_http_error(error) from error
        return release_artifact_response(content, artifact_id)

    @app.post("/api/releases/{slug}/artifacts/{artifact_id}/regenerate")
    async def regenerate_course_release_artifact(slug: str, artifact_id: str) -> Response:
        active = require_bound_workspace()
        try:
            content = regenerate_release_artifact(
                workspace=active,
                release_data_root=release_data,
                slug=slug,
                artifact_id=artifact_id,
                library_data_root=data_dir,
            )
        except WorkspaceHistoryNotInitializedError as error:
            raise HTTPException(status_code=404, detail=str(error)) from error
        except WorkspaceHistoryError as error:
            raise HTTPException(status_code=409, detail=str(error)) from error
        except ReleaseError as error:
            raise release_http_error(error) from error
        return release_artifact_response(content, artifact_id)

    @app.post("/api/workspace/revisions", response_model=CourseRevision, status_code=201)
    async def create_workspace_revision(request: RevisionCreateRequest) -> CourseRevision:
        active = require_workspace()
        async with exclusive_mutation(active):
            require_canonical_authoring(active)
            try:
                revision = create_revision(active, request)
                return revision
            except ValueError as error:
                raise HTTPException(status_code=422, detail=str(error)) from error
            except WorkspaceHistoryNotInitializedError as error:
                raise HTTPException(status_code=404, detail=str(error)) from error
            except WorkspaceHistoryError as error:
                raise HTTPException(status_code=409, detail=str(error)) from error

    @app.post("/api/workspace/current-state/revert", response_model=CurrentState)
    async def revert_workspace_current_state(request: SelectiveRevertRequest) -> CurrentState:
        active = require_workspace()
        async with exclusive_mutation(active):
            try:
                before = read_current_state(active)
                state = revert_current_path(active, request)
                if before.drift != "unknown":
                    record_app_authored_paths(active, {request.path})
                    state = read_current_state(active)
                return state
            except ValueError as error:
                raise HTTPException(status_code=422, detail=str(error)) from error
            except WorkspaceHistoryNotInitializedError as error:
                raise HTTPException(status_code=404, detail=str(error)) from error
            except WorkspaceHistoryError as error:
                raise HTTPException(status_code=409, detail=str(error)) from error

    @app.post("/api/workspace/revisions/{revision_id}/restore", response_model=CurrentState)
    async def restore_workspace_revision(revision_id: str) -> CurrentState:
        active = require_workspace()
        async with exclusive_mutation(active):
            try:
                restore_revision(active, revision_id)
                record_restored_revision(active, revision_id)
                return read_current_state(active)
            except ValueError as error:
                raise HTTPException(status_code=422, detail=str(error)) from error
            except WorkspaceHistoryNotInitializedError as error:
                raise HTTPException(status_code=404, detail=str(error)) from error
            except WorkspaceHistoryError as error:
                raise HTTPException(status_code=409, detail=str(error)) from error

    @app.post("/api/launcher/open-folder", response_model=WorkspaceResponse)
    async def open_workspace() -> WorkspaceResponse | Response:
        if workspace is not None:
            raise HTTPException(status_code=409, detail="A Course Workspace is already active")
        selection = choose_folder()
        if selection is None:
            return Response(status_code=204)
        return bind_workspace(selection)

    @app.post("/api/launcher/new-course", response_model=WorkspaceResponse)
    async def new_course_workspace() -> WorkspaceResponse | Response:
        if workspace is not None:
            raise HTTPException(status_code=409, detail="A Course Workspace is already active")
        selection = choose_folder()
        if selection is None:
            return Response(status_code=204)
        try:
            candidate = validate_workspace_path(selection)
        except WorkspaceSelectionError as error:
            raise HTTPException(status_code=422, detail=str(error)) from error
        if (candidate / "course.yaml").exists():
            raise HTTPException(
                status_code=409,
                detail="That folder already contains a Course; use Open Folder instead",
            )
        return bind_workspace(candidate)

    @app.get("/api/launcher/recent", response_model=list[RecentWorkspaceResponse])
    async def recent_workspaces() -> list[RecentWorkspaceResponse]:
        return [
            RecentWorkspaceResponse(id=identity, name=path.name, path=str(path))
            for identity, path in read_recent_workspaces(recent_path).items()
        ]

    @app.post("/api/launcher/recent/{identity}/open", response_model=WorkspaceResponse)
    async def reopen_workspace(identity: str) -> WorkspaceResponse:
        paths = read_recent_workspaces(recent_path)
        selection = paths.get(identity)
        if selection is None:
            raise HTTPException(status_code=404, detail="Recent Course Workspace was not found")
        try:
            return bind_workspace(selection)
        except HTTPException as error:
            if error.status_code == 422:
                raise HTTPException(
                    status_code=410,
                    detail=f"Recent Course Workspace is no longer available: {selection}",
                ) from error
            raise

    @app.get("/api/course", response_model=CoursePlan)
    async def course_plan() -> CoursePlan:
        _, plan = require_course_plan()
        return plan

    @app.get("/api/provider", response_model=ProviderStatus, response_model_exclude_none=True)
    async def configured_provider() -> ProviderStatus:
        require_workspace()
        return provider_status(provider_path)

    @app.put("/api/provider", response_model=ProviderStatus, response_model_exclude_none=True)
    async def configure_provider(request: ProviderConfigurationRequest) -> ProviderStatus:
        require_workspace()
        try:
            capabilities = await provider_validator(request)
            require_planning_capabilities(capabilities)
        except (ProviderCapabilityError, ProviderValidationError) as error:
            raise HTTPException(status_code=422, detail=str(error)) from error
        save_provider_configuration(provider_path, request, capabilities)
        return provider_status(provider_path)

    @app.get("/api/models", response_model=ModelCatalog)
    async def model_catalog() -> ModelCatalog:
        require_workspace()
        return read_model_catalog(provider_path)

    @app.post("/api/provider-accounts", response_model=ProviderAccount, status_code=201)
    async def create_provider_account(request: ProviderAccountRequest) -> ProviderAccount:
        require_workspace()
        try:
            await provider_account_validator(request)
        except ProviderValidationError as error:
            raise HTTPException(status_code=422, detail=str(error)) from error
        return save_provider_account(provider_path, request)

    @app.patch("/api/provider-accounts/{account_id}/credential", response_model=ProviderAccount)
    async def rotate_provider_account_credential(
        account_id: str, request: ProviderAccountCredentialRequest
    ) -> ProviderAccount:
        require_workspace()
        try:
            validation_request = provider_request_for_credential_rotation(
                provider_path, account_id, request
            )
            await provider_account_validator(validation_request)
            return replace_provider_account_credential(provider_path, account_id, request)
        except KeyError as error:
            raise HTTPException(status_code=404, detail="Provider Account was not found") from error
        except ProviderValidationError as error:
            raise HTTPException(status_code=422, detail=str(error)) from error

    @app.delete("/api/provider-accounts/{account_id}", response_model=ModelCatalog)
    async def remove_provider_account(
        account_id: str, delete_model_presets: bool = False
    ) -> ModelCatalog:
        require_workspace()
        try:
            return delete_provider_account(
                provider_path,
                account_id,
                delete_model_presets=delete_model_presets,
            )
        except KeyError as error:
            raise HTTPException(status_code=404, detail="Provider Account was not found") from error
        except ProviderAccountInUseError as error:
            raise HTTPException(status_code=409, detail=str(error)) from error

    @app.get(
        "/api/provider-accounts/{account_id}/models",
        response_model=list[ModelSuggestion],
    )
    async def suggest_provider_models(account_id: str) -> list[ModelSuggestion]:
        require_workspace()
        try:
            return await provider_model_lister(provider_path, account_id)
        except KeyError as error:
            raise HTTPException(status_code=404, detail="Provider Account was not found") from error
        except ProviderValidationError as error:
            raise HTTPException(status_code=422, detail=str(error)) from error

    @app.post("/api/models", response_model=ModelPreset, status_code=201)
    async def create_model_preset(request: ModelPresetRequest) -> ModelPreset:
        require_workspace()
        try:
            provider_request = provider_request_for_model(provider_path, request)
            capabilities = await provider_validator(provider_request)
            require_planning_capabilities(capabilities)
            return save_model_preset(provider_path, request, capabilities)
        except KeyError as error:
            raise HTTPException(status_code=404, detail="Provider Account was not found") from error
        except (ProviderCapabilityError, ProviderValidationError) as error:
            raise HTTPException(status_code=422, detail=str(error)) from error

    @app.put("/api/models/selected", response_model=ModelCatalog)
    async def choose_model_preset(selection: ModelSelection) -> ModelCatalog:
        require_workspace()
        try:
            return select_model_preset(provider_path, selection.model_id)
        except KeyError as error:
            raise HTTPException(status_code=404, detail="Model Preset was not found") from error

    class ConversationCreateRequest(BaseModel):
        title: str | None = Field(default=None, max_length=200)

    class ConversationUpdateRequest(BaseModel):
        title: str | None = Field(default=None, max_length=200)
        archived: bool | None = None

    class CompactionRequest(BaseModel):
        summary: str = Field(min_length=1, max_length=4000)
        revision: str

    @app.get("/api/conversations", response_model=ConversationCatalog)
    async def conversations() -> ConversationCatalog:
        return list_conversations(chat_path, require_workspace())

    @app.post("/api/conversations", response_model=ConversationCatalog, status_code=201)
    async def new_conversation(body: ConversationCreateRequest) -> ConversationCatalog:
        active = require_workspace()
        async with exclusive_mutation(active):
            try:
                return create_conversation(chat_path, active, body.title)
            except ValueError as error:
                raise HTTPException(status_code=422, detail=str(error)) from error

    @app.post("/api/conversations/{conversation_id}/activate", response_model=ConversationCatalog)
    async def open_conversation(conversation_id: str) -> ConversationCatalog:
        active = require_workspace()
        async with exclusive_mutation(active):
            try:
                return activate_conversation(chat_path, active, conversation_id)
            except KeyError as error:
                raise HTTPException(status_code=404, detail="Conversation was not found") from error
            except ConversationConflict as error:
                raise HTTPException(status_code=409, detail=str(error)) from error

    @app.patch("/api/conversations/{conversation_id}", response_model=ConversationCatalog)
    async def edit_conversation(
        conversation_id: str, body: ConversationUpdateRequest
    ) -> ConversationCatalog:
        active = require_workspace()
        async with exclusive_mutation(active):
            try:
                return update_conversation(
                    chat_path, active, conversation_id, title=body.title, archived=body.archived
                )
            except KeyError as error:
                raise HTTPException(status_code=404, detail="Conversation was not found") from error
            except ConversationConflict as error:
                raise HTTPException(status_code=409, detail=str(error)) from error
            except ValueError as error:
                raise HTTPException(status_code=422, detail=str(error)) from error

    @app.delete("/api/conversations/{conversation_id}", response_model=ConversationCatalog)
    async def remove_conversation(conversation_id: str) -> ConversationCatalog:
        active = require_workspace()
        async with exclusive_mutation(active):
            try:
                return delete_conversation(chat_path, active, conversation_id)
            except KeyError as error:
                raise HTTPException(status_code=404, detail="Conversation was not found") from error
            except ConversationConflict as error:
                raise HTTPException(status_code=409, detail=str(error)) from error

    @app.get("/api/conversations/{conversation_id}", response_model=ChatTranscript)
    async def conversation_transcript(conversation_id: str) -> ChatTranscript:
        active = require_workspace()
        try:
            return read_conversation_transcript(chat_path, active, conversation_id)
        except KeyError as error:
            raise HTTPException(status_code=404, detail="Conversation was not found") from error

    @app.post(
        "/api/conversations/{conversation_id}/compact/preview", response_model=CompactionPreview
    )
    async def preview_conversation_compaction(conversation_id: str) -> CompactionPreview:
        active = require_workspace()
        async with exclusive_mutation(active):
            try:
                return preview_compaction(chat_path, active, conversation_id)
            except KeyError as error:
                raise HTTPException(status_code=404, detail="Conversation was not found") from error
            except ConversationConflict as error:
                raise HTTPException(status_code=409, detail=str(error)) from error

    @app.post("/api/conversations/{conversation_id}/compact", response_model=ChatTranscript)
    async def confirm_conversation_compaction(
        conversation_id: str, body: CompactionRequest
    ) -> ChatTranscript:
        active = require_workspace()
        async with exclusive_mutation(active):
            try:
                return compact_conversation(
                    chat_path,
                    active,
                    conversation_id,
                    summary=body.summary,
                    revision=body.revision,
                )
            except KeyError as error:
                raise HTTPException(status_code=404, detail="Conversation was not found") from error
            except ConversationConflict as error:
                raise HTTPException(status_code=409, detail=str(error)) from error
            except ValueError as error:
                raise HTTPException(status_code=422, detail=str(error)) from error

    @app.get("/api/chat", response_model=ChatTranscript)
    async def chat_transcript() -> ChatTranscript:
        active = require_workspace()
        return read_chat_transcript(chat_path, active)

    @app.delete("/api/chat", status_code=204)
    async def clear_chat_transcript() -> Response:
        active = require_workspace()
        async with exclusive_mutation(active):
            try:
                clear_chat_history(chat_path, active)
            except ConversationConflict as error:
                raise HTTPException(status_code=409, detail=str(error)) from error
        return Response(status_code=204)

    @app.post("/api/agent/cancel", status_code=204)
    async def cancel_agent_run() -> Response:
        active = require_workspace()
        cancel_event(active).set()
        lock = mutation_lock(active)
        await lock.acquire()
        lock.release()
        return Response(status_code=204)

    @app.post("/api/agent")
    async def run_course_agent(request: Request) -> StarletteResponse:
        active = require_workspace()
        lock = mutation_lock(active)
        if lock.locked():
            raise HTTPException(
                status_code=409,
                detail="Another Course change is already running in this Workspace.",
            )
        await lock.acquire()
        cancel = cancel_event(active)
        cancel.clear()
        released = False
        run_snapshot: str | None = None
        run_provenance_permitted = False
        run_boundary_closed = False
        reconciliation_context = None
        is_reconciliation = False
        deps: CourseAgentDeps | None = None
        initial_presentation_paths: set[str] = set()

        def release_run_lock() -> None:
            nonlocal released
            if not released:
                released = True
                lock.release()

        def ensure_run_boundary() -> None:
            nonlocal run_snapshot, run_provenance_permitted
            if run_snapshot is not None:
                return
            if not is_reconciliation:
                run_provenance_permitted = require_canonical_authoring(
                    active, allow_uninitialized=True
                )
            try:
                run_snapshot = begin_run_boundary(active)
            except WorkspaceHistoryNotInitializedError:
                initialize_workspace_history(active)
                run_snapshot = begin_run_boundary(active)

        def protect_agent_mutation() -> None:
            ensure_run_boundary()
            if run_snapshot is None:
                raise RuntimeError("Course Agent run boundary was unavailable")
            mark_run_mutation(active, run_snapshot)

        def checkpoint_agent_mutation() -> None:
            nonlocal run_snapshot
            if run_snapshot is None:
                raise RuntimeError("Course Agent run boundary was unavailable")
            run_snapshot = checkpoint_run_mutation(active, run_snapshot)

        def close_run_boundary() -> None:
            nonlocal run_boundary_closed
            if run_snapshot is None or run_boundary_closed:
                return
            try:
                finish_run_boundary(active, run_snapshot)
                run_boundary_closed = True
                if not is_reconciliation:
                    if deps is None:
                        raise RuntimeError("Course Agent state was unavailable at run completion")
                    expected_entries: dict[str, bytes | None] = {}
                    if deps.course_state.course is not None:
                        expected_entries["course.yaml"] = serialize_course_plan(
                            deps.course_state.course
                        )
                    if deps.course_state.sources or (active / "sources.yaml").exists():
                        expected_entries["sources.yaml"] = sources_module.serialize_sources_index(
                            sources_module.SourcesIndex(sources=deps.course_state.sources)
                        )
                    current_presentations = {
                        f"presentations/{presentation.id}.yaml": serialize_presentation(
                            presentation
                        )
                        for presentation in deps.course_state.presentations
                    }
                    expected_entries.update(current_presentations)
                    for path in initial_presentation_paths - current_presentations.keys():
                        expected_entries[path] = None
                    if expected_entries:
                        record_canonical_mutation(
                            active, run_provenance_permitted, expected_entries
                        )
            except BaseException:
                if not run_boundary_closed:
                    abandon_run_boundary(active, run_snapshot)
                raise

        try:
            body = await request.body()
            props: dict[str, object] = {}
            try:
                body_json = json.loads(body) if body else {}
                if isinstance(body_json, dict):
                    requested_conversation_id = body_json.get("threadId")
                    actual_conversation_id = active_conversation_id(chat_path, active)
                    legacy_single_thread = (
                        len(list_conversations(chat_path, active).conversations) == 1
                    )
                    if (
                        requested_conversation_id not in (None, actual_conversation_id)
                        and not legacy_single_thread
                    ):
                        raise HTTPException(
                            status_code=409,
                            detail=("Active conversation changed; reload before sending."),
                        )
                    forwarded = body_json.get("forwardedProps", {})
                    if isinstance(forwarded, dict):
                        props = cast(dict[str, object], forwarded)
            except json.JSONDecodeError:
                pass

            requested_drift_id = props.get("reconciliationDriftId")
            if requested_drift_id is not None:
                if not isinstance(requested_drift_id, str):
                    raise HTTPException(status_code=422, detail="Workspace Drift ID is invalid")
                try:
                    reconciliation_context = capture_reconciliation_context(active)
                except ValueError as error:
                    raise HTTPException(status_code=409, detail=str(error)) from error
                except WorkspaceHistoryError as error:
                    raise HTTPException(status_code=409, detail=str(error)) from error
                if reconciliation_context.drift_id != requested_drift_id:
                    raise HTTPException(
                        status_code=409,
                        detail="Workspace Drift changed; refresh Current State before reconciling.",
                    )
                is_reconciliation = True

            active_model = resolve_active_model(provider_path)
            if active_model is None:
                raise HTTPException(
                    status_code=409,
                    detail="Configure a model provider before starting the Course Agent.",
                )
            configuration, api_key = active_model
            try:
                require_planning_capabilities(configuration.capabilities)
            except ProviderCapabilityError as error:
                raise HTTPException(status_code=422, detail=str(error)) from error

            canonical_preconditions = None
            if is_reconciliation:
                plan = None
                sources_index = None
            else:
                initial_paths = {"course.yaml", "sources.yaml"}
                presentations_directory = active / "presentations"
                if presentations_directory.is_dir() and not presentations_directory.is_symlink():
                    for candidate in presentations_directory.glob("*.yaml"):
                        if candidate.is_symlink() or not candidate.is_file():
                            continue
                        initial_paths.add(f"presentations/{candidate.name}")
                        if len(initial_paths) > MAX_CANONICAL_FILES:
                            raise CanonicalMutationConflict(
                                "Too many canonical Course files to inspect"
                            )
                canonical_preconditions = capture_canonical_files(active, initial_paths)
                try:
                    plan = read_course_plan(active)
                except InvalidCoursePlan as error:
                    raise HTTPException(
                        status_code=422, detail=f"course.yaml is invalid: {error}"
                    ) from error
                sources_index = sources_module.read_sources_index(active)
            mode = (
                AgentMode.GUIDED
                if is_reconciliation
                else AgentMode(props.get("mode", AgentMode.AUTONOMOUS))
            )

            def apply_reconciliation_patch(
                reconciliation: ReconciliationApplyRequest,
            ) -> str:
                return apply_reconciliation(active, reconciliation).id

            loaded_presentations = list_presentations(active) if not is_reconciliation else []
            initial_presentation_paths = {
                f"presentations/{presentation.id}.yaml" for presentation in loaded_presentations
            }
            deps = CourseAgentDeps(
                course_state=CourseAgentState(
                    course=plan,
                    sources=sources_index.sources if sources_index else [],
                    presentations=loaded_presentations,
                ),
                workspace=active,
                data_dir=data_dir,
                cache_dir=cache_dir,
                chat_store_path=chat_path,
                vision=configuration.capabilities.vision,
                before_mutation=None if is_reconciliation else protect_agent_mutation,
                after_mutation=None if is_reconciliation else checkpoint_agent_mutation,
                create_revision=(
                    None
                    if is_reconciliation
                    else lambda summary: (
                        create_revision(
                            active,
                            RevisionCreateRequest(summary=summary),
                            expected=canonical_preconditions,
                        ).id
                    )
                ),
                reconciliation_context=reconciliation_context,
                apply_reconciliation=(apply_reconciliation_patch if is_reconciliation else None),
                canonical_preconditions=canonical_preconditions,
            )
            conversation_id = active_conversation_id(chat_path, active)
            history = read_chat_history(chat_path, active, conversation_id)

            async def persist_if_not_cancelled(result: object) -> None:
                if not cancel.is_set():
                    completed = cast(AgentRunResult[str], result)
                    save_chat_history(chat_path, active, completed.all_messages(), conversation_id)

            model = agent_model or build_provider_model(configuration, api_key)
            active_agent = (
                reconciliation_agent
                if is_reconciliation
                else autonomous_agent
                if mode == AgentMode.AUTONOMOUS
                else course_agent
            )
            if plan is not None or is_reconciliation:
                try:
                    ensure_run_boundary()
                except ValueError as error:
                    raise HTTPException(
                        status_code=422,
                        detail=f"Current State cannot be protected before this run: {error}",
                    ) from error
                except WorkspaceHistoryError as error:
                    raise HTTPException(status_code=409, detail=str(error)) from error
            response = await AGUIAdapter.dispatch_request(
                request,
                agent=active_agent,
                model=model,
                deps=deps,
                output_type=[str, DeferredToolRequests],
                message_history=history,
                conversation_id=conversation_id,
                on_complete=persist_if_not_cancelled,
                allowed_file_url_schemes=frozenset(),
            )
        except BaseException:
            try:
                close_run_boundary()
            finally:
                release_run_lock()
            raise

        try:
            previous_background = response.background
        except BaseException:
            try:
                close_run_boundary()
            finally:
                release_run_lock()
            raise
        finalized = False
        deferred_cleanup_pending = False

        async def finalize_response(termination_confirmed: bool = True) -> None:
            nonlocal finalized
            if finalized or deferred_cleanup_pending:
                return
            if not termination_confirmed:
                return
            finalized = True
            try:
                try:
                    close_run_boundary()
                finally:
                    if previous_background is not None:
                        await previous_background()
            finally:
                release_run_lock()

        try:
            if isinstance(response, StreamingResponse):
                body_iterator = response.body_iterator

                async def guarded_body_iterator():
                    nonlocal deferred_cleanup_pending
                    iterator = None
                    next_chunk: asyncio.Future[object] | None = None
                    cancellation: asyncio.Task[bool] | None = None
                    iterator_closed = False
                    termination_confirmed = True
                    deferred_cleanup_scheduled = False

                    async def cancel_and_wait(task: asyncio.Future[object]) -> bool:
                        def consume_late_result(completed: asyncio.Future[object]) -> None:
                            with suppress(BaseException):
                                completed.result()

                        task.cancel()
                        try:
                            done, _pending = await asyncio.wait(
                                {task},
                                timeout=STREAM_TERMINATION_CONFIRMATION_SECONDS,
                            )
                        except BaseException:
                            task.add_done_callback(consume_late_result)
                            return False
                        if task not in done:
                            task.add_done_callback(consume_late_result)
                            return False
                        with suppress(BaseException):
                            task.result()
                        return True

                    def schedule_deferred_cleanup(task: asyncio.Future[object]) -> None:
                        nonlocal deferred_cleanup_pending, deferred_cleanup_scheduled
                        if deferred_cleanup_scheduled:
                            return
                        deferred_cleanup_scheduled = True
                        deferred_cleanup_pending = True

                        async def finish_after_late_termination() -> None:
                            nonlocal deferred_cleanup_pending
                            with suppress(BaseException):
                                await task
                            await close_underlying()
                            deferred_cleanup_pending = False
                            await finalize_response()

                        cleanup_task = asyncio.create_task(finish_after_late_termination())
                        deferred_agent_cleanup_tasks.add(cleanup_task)
                        cleanup_task.add_done_callback(finish_deferred_agent_cleanup)

                    async def close_underlying() -> bool:
                        nonlocal iterator_closed
                        if iterator_closed:
                            return True
                        if iterator is None:
                            return False
                        close_iterator = getattr(iterator, "aclose", None)
                        if close_iterator is None:
                            return False
                        try:
                            close = cast(Callable[[], Awaitable[None]], close_iterator)
                            await close()
                        except BaseException:
                            return False
                        iterator_closed = True
                        return True

                    try:
                        iterator = body_iterator.__aiter__()
                        while True:
                            next_chunk = asyncio.ensure_future(anext(iterator))
                            cancellation = asyncio.create_task(cancel.wait())
                            try:
                                done, _pending = await asyncio.wait(
                                    {next_chunk, cancellation},
                                    return_when=asyncio.FIRST_COMPLETED,
                                )
                            except BaseException:
                                if not await cancel_and_wait(next_chunk):
                                    termination_confirmed = False
                                    schedule_deferred_cleanup(next_chunk)
                                raise
                            if cancellation in done:
                                if not await cancel_and_wait(next_chunk):
                                    termination_confirmed = False
                                    schedule_deferred_cleanup(next_chunk)
                                break
                            cancellation.cancel()
                            with suppress(asyncio.CancelledError):
                                await cancellation
                            try:
                                yield next_chunk.result()
                            except StopAsyncIteration:
                                break
                            finally:
                                next_chunk = None
                    finally:
                        if cancellation is not None and not cancellation.done():
                            cancellation.cancel()
                            with suppress(BaseException):
                                await cancellation
                        if (
                            next_chunk is not None
                            and not next_chunk.done()
                            and not await cancel_and_wait(next_chunk)
                        ):
                            termination_confirmed = False
                            schedule_deferred_cleanup(next_chunk)
                        if not await close_underlying():
                            termination_confirmed = False
                        await finalize_response(termination_confirmed)

                response.body_iterator = guarded_body_iterator()
            response.background = BackgroundTask(finalize_response)
        except BaseException:
            try:
                close_run_boundary()
            finally:
                release_run_lock()
            raise
        return response

    @app.post("/api/course", response_model=CoursePlan, status_code=201)
    async def create_course(course_input: CoursePlanInput) -> CoursePlan:
        active = require_workspace()
        async with exclusive_mutation(active):
            expected_course = capture_canonical_file(active, "course.yaml")
            provenance_permitted = require_canonical_authoring(active, allow_uninitialized=True)
            if expected_course.content is not None:
                raise HTTPException(
                    status_code=409, detail="This Workspace already contains a Course"
                )
            plan = create_course_plan(course_input)
            try:
                initialize_workspace_history(active)
                create_course_plan_file(active, plan, expected=expected_course)
            except FileExistsError as error:
                raise HTTPException(
                    status_code=409, detail="This Workspace already contains a Course"
                ) from error
            except subprocess.CalledProcessError as error:
                raise HTTPException(
                    status_code=500, detail="Course history could not be initialized"
                ) from error
            record_canonical_mutation(
                active, provenance_permitted, {"course.yaml": serialize_course_plan(plan)}
            )
            return plan

    @app.patch("/api/course/lectures/{lecture_id}", response_model=CoursePlan)
    async def rename_lecture(lecture_id: str, rename: LectureRename) -> CoursePlan:
        active = require_workspace()
        async with exclusive_mutation(active):
            expected_course = capture_canonical_file(active, "course.yaml")
            plan = read_required_course_plan(active)
            provenance_permitted = require_canonical_authoring(active)
            if not rename.title.strip():
                raise HTTPException(status_code=422, detail="Lecture title cannot be empty")
            if all(lecture.id != lecture_id for lecture in plan.lectures):
                raise HTTPException(status_code=404, detail="Lecture was not found")
            updated = plan.model_copy(
                update={
                    "lectures": [
                        lecture.model_copy(update={"title": rename.title.strip()})
                        if lecture.id == lecture_id
                        else lecture
                        for lecture in plan.lectures
                    ]
                }
            )
            write_course_plan(active, updated, expected=expected_course)
            record_canonical_mutation(
                active, provenance_permitted, {"course.yaml": serialize_course_plan(updated)}
            )
            return updated

    @app.put("/api/course/lectures/order", response_model=CoursePlan)
    async def reorder_lectures(order: LectureOrder) -> CoursePlan:
        active = require_workspace()
        async with exclusive_mutation(active):
            expected_course = capture_canonical_file(active, "course.yaml")
            plan = read_required_course_plan(active)
            provenance_permitted = require_canonical_authoring(active)
            current_ids = [lecture.id for lecture in plan.lectures]
            if len(order.lecture_ids) != len(current_ids) or set(order.lecture_ids) != set(
                current_ids
            ):
                raise HTTPException(
                    status_code=422,
                    detail="Lecture order must contain every Lecture ID exactly once",
                )
            lectures_by_id = {lecture.id: lecture for lecture in plan.lectures}
            updated = plan.model_copy(
                update={"lectures": [lectures_by_id[identity] for identity in order.lecture_ids]}
            )
            write_course_plan(active, updated, expected=expected_course)
            record_canonical_mutation(
                active, provenance_permitted, {"course.yaml": serialize_course_plan(updated)}
            )
            return updated

    @app.patch("/api/course", response_model=CoursePlan)
    async def update_course_details(details: CourseDetailsUpdate) -> CoursePlan:
        active = require_workspace()
        async with exclusive_mutation(active):
            expected_course = capture_canonical_file(active, "course.yaml")
            plan = read_required_course_plan(active)
            provenance_permitted = require_canonical_authoring(active)
            updated = plan.model_copy(update=details.model_dump(exclude_none=True))
            write_course_plan(active, updated, expected=expected_course)
            record_canonical_mutation(
                active, provenance_permitted, {"course.yaml": serialize_course_plan(updated)}
            )
            return updated

    @app.post("/api/course/lectures", response_model=CoursePlan, status_code=201)
    async def add_lecture(lecture: LectureCreate) -> CoursePlan:
        active = require_workspace()
        async with exclusive_mutation(active):
            expected_course = capture_canonical_file(active, "course.yaml")
            plan = read_required_course_plan(active)
            provenance_permitted = require_canonical_authoring(active)
            updated = plan.model_copy(
                update={
                    "lectures": [
                        *plan.lectures,
                        Lecture(id=f"lecture-{uuid4().hex[:12]}", title=lecture.title),
                    ]
                }
            )
            write_course_plan(active, updated, expected=expected_course)
            record_canonical_mutation(
                active, provenance_permitted, {"course.yaml": serialize_course_plan(updated)}
            )
            return updated

    @app.delete("/api/course/lectures/{lecture_id}", response_model=CoursePlan)
    async def remove_lecture(lecture_id: str) -> CoursePlan:
        active = require_workspace()
        async with exclusive_mutation(active):
            expected_course = capture_canonical_file(active, "course.yaml")
            plan = read_required_course_plan(active)
            provenance_permitted = require_canonical_authoring(active)
            if all(lecture.id != lecture_id for lecture in plan.lectures):
                raise HTTPException(status_code=404, detail="Lecture was not found")
            if len(plan.lectures) == 1:
                raise HTTPException(
                    status_code=409, detail="A Course Plan needs at least one Lecture"
                )
            if read_presentation_for_lecture(active, lecture_id) is not None:
                raise HTTPException(
                    status_code=409,
                    detail="Delete this Lecture's Presentation before removing the Lecture",
                )
            updated = plan.model_copy(
                update={
                    "lectures": [lecture for lecture in plan.lectures if lecture.id != lecture_id]
                }
            )
            write_course_plan(active, updated, expected=expected_course)
            record_canonical_mutation(
                active, provenance_permitted, {"course.yaml": serialize_course_plan(updated)}
            )
            return updated

    @app.patch("/api/course/profile", response_model=CoursePlan)
    async def api_pin_template_profile(pin: TemplateProfilePin) -> CoursePlan:
        template_profile_id = pin.template_profile_id
        template_profile_version = pin.template_profile_version
        active = require_workspace()
        async with exclusive_mutation(active):
            expected_course = capture_canonical_file(active, "course.yaml")
            plan = read_required_course_plan(active)
            if template_profile_id is not None:
                try:
                    resolved = tpl.resolve_profile(
                        templates_data,
                        template_profile_id,
                        template_profile_version,
                    )
                except ValueError as error:
                    raise HTTPException(status_code=422, detail=str(error)) from error
                template_profile_version = resolved.version
            elif template_profile_version is not None:
                raise HTTPException(
                    status_code=422,
                    detail="A Template Profile version requires a Template Profile ID",
                )
            provenance_permitted = require_canonical_authoring(active)
            updated = plan.model_copy(
                update={
                    "template_profile_id": template_profile_id,
                    "template_profile_version": template_profile_version,
                }
            )
            write_course_plan(active, updated, expected=expected_course)
            record_canonical_mutation(
                active, provenance_permitted, {"course.yaml": serialize_course_plan(updated)}
            )
            return updated

    class PresentationSummary(BaseModel):
        id: str
        lecture_id: str
        slide_count: int

    @app.get("/api/presentations", response_model=list[PresentationSummary])
    async def api_list_presentations() -> list[PresentationSummary]:
        active = require_workspace()
        return [
            PresentationSummary(
                id=p.id,
                lecture_id=p.lecture_id,
                slide_count=len(p.slides),
            )
            for p in list_presentations(active)
        ]

    @app.get("/api/presentations/{lecture_id}", response_model=Presentation)
    async def api_get_presentation(lecture_id: str) -> Presentation:
        active = require_workspace()
        pres = read_presentation_for_lecture(active, lecture_id)
        if pres is None:
            raise HTTPException(
                status_code=404,
                detail="No Presentation exists for this lecture",
            )
        return pres

    def preview_context(lecture_id: str) -> tuple[Presentation, PreviewContext]:
        active, plan = require_course_plan()
        presentation = read_presentation_for_lecture(active, lecture_id)
        if presentation is None:
            raise HTTPException(status_code=404, detail="No Presentation exists for this lecture")
        try:
            profile = tpl.resolve_profile(
                templates_data,
                plan.template_profile_id,
                plan.template_profile_version,
            )
        except ValueError as error:
            raise HTTPException(status_code=404, detail=str(error)) from error
        template_path = (
            tpl.profile_dir(templates_data, profile.id) / "template.pptx"
            if profile.id != tpl.BUILTIN_DEFAULT_ID
            else None
        )
        return presentation, PreviewContext(
            profile,
            template_path,
            templates_cache,
            sources_module.source_image_resolver(active, data_dir),
        )

    @app.get(
        "/api/presentations/{lecture_id}/preview",
        response_model=PresentationPreview,
    )
    async def api_get_presentation_preview(lecture_id: str) -> PresentationPreview:
        presentation, context = preview_context(lecture_id)
        return await asyncio.to_thread(
            build_preview,
            presentation,
            context,
        )

    @app.post(
        "/api/presentations/{lecture_id}/preview/render",
        response_model=PresentationPreview,
    )
    async def api_render_presentation_preview(lecture_id: str) -> PresentationPreview:
        presentation, context = preview_context(lecture_id)
        try:
            return await asyncio.to_thread(
                render_presentation_preview,
                presentation,
                context,
            )
        except (ExportError, RuntimeError) as error:
            raise HTTPException(status_code=422, detail=str(error)) from error

    @app.get("/api/preview-assets/{profile_id}/{kind}/{version_or_key}/{filename}")
    async def api_preview_asset(
        profile_id: str,
        kind: Literal["backgrounds", "previews"],
        version_or_key: str,
        filename: str,
    ) -> StarletteResponse:
        if (
            not profile_id.startswith(("tpl-", "_builtin-default"))
            or not version_or_key.replace("v", "", 1).isalnum()
            or not filename.endswith(".png")
            or any(part in filename for part in ("..", "/", "\\"))
        ):
            raise HTTPException(status_code=404, detail="Not found")
        asset = templates_cache / profile_id / kind / version_or_key / filename
        if not asset.is_file():
            raise HTTPException(status_code=404, detail="Not found")
        return StarletteResponse(content=asset.read_bytes(), media_type="image/png")

    @app.post("/api/presentations/{lecture_id}", response_model=Presentation, status_code=201)
    async def api_create_presentation(
        lecture_id: str, request: PresentationRequest
    ) -> Presentation:
        from uuid import uuid4

        from course_harness.presentation import (
            SLIDE_CLASSES_BY_LAYOUT,
            fill_slide_layout_fields,
        )

        active = require_workspace()
        async with exclusive_mutation(active):
            expected_course = capture_canonical_file(active, "course.yaml")
            plan = read_required_course_plan(active)
            if all(lec.id != lecture_id for lec in plan.lectures):
                raise HTTPException(status_code=404, detail="Lecture was not found")

            existing = read_presentation_for_lecture(active, lecture_id)
            if existing:
                raise HTTPException(
                    status_code=409,
                    detail="This lecture already has a Presentation",
                )

            slides = []
            for cmd in request.slides:
                layout = cmd.layout
                if layout not in SLIDE_CLASSES_BY_LAYOUT:
                    raise HTTPException(status_code=422, detail=f"Unknown slide layout: {layout}")
                cls = SLIDE_CLASSES_BY_LAYOUT[layout]
                slide_id = cmd.id or f"slide-{uuid4().hex[:12]}"
                fields: dict[str, object] = {
                    "id": slide_id,
                    "title": cmd.title,
                    "speaker_notes": cmd.speaker_notes,
                    "purpose": cmd.purpose,
                    "citations": cmd.citations,
                    "archived": cmd.archived,
                }
                fields = fill_slide_layout_fields(layout, fields, cmd)
                model_fields = cls.model_fields  # type: ignore
                filtered = {k: v for k, v in fields.items() if k in model_fields}
                slides.append(cls(**filtered))

            presentation_id = f"presentation-{uuid4().hex[:12]}"
            presentation = Presentation(
                id=presentation_id,
                lecture_id=lecture_id,
                slides=slides,
            )
            updated_lectures = [
                lec.model_copy(update={"presentation_id": presentation_id})
                if lec.id == lecture_id
                else lec
                for lec in plan.lectures
            ]
            updated_plan = plan.model_copy(update={"lectures": updated_lectures})
            presentation_path = f"presentations/{presentation.id}.yaml"
            expected_presentation = capture_canonical_file(active, presentation_path)
            provenance_permitted = require_canonical_authoring(active)
            apply_canonical_mutation(
                active,
                expected={
                    "course.yaml": expected_course,
                    presentation_path: expected_presentation,
                },
                updates={
                    "course.yaml": serialize_course_plan(updated_plan),
                    presentation_path: serialize_presentation(presentation),
                },
            )
            record_canonical_mutation(
                active,
                provenance_permitted,
                {
                    "course.yaml": serialize_course_plan(updated_plan),
                    f"presentations/{presentation.id}.yaml": serialize_presentation(presentation),
                },
            )

            return presentation

    @app.delete("/api/presentations/{lecture_id}", status_code=204)
    async def api_delete_presentation(lecture_id: str) -> Response:
        active = require_workspace()
        async with exclusive_mutation(active):
            expected_course = capture_canonical_file(active, "course.yaml")
            plan = read_required_course_plan(active)
            planned_lecture = next((lec for lec in plan.lectures if lec.id == lecture_id), None)
            expected_presentation = (
                capture_canonical_file(
                    active, f"presentations/{planned_lecture.presentation_id}.yaml"
                )
                if planned_lecture is not None and planned_lecture.presentation_id is not None
                else None
            )
            pres = read_presentation_for_lecture(active, lecture_id)
            if pres is None:
                raise HTTPException(
                    status_code=404,
                    detail="No Presentation exists for this lecture",
                )
            provenance_permitted = require_canonical_authoring(active)
            updated_lectures = [
                lec.model_copy(update={"presentation_id": None}) if lec.id == lecture_id else lec
                for lec in plan.lectures
            ]
            updated_plan = plan.model_copy(update={"lectures": updated_lectures})
            presentation_path = f"presentations/{pres.id}.yaml"
            apply_canonical_mutation(
                active,
                expected={
                    "course.yaml": expected_course,
                    presentation_path: expected_presentation
                    or capture_canonical_file(active, presentation_path),
                },
                updates={
                    "course.yaml": serialize_course_plan(updated_plan),
                    presentation_path: None,
                },
            )
            record_canonical_mutation(
                active,
                provenance_permitted,
                {
                    "course.yaml": serialize_course_plan(updated_plan),
                    f"presentations/{pres.id}.yaml": None,
                },
            )
            return Response(status_code=204)

    @app.patch("/api/presentations/{lecture_id}/slides/{slide_id}", response_model=Presentation)
    async def api_patch_slide(
        lecture_id: str, slide_id: str, request: SlidePatchRequest
    ) -> Presentation:
        active = require_workspace()
        async with exclusive_mutation(active):
            current_plan = read_required_course_plan(active)
            current_lecture = next(
                (lecture for lecture in current_plan.lectures if lecture.id == lecture_id), None
            )
            expected_presentation = (
                capture_canonical_file(
                    active, f"presentations/{current_lecture.presentation_id}.yaml"
                )
                if current_lecture is not None and current_lecture.presentation_id is not None
                else None
            )
            pres = read_presentation_for_lecture(active, lecture_id)
            if pres is None:
                raise HTTPException(
                    status_code=404,
                    detail="No Presentation exists for this lecture",
                )
            existing = slide_by_id(pres, slide_id)
            if existing is None:
                raise HTTPException(status_code=404, detail="Slide was not found")

            update: dict[str, object] = {}
            valid_fields = set(type(existing).model_fields.keys())
            if request.title is not None and "title" in valid_fields:
                update["title"] = request.title
            if request.speaker_notes is not None and "speaker_notes" in valid_fields:
                update["speaker_notes"] = request.speaker_notes
            if request.purpose is not None and "purpose" in valid_fields:
                update["purpose"] = request.purpose
            if request.citations is not None and "citations" in valid_fields:
                update["citations"] = request.citations
            if request.subtitle is not None and "subtitle" in valid_fields:
                update["subtitle"] = request.subtitle
            if request.bullets is not None and "bullets" in valid_fields:
                update["bullets"] = request.bullets
            if request.left_content is not None and "left_content" in valid_fields:
                update["left_content"] = request.left_content
            if request.right_content is not None and "right_content" in valid_fields:
                update["right_content"] = request.right_content
            if request.statement is not None and "statement" in valid_fields:
                update["statement"] = request.statement
            if request.text is not None and "text" in valid_fields:
                update["text"] = request.text
            if request.code is not None and "code" in valid_fields:
                update["code"] = request.code
            if request.language is not None and "language" in valid_fields:
                update["language"] = request.language
            if request.image_source_id is not None and "image_source_id" in valid_fields:
                if request.image_source_id and (
                    sources_module.source_image_resolver(active, data_dir)(request.image_source_id)
                    is None
                ):
                    raise HTTPException(
                        status_code=422,
                        detail="The image must be an image Source admitted to this Course.",
                    )
                update["image_source_id"] = request.image_source_id or None
            if request.image_url is not None and "image_url" in valid_fields:
                update["image_url"] = request.image_url
            if request.caption is not None and "caption" in valid_fields:
                update["caption"] = request.caption
            if request.quote is not None and "quote" in valid_fields:
                update["quote"] = request.quote
            if request.attribution is not None and "attribution" in valid_fields:
                update["attribution"] = request.attribution

            updated_slide = existing.model_copy(update=update)
            if request.archived is not None and request.archived != existing.archived:
                updated_slide = updated_slide.model_copy(update={"archived": request.archived})

            reordered: list[Slide]
            if request.archived is True and not existing.archived:
                reordered = (
                    [s for s in pres.slides if s.id != slide_id and not s.archived]
                    + [s for s in pres.slides if s.id != slide_id and s.archived]
                    + [updated_slide]
                )
            elif request.archived is False and existing.archived:
                reordered = (
                    [s for s in pres.slides if s.id != slide_id and not s.archived]
                    + [updated_slide]
                    + [s for s in pres.slides if s.id != slide_id and s.archived]
                )
            else:
                reordered = [s if s.id != slide_id else updated_slide for s in pres.slides]
            updated = pres.model_copy(update={"slides": reordered})
            presentation_path = f"presentations/{updated.id}.yaml"
            provenance_permitted = require_canonical_authoring(active)
            write_presentation(
                active,
                updated,
                expected=expected_presentation or capture_canonical_file(active, presentation_path),
            )
            record_canonical_mutation(
                active,
                provenance_permitted,
                {f"presentations/{updated.id}.yaml": serialize_presentation(updated)},
            )
            return updated

    @app.put("/api/presentations/{lecture_id}/slides/order", response_model=Presentation)
    async def api_reorder_slides(lecture_id: str, request: SlideOrderRequest) -> Presentation:
        active = require_workspace()
        async with exclusive_mutation(active):
            current_plan = read_required_course_plan(active)
            current_lecture = next(
                (lecture for lecture in current_plan.lectures if lecture.id == lecture_id), None
            )
            expected_presentation = (
                capture_canonical_file(
                    active, f"presentations/{current_lecture.presentation_id}.yaml"
                )
                if current_lecture is not None and current_lecture.presentation_id is not None
                else None
            )
            pres = read_presentation_for_lecture(active, lecture_id)
            if pres is None:
                raise HTTPException(
                    status_code=404,
                    detail="No Presentation exists for this lecture",
                )
            try:
                updated = reorder_slides(pres, request.slide_ids)
            except ValueError as error:
                raise HTTPException(status_code=422, detail=str(error)) from error
            presentation_path = f"presentations/{updated.id}.yaml"
            provenance_permitted = require_canonical_authoring(active)
            write_presentation(
                active,
                updated,
                expected=expected_presentation or capture_canonical_file(active, presentation_path),
            )
            record_canonical_mutation(
                active,
                provenance_permitted,
                {f"presentations/{updated.id}.yaml": serialize_presentation(updated)},
            )
            return updated

    @app.get("/api/presentations/{lecture_id}/export")
    async def api_export_presentation(lecture_id: str, profile: str | None = None) -> Response:
        active, plan = require_course_plan()
        pres = read_presentation_for_lecture(active, lecture_id)
        if pres is None:
            raise HTTPException(
                status_code=404,
                detail="No Presentation exists for this lecture",
            )
        try:
            if profile is not None:
                resolved_profile = tpl.resolve_profile(templates_data, profile)
            else:
                resolved_profile = tpl.resolve_profile(
                    templates_data,
                    plan.template_profile_id,
                    plan.template_profile_version,
                )
        except ValueError as error:
            raise HTTPException(status_code=404, detail=str(error)) from error

        template_file = None
        if resolved_profile.id != tpl.BUILTIN_DEFAULT_ID:
            template_file = tpl.profile_dir(templates_data, resolved_profile.id) / "template.pptx"

        if resolved_profile.id != tpl.BUILTIN_DEFAULT_ID:
            issues = validate_export_mapping(resolved_profile, template_file)
            blocking = [i for i in issues if i["level"] == "blocking"]
            if blocking:
                errors = "; ".join(i["message"] for i in blocking)
                raise HTTPException(status_code=422, detail=f"Template validation failed: {errors}")

        try:
            pptx_bytes = export_presentation(
                pres,
                profile=resolved_profile,
                template_path=template_file,
                image_resolver=sources_module.source_image_resolver(active, data_dir),
            )
        except ExportError as error:
            raise HTTPException(status_code=422, detail=str(error)) from error
        return Response(
            content=pptx_bytes,
            media_type="application/vnd.openxmlformats-officedocument.presentationml.presentation",
            headers={"Content-Disposition": f'attachment; filename="{pres.id}.pptx"'},
        )

    @app.get("/api/sources", response_model=list[sources_module.Source])
    async def list_sources() -> list[sources_module.Source]:
        active = require_workspace()
        index = sources_module.read_sources_index(active)
        if index is None:
            return []
        return index.sources

    @app.post(
        "/api/sources",
        response_model=sources_module.Source,
        status_code=201,
    )
    async def admit_source_route(
        request: sources_module.SourceAdmissionRequest,
    ) -> sources_module.Source:
        active = require_workspace()
        async with exclusive_mutation(active):
            expected_sources = capture_canonical_file(active, "sources.yaml")
            before_index = (
                sources_module.read_sources_index(active) or sources_module.SourcesIndex()
            )
            provenance_permitted = require_canonical_authoring(active, allow_uninitialized=True)
            if not (active / ".git").exists():
                try:
                    initialize_workspace_history(active)
                except subprocess.CalledProcessError as error:
                    raise HTTPException(
                        status_code=500, detail="Course history could not be initialized"
                    ) from error
            try:
                source = sources_module.admit_source(
                    active,
                    data_dir,
                    cache_dir,
                    request.resource_id,
                    label=request.label,
                    expected=expected_sources,
                )
                expected_index = before_index.model_copy(
                    update={"sources": [*before_index.sources, source]}
                )
                record_canonical_mutation(
                    active,
                    provenance_permitted,
                    {"sources.yaml": sources_module.serialize_sources_index(expected_index)},
                )
                return source
            except CanonicalMutationConflict as error:
                raise HTTPException(status_code=409, detail=str(error)) from error
            except ValueError as error:
                raise HTTPException(status_code=422, detail=str(error)) from error

    @app.get("/api/sources/{source_id}", response_model=sources_module.Source)
    async def source_detail(source_id: str) -> sources_module.Source:
        active = require_workspace()
        index = sources_module.read_sources_index(active)
        if index is None:
            raise HTTPException(status_code=404, detail="Source was not found")
        source = next((s for s in index.sources if s.id == source_id), None)
        if source is None:
            raise HTTPException(status_code=404, detail="Source was not found")
        return source

    @app.delete("/api/sources/{source_id}", status_code=204)
    async def remove_source(source_id: str) -> Response:
        active = require_workspace()
        async with exclusive_mutation(active):
            expected_sources = capture_canonical_file(active, "sources.yaml")
            index = sources_module.read_sources_index(active)
            if index is None:
                raise HTTPException(status_code=404, detail="Source was not found")
            remaining = [s for s in index.sources if s.id != source_id]
            if len(remaining) == len(index.sources):
                raise HTTPException(status_code=404, detail="Source was not found")
            provenance_permitted = require_canonical_authoring(active)
            updated = sources_module.SourcesIndex(version=index.version, sources=remaining)
            sources_module.write_sources_index(active, updated, expected=expected_sources)
            record_canonical_mutation(
                active,
                provenance_permitted,
                {"sources.yaml": sources_module.serialize_sources_index(updated)},
            )
            return Response(status_code=204)

    @app.post(
        "/api/sources/search",
        response_model=list[search_module.GroupedSearchResult],
    )
    async def search_sources(
        request: search_module.SearchRequest,
    ) -> list[search_module.GroupedSearchResult]:
        active = require_workspace()
        index = sources_module.read_sources_index(active)
        if index is None or not index.sources:
            return []

        sources_by_version: dict[str, tuple[str, str, str]] = {}
        for s in index.sources:
            sources_by_version[s.source_version_id] = (s.id, s.resource_id, s.label)

        hits = search_module.search_raw(cache_dir, request.query, limit=request.limit or 10)
        logger.info(
            "Search '%s': %d FTS5 hits, %d sources (version keys: %s)",
            request.query,
            len(hits),
            len(sources_by_version),
            [k[:12] + "…" for k in sources_by_version],
        )
        for h in hits:
            logger.info(
                "  hit content_hash=%s… matched=%s",
                h.content_hash[:12],
                h.content_hash in sources_by_version,
            )

        return search_module.enrich_search_results(hits, sources_by_version)

    @app.get("/api/sources/{source_id}/image")
    async def source_image(source_id: str) -> StarletteResponse:
        active = require_workspace()
        image = sources_module.source_image_resolver(active, data_dir)(source_id)
        if image is None:
            raise HTTPException(status_code=404, detail="Image Source was not found")
        return StarletteResponse(
            content=await asyncio.to_thread(image.path.read_bytes),
            media_type=image.media_type,
        )

    @app.get("/api/sources/{source_id}/content")
    async def source_content(
        source_id: str,
        max_chars: int = 4000,
        line_start: int | None = None,
        line_end: int | None = None,
    ) -> StarletteResponse:
        active = require_workspace()
        index = sources_module.read_sources_index(active)
        if index is None:
            raise HTTPException(status_code=404, detail="Source was not found")
        source = next((s for s in index.sources if s.id == source_id), None)
        if source is None:
            raise HTTPException(status_code=404, detail="Source was not found")

        extracted = library.derived_dir(cache_dir) / source.source_version_id / "extracted.md"
        if not extracted.is_file():
            raise HTTPException(
                status_code=404,
                detail=(
                    "Extracted content is not available. Try processing the underlying Resource."
                ),
            )

        try:
            content = extracted.read_text(encoding="utf-8")
        except (OSError, UnicodeError) as error:
            raise HTTPException(
                status_code=500,
                detail="Could not read extracted content",
            ) from error

        if line_start is not None or line_end is not None:
            lines = content.split("\n")
            start = max(0, line_start or 0)
            end = min(len(lines) - 1, line_end if line_end is not None else len(lines) - 1)
            if start >= len(lines) or end < start:
                raise HTTPException(status_code=422, detail="Invalid coordinate range")
            content = "\n".join(lines[start : end + 1])

        if len(content) > max_chars:
            content = content[:max_chars]

        return StarletteResponse(content=content, media_type="text/plain")

    @app.delete("/api/resources/cache", status_code=204)
    async def clear_resource_cache() -> Response:
        require_workspace()
        search_module.rebuild_index(cache_dir, data_dir)
        logger.info("Search index rebuilt from processed resources")
        return Response(status_code=204)

    @app.delete("/api/resources/{resource_id}", status_code=204)
    async def remove_resource(resource_id: str) -> Response:
        require_workspace()
        async with resource_registry_lock:
            removed = library.remove_resource(data_dir, cache_dir, resource_id)
        if not removed:
            raise HTTPException(status_code=404, detail="Resource was not found")
        remote_job_states.pop(resource_id, None)
        return Response(status_code=204)

    resource_registry_lock = asyncio.Lock()
    remote_job_states: dict[str, tuple[res.ProcessingStatus, str | None]] = {}
    remote_jobs: dict[str, asyncio.Task[None]] = {}

    def with_remote_job_state(state: res.ResourceState) -> res.ResourceState:
        job_state = remote_job_states.get(state.resource_id)
        if job_state is None:
            return state
        status, error = job_state
        return state.model_copy(update={"status": status, "error": error})

    def schedule_remote_capture(resource_id: str, url: str, media_type: str | None) -> None:
        remote_job_states[resource_id] = ("processing", None)

        async def capture_and_process() -> None:
            try:
                content, resolved_type = await library.fetch_remote_resource(
                    url, media_type, host_resolver=remote_host_resolver
                )
                async with resource_registry_lock:
                    snapshot = await asyncio.to_thread(
                        library.save_remote_snapshot,
                        data_dir,
                        resource_id,
                        content,
                        resolved_type,
                    )
                if snapshot is None:
                    return
                result = await asyncio.to_thread(
                    library.reprocess_resource, data_dir, cache_dir, resource_id
                )
                if library.get_resource_state(data_dir, cache_dir, resource_id) is None:
                    remote_job_states.pop(resource_id, None)
                elif result is not None and result.status != "ready":
                    remote_job_states[resource_id] = (result.status, result.error)
                else:
                    remote_job_states.pop(resource_id, None)
            except Exception as error:
                logger.warning("Remote Resource %s could not be prepared: %s", resource_id, error)
                if library.get_resource_state(data_dir, cache_dir, resource_id) is not None:
                    remote_job_states[resource_id] = ("failed", str(error))
            finally:
                remote_jobs.pop(resource_id, None)

        remote_jobs[resource_id] = asyncio.create_task(capture_and_process())

    @app.get("/api/resources", response_model=list[res.ResourceState])
    async def list_resources() -> list[res.ResourceState]:
        require_workspace()
        return [
            with_remote_job_state(state)
            for state in library.list_resources_with_state(data_dir, cache_dir)
        ]

    model_download_lock = asyncio.Lock()
    model_download_status = docling_parser.ModelDownloadStatus(
        ready=docling_parser.models_ready(cache_dir)
    )

    def require_pdf_models(media_type: str) -> None:
        if media_type == docling_parser.PDF_MEDIA_TYPE and not docling_parser.models_ready(
            cache_dir
        ):
            raise HTTPException(
                status_code=409,
                detail="Download PDF processing models before processing this resource.",
            )

    @app.get("/api/resources/parser-models", response_model=docling_parser.ModelDownloadStatus)
    async def parser_models_status() -> docling_parser.ModelDownloadStatus:
        require_workspace()
        model_download_status.ready = docling_parser.models_ready(cache_dir)
        return model_download_status

    @app.post("/api/resources/parser-models", response_model=docling_parser.ModelDownloadStatus)
    async def install_parser_models() -> docling_parser.ModelDownloadStatus:
        require_workspace()
        async with model_download_lock:
            if not docling_parser.models_ready(cache_dir):
                model_download_status.downloading = True
                model_download_status.error = None
                model_download_status.completed_steps = 0

                def update_progress(stage: str, completed: int) -> None:
                    model_download_status.stage = stage
                    model_download_status.completed_steps = completed

                try:
                    await asyncio.to_thread(
                        docling_parser.download_models, cache_dir, update_progress
                    )
                except Exception as error:
                    logger.exception("Document model download failed")
                    model_download_status.error = (
                        "Document models could not be downloaded. Please retry."
                    )
                    raise HTTPException(
                        status_code=502,
                        detail="Document models could not be downloaded. Please retry.",
                    ) from error
                finally:
                    model_download_status.downloading = False
            model_download_status.ready = True
            model_download_status.stage = "Ready"
            model_download_status.completed_steps = model_download_status.total_steps
        return model_download_status

    @app.get("/api/resources/{resource_id}", response_model=res.ResourceState)
    async def resource_detail(resource_id: str) -> res.ResourceState:
        require_workspace()
        state = library.get_resource_state(data_dir, cache_dir, resource_id)
        if state is None:
            raise HTTPException(status_code=404, detail="Resource was not found")
        return with_remote_job_state(state)

    @app.post("/api/resources", response_model=res.Resource, status_code=201)
    async def register_resource_route(request: res.ResourceRegistrationRequest) -> res.Resource:
        require_workspace()
        registry = library.registry_path(data_dir)
        async with resource_registry_lock:
            return res.register_resource(registry, request)

    @app.post("/api/resources/{resource_id}/process", response_model=res.ResourceState)
    async def process_resource(resource_id: str) -> res.ResourceState:
        require_workspace()
        index = res.read_library_index(library.registry_path(data_dir))
        resource = next((r for r in index.resources if r.id == resource_id), None)
        if resource is None:
            raise HTTPException(status_code=404, detail="Resource was not found")
        require_pdf_models(resource.media_type)

        from pathlib import Path as _Path

        if resource.kind == "local-file":
            location = _Path(resource.location)
            if not location.is_file():
                raise HTTPException(
                    status_code=422,
                    detail="The registered file is no longer accessible.",
                )
            content = location.read_bytes()
        else:
            raise HTTPException(
                status_code=422,
                detail="Uploaded Resources must be processed with their content payload.",
            )

        async with resource_registry_lock:
            result = await asyncio.to_thread(
                library.process_existing_resource, data_dir, cache_dir, resource_id, content
            )
        if result is None:
            raise HTTPException(status_code=404, detail="Resource was not found")
        if result.status != "ready":
            raise HTTPException(status_code=422, detail=result.error or "Processing failed.")
        return result

    @app.post("/api/resources/{resource_id}/reprocess", response_model=res.ResourceState)
    async def reprocess_resource(resource_id: str) -> res.ResourceState:
        require_workspace()
        if resource_id in remote_jobs:
            raise HTTPException(status_code=409, detail="This Resource is already processing.")
        index = res.read_library_index(library.registry_path(data_dir))
        resource = next((r for r in index.resources if r.id == resource_id), None)
        if resource is not None:
            require_pdf_models(resource.media_type)
        result = await asyncio.to_thread(
            library.reprocess_resource, data_dir, cache_dir, resource_id
        )
        if result is None:
            raise HTTPException(
                status_code=404, detail="Resource was not found or has no Snapshot."
            )
        if result.status != "ready":
            raise HTTPException(status_code=422, detail=result.error or "Reprocessing failed.")
        remote_job_states.pop(resource_id, None)
        return result

    @app.post("/api/resources/upload", response_model=res.ResourceState, status_code=201)
    async def upload_resource(request: Request) -> res.ResourceState:
        require_workspace()
        form = await _bounded_upload_form(request)
        uploaded_file = form.get("file")
        if uploaded_file is None:
            raise HTTPException(status_code=422, detail="A file attachment is required.")
        if not isinstance(uploaded_file, UploadFile):
            raise HTTPException(status_code=422, detail="A file attachment is required.")

        filename = _validated_upload_filename(
            getattr(uploaded_file, "filename", None), fallback="uploaded-file"
        )
        content = await _read_bounded_upload(uploaded_file)

        media_type = getattr(uploaded_file, "content_type", None)
        if (
            not isinstance(media_type, str)
            or not media_type
            or media_type == "application/octet-stream"
        ):
            media_type = res.identify_media_type(str(filename), content)

        require_pdf_models(media_type)

        record = res.ResourceRegistrationRequest(
            kind="upload",
            location=str(filename),
            media_type=media_type,
        )
        async with resource_registry_lock:
            _, _, state = await asyncio.to_thread(
                library.register_and_snapshot, data_dir, cache_dir, record, content
            )
        return state

    @app.get("/api/resources/{resource_id}/content")
    async def resource_content(resource_id: str) -> StarletteResponse:
        require_workspace()
        index = res.read_library_index(library.registry_path(data_dir))
        resource = next((r for r in index.resources if r.id == resource_id), None)
        if resource is None or resource.snapshot_hash is None:
            raise HTTPException(status_code=404, detail="Resource content is not available.")

        snapshot_path = library.snapshots_dir(data_dir) / resource.snapshot_hash
        if not snapshot_path.is_file():
            raise HTTPException(status_code=404, detail="Snapshot content is missing.")

        return StarletteResponse(
            content=snapshot_path.read_bytes(),
            media_type=resource.media_type,
        )

    @app.get("/api/resources/{resource_id}/preview")
    async def resource_preview(resource_id: str) -> StarletteResponse:
        require_workspace()
        index = res.read_library_index(library.registry_path(data_dir))
        resource = next((r for r in index.resources if r.id == resource_id), None)
        if resource is None or resource.snapshot_hash is None:
            raise HTTPException(status_code=404, detail="Resource preview is not available.")

        snapshot_path = library.snapshots_dir(data_dir) / resource.snapshot_hash
        if not snapshot_path.is_file():
            raise HTTPException(status_code=404, detail="Snapshot content is missing.")

        if resource.media_type == "application/pdf":
            return StarletteResponse(
                content=snapshot_path.read_bytes(), media_type="application/pdf"
            )
        if resource.media_type in res.IMAGE_MEDIA_TYPES:
            return StarletteResponse(
                content=snapshot_path.read_bytes(), media_type=resource.media_type
            )

        processor = res.resolve_processor(resource.media_type)
        if processor in {"text", "code"} or resource.media_type.startswith("text/"):
            content = snapshot_path.read_bytes()
        elif processor == "docling":
            extracted_path = (
                library.derived_dir(cache_dir) / resource.snapshot_hash / "extracted.md"
            )
            if not extracted_path.is_file():
                raise HTTPException(
                    status_code=404, detail="Process this resource to preview its text."
                )
            content = extracted_path.read_bytes()
        else:
            raise HTTPException(
                status_code=415, detail="A preview is not available for this file type."
            )

        return StarletteResponse(content=content, media_type="text/plain; charset=utf-8")

    @app.post("/api/discovery/search", response_model=list[res.DiscoveryResult])
    async def discover_remote(request: res.DiscoveryRequest) -> list[res.DiscoveryResult]:
        require_workspace()
        from course_harness import discovery  # noqa: PLC0415

        return await discovery.discover(request)

    @app.post("/api/discovery/inspect", response_model=res.Candidate)
    async def inspect_remote(request: res.RemoteFetchRequest) -> res.Candidate:
        require_workspace()
        from course_harness.discovery import inspect_web_url  # noqa: PLC0415

        try:
            return await inspect_web_url(request.url, host_resolver=remote_host_resolver)
        except ValueError as error:
            raise HTTPException(status_code=422, detail=str(error)) from error

    @app.post("/api/resources/remote", response_model=res.ResourceState, status_code=201)
    async def register_remote(request: res.RemoteFetchRequest) -> res.ResourceState:
        require_workspace()
        try:
            await res.validate_remote_url(request.url, host_resolver=remote_host_resolver)
            async with resource_registry_lock:
                state = library.register_remote_reference(data_dir, request.url, request.media_type)
            schedule_remote_capture(state.resource_id, request.url, request.media_type)
            return with_remote_job_state(state)
        except ValueError as error:
            raise HTTPException(status_code=422, detail=str(error)) from error

    @app.post("/api/resources/{resource_id}/refresh", response_model=res.ResourceState)
    async def refresh_resource(resource_id: str) -> res.ResourceState:
        require_workspace()
        state = library.get_resource_state(data_dir, cache_dir, resource_id)
        if state is None:
            raise HTTPException(status_code=422, detail="Resource was not found in the Library.")
        if state.kind != "remote":
            raise HTTPException(status_code=422, detail="Only remote resources can be refreshed.")
        if resource_id in remote_jobs:
            return with_remote_job_state(state)
        index = res.read_library_index(library.registry_path(data_dir))
        resource = next(
            (resource for resource in index.resources if resource.id == resource_id), None
        )
        if resource is None:
            raise HTTPException(status_code=422, detail="Resource was not found in the Library.")
        media_type = (
            resource.media_type if resource.media_type != "application/octet-stream" else None
        )
        schedule_remote_capture(resource_id, resource.location, media_type)
        return with_remote_job_state(state)

    @app.post("/api/sources/{source_id}/adopt-version", response_model=sources_module.Source)
    async def adopt_source_version(source_id: str) -> sources_module.Source:
        active = require_workspace()
        async with exclusive_mutation(active):
            try:
                expected_sources = capture_canonical_file(active, "sources.yaml")
                before_index = sources_module.read_sources_index(active)
                provenance_permitted = require_canonical_authoring(active)
                source = sources_module.adopt_source_version(
                    active, data_dir, cache_dir, source_id, expected=expected_sources
                )
                if before_index is None:
                    raise ValueError("No Sources exist in this Workspace.")
                expected_index = before_index.model_copy(
                    update={
                        "sources": [
                            source if item.id == source_id else item
                            for item in before_index.sources
                        ]
                    }
                )
                record_canonical_mutation(
                    active,
                    provenance_permitted,
                    {"sources.yaml": sources_module.serialize_sources_index(expected_index)},
                )
                return source
            except CanonicalMutationConflict as error:
                raise HTTPException(status_code=409, detail=str(error)) from error
            except ValueError as error:
                raise HTTPException(status_code=422, detail=str(error)) from error

    @app.get("/api/resources/{resource_id}/snapshots", response_model=list[res.Snapshot])
    async def resource_snapshots(resource_id: str) -> list[res.Snapshot]:
        require_workspace()
        index = res.read_library_index(library.registry_path(data_dir))
        resource = next((r for r in index.resources if r.id == resource_id), None)
        if resource is None:
            raise HTTPException(status_code=404, detail="Resource was not found.")
        history = list(resource.snapshot_history)
        if resource.snapshot_hash and resource.snapshot_hash not in history:
            history.append(resource.snapshot_hash)
        seen: set[str] = set()
        result: list[res.Snapshot] = []
        for h in reversed(history):
            if h in seen:
                continue
            seen.add(h)
            snapshot_path = library.snapshots_dir(data_dir) / h
            if snapshot_path.is_file():
                result.append(
                    res.Snapshot(
                        resource_id=resource_id,
                        content_hash=h,
                        byte_count=snapshot_path.stat().st_size,
                        captured_at=resource.registered_at,
                    )
                )
        return result

    # -------------------------------------------------------------------
    # Template Profiles
    # -------------------------------------------------------------------

    class TemplateUploadResponse(BaseModel):
        profile: tpl.TemplateProfile
        inspection: dict

    @app.get("/api/templates", response_model=list[tpl.TemplateProfileSummary])
    async def api_list_templates() -> list[tpl.TemplateProfileSummary]:
        builtin = tpl._builtin_default_profile()
        builtin_summary = tpl.TemplateProfileSummary(
            id=builtin.id,
            name=builtin.name,
            version=builtin.version,
            slide_count=builtin.slide_count,
            mapped_layouts=len(builtin.layouts),
        )
        registry = tpl.read_registry(templates_data)
        return [builtin_summary] + registry.profiles

    @app.post("/api/templates/upload", response_model=TemplateUploadResponse)
    async def api_upload_template(request: Request) -> TemplateUploadResponse:

        form = await _bounded_upload_form(request)
        uploaded_file = form.get("file")
        if uploaded_file is None:
            raise HTTPException(status_code=422, detail="A .pptx or .potx file is required.")
        if not isinstance(uploaded_file, UploadFile):
            raise HTTPException(status_code=422, detail="A file attachment is required.")

        filename = _validated_upload_filename(
            getattr(uploaded_file, "filename", None), fallback="uploaded.pptx"
        )
        content = await _read_bounded_upload(uploaded_file)

        media_type = getattr(uploaded_file, "content_type", None)
        if media_type and media_type not in {
            tpl.TEMPLATE_MEDIA_TYPE,
            tpl.TEMPLATE_POTX_MEDIA_TYPE,
        }:
            raise HTTPException(
                status_code=422,
                detail="Only .pptx and .potx files are supported",
            )

        try:
            tpl.validate_template_content(content)
        except Exception as error:
            raise HTTPException(
                status_code=422,
                detail=f"Could not read PowerPoint template: {error}",
            ) from error

        from uuid import uuid4

        profile_id = f"tpl-{uuid4().hex[:12]}"
        pd = tpl.profile_dir(templates_data, profile_id)
        pd.mkdir(parents=True, exist_ok=True)
        template_path = pd / "template.pptx"
        template_path.write_bytes(content)

        inspection = inspect_template(template_path)
        mappings = map_semantic_layouts(inspection)

        name = str(filename)
        if name.lower().endswith(".pptx") or name.lower().endswith(".potx"):
            name = name.rsplit(".", 1)[0]

        registry = tpl.read_registry(templates_data)
        name = tpl.unique_profile_name(name, registry)
        profile = tpl.TemplateProfile(
            id=profile_id,
            name=name[:200],
            version=1,
            template_filename=str(filename),
            slide_width=inspection["slide_width"],
            slide_height=inspection["slide_height"],
            slide_count=inspection["slide_count"],
            layouts=[
                tpl.TemplateLayoutMapping(
                    semantic_layout=m["semantic_layout"],
                    template_layout_index=m["template_layout_index"],
                    confidence=m["confidence"],
                    rationale=m["rationale"],
                    slot_mappings=m["slot_mappings"],
                )
                for m in mappings
            ],
        )
        tpl.write_profile_version(templates_data, profile)

        registry.profiles.append(
            tpl.TemplateProfileSummary(
                id=profile.id,
                name=profile.name,
                version=profile.version,
                slide_count=profile.slide_count,
                mapped_layouts=len(profile.layouts),
            )
        )
        tpl.write_registry(templates_data, registry)

        schedule_template_background_render(profile, template_path)

        return TemplateUploadResponse(profile=profile, inspection=inspection)

    @app.get("/api/templates/{profile_id}", response_model=tpl.TemplateProfile)
    async def api_get_template(profile_id: str) -> tpl.TemplateProfile:
        try:
            return tpl.resolve_profile(templates_data, profile_id)
        except ValueError as error:
            raise HTTPException(status_code=404, detail=str(error)) from error

    @app.get("/api/templates/{profile_id}/inspection")
    async def api_inspect_template(profile_id: str) -> dict:
        if profile_id == tpl.BUILTIN_DEFAULT_ID:
            raise HTTPException(
                status_code=400,
                detail="The built-in default does not have an imported template file",
            )
        profile = tpl.read_profile(templates_data, profile_id)
        if profile is None:
            raise HTTPException(status_code=404, detail="Template profile not found")
        template_file = tpl.profile_dir(templates_data, profile.id) / "template.pptx"
        return inspect_template(template_file)

    class MappingUpdate(BaseModel):
        model_config = ConfigDict(extra="forbid")
        semantic_layout: str
        template_layout_index: int
        confidence: float | None = Field(default=None, ge=0, le=1)
        rationale: str | None = Field(default=None, min_length=1)
        slot_mappings: dict[str, int] | None = None

    class MappingUpdateRequest(BaseModel):
        model_config = ConfigDict(extra="forbid")
        mappings: list[MappingUpdate]

    class TemplateRenameRequest(BaseModel):
        model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
        name: str = Field(min_length=1, max_length=200)

    @app.patch("/api/templates/{profile_id}", response_model=tpl.TemplateProfile)
    async def api_rename_template(
        profile_id: str, request: TemplateRenameRequest
    ) -> tpl.TemplateProfile:
        if profile_id == tpl.BUILTIN_DEFAULT_ID:
            raise HTTPException(
                status_code=400,
                detail="Cannot rename the built-in default profile",
            )
        profile = tpl.read_profile(templates_data, profile_id)
        if profile is None:
            raise HTTPException(status_code=404, detail="Template profile not found")

        registry = tpl.read_registry(templates_data)
        available_name = tpl.unique_profile_name(
            request.name,
            registry,
            exclude_profile_id=profile_id,
        )
        if available_name != request.name:
            raise HTTPException(
                status_code=409,
                detail=f'A Template Profile named "{request.name}" already exists.',
            )

        updated_profile = profile.model_copy(update={"name": request.name})
        tpl.write_profile(templates_data, updated_profile)
        for summary in registry.profiles:
            if summary.id == profile_id:
                summary.name = request.name
                break
        tpl.write_registry(templates_data, registry)
        return updated_profile

    @app.put("/api/templates/{profile_id}", response_model=tpl.TemplateProfile)
    async def api_update_template_mapping(
        profile_id: str, request: MappingUpdateRequest
    ) -> tpl.TemplateProfile:
        if profile_id == tpl.BUILTIN_DEFAULT_ID:
            raise HTTPException(
                status_code=400,
                detail="Cannot update the built-in default profile",
            )
        profile = tpl.read_profile(templates_data, profile_id)
        if profile is None:
            raise HTTPException(status_code=404, detail="Template profile not found")
        new_mappings = {m.semantic_layout: m for m in request.mappings}
        updated_layouts = []
        for m in profile.layouts:
            if m.semantic_layout in new_mappings:
                requested = new_mappings[m.semantic_layout]
                new_idx = requested.template_layout_index
                slot_mappings = (
                    requested.slot_mappings
                    if requested.slot_mappings is not None
                    else m.slot_mappings
                )
                if requested.confidence is not None and requested.rationale is not None:
                    updated_layouts.append(
                        tpl.TemplateLayoutMapping(
                            semantic_layout=m.semantic_layout,
                            template_layout_index=new_idx,
                            confidence=requested.confidence,
                            rationale=requested.rationale,
                            slot_mappings=slot_mappings,
                        )
                    )
                elif new_idx == m.template_layout_index and slot_mappings == m.slot_mappings:
                    updated_layouts.append(m)
                else:
                    updated_layouts.append(
                        tpl.TemplateLayoutMapping(
                            semantic_layout=m.semantic_layout,
                            template_layout_index=new_idx,
                            confidence=1.0,
                            rationale=f"Manually corrected to layout index {new_idx}",
                            slot_mappings=slot_mappings,
                        )
                    )
            else:
                updated_layouts.append(m)
        updated_profile = tpl.TemplateProfile(
            id=profile.id,
            name=profile.name,
            version=profile.version + 1,
            template_filename=profile.template_filename,
            slide_width=profile.slide_width,
            slide_height=profile.slide_height,
            slide_count=profile.slide_count,
            layouts=updated_layouts,
        )
        tpl.write_profile_version(templates_data, updated_profile)
        registry = tpl.read_registry(templates_data)
        for s in registry.profiles:
            if s.id == profile.id:
                s.version = updated_profile.version
                s.mapped_layouts = len(updated_profile.layouts)
                break
        tpl.write_registry(templates_data, registry)
        template_path = tpl.profile_dir(templates_data, updated_profile.id) / "template.pptx"
        schedule_template_background_render(updated_profile, template_path)
        return updated_profile

    @app.delete("/api/templates/{profile_id}", status_code=204)
    async def api_delete_template(profile_id: str) -> Response:
        if profile_id == tpl.BUILTIN_DEFAULT_ID:
            raise HTTPException(
                status_code=400,
                detail="Cannot delete the built-in default profile",
            )
        if tpl.read_profile(templates_data, profile_id) is None:
            raise HTTPException(status_code=404, detail="Template profile not found")
        tpl.delete_profile(templates_data, templates_cache, profile_id)
        return Response(status_code=204)

    @app.post("/api/templates/{profile_id}/calibrate", response_model=list[tpl.CalibrationSlide])
    async def api_calibrate(profile_id: str) -> list[tpl.CalibrationSlide]:
        profile = tpl.read_profile(templates_data, profile_id)
        if profile is None and profile_id != tpl.BUILTIN_DEFAULT_ID:
            raise HTTPException(status_code=404, detail="Template profile not found")
        try:
            resolved = tpl.resolve_profile(templates_data, profile_id)
        except ValueError as error:
            raise HTTPException(status_code=404, detail=str(error)) from error
        try:
            return tpl.render_calibration(templates_data, templates_cache, resolved)
        except RuntimeError as error:
            raise HTTPException(status_code=422, detail=str(error)) from error

    @app.get("/api/templates/{profile_id}/calibration/{filename}")
    async def api_calibration_image(profile_id: str, filename: str) -> StarletteResponse:
        if ".." in filename or "/" in filename:
            raise HTTPException(status_code=404, detail="Not found")
        cal_dir = tpl.calibration_dir(templates_cache, profile_id)
        image_path = cal_dir / filename
        if not image_path.is_file():
            raise HTTPException(status_code=404, detail="Not found")
        return StarletteResponse(
            content=image_path.read_bytes(),
            media_type="image/png",
        )

    @app.post("/api/templates/{profile_id}/validate")
    async def api_validate_template(profile_id: str) -> list[dict]:
        if profile_id == tpl.BUILTIN_DEFAULT_ID:
            return []
        profile = tpl.read_profile(templates_data, profile_id)
        if profile is None:
            raise HTTPException(status_code=404, detail="Template profile not found")
        template_file = tpl.profile_dir(templates_data, profile.id) / "template.pptx"
        return validate_export_mapping(profile, template_file)

    class SuggestMappingsResponse(BaseModel):
        model_config = ConfigDict(extra="forbid")
        mappings: list[dict]
        source: Literal["ai", "heuristic-fallback"]
        notice: str | None = None

    class SuggestMappingsRequest(BaseModel):
        model_config = ConfigDict(extra="forbid")
        consent: Literal[True]

    @app.post(
        "/api/templates/{profile_id}/suggest-mappings",
        response_model=SuggestMappingsResponse,
    )
    async def api_suggest_mappings(
        profile_id: str, request: SuggestMappingsRequest
    ) -> SuggestMappingsResponse:
        del request  # The Literal[True] field is the explicit transmission consent boundary.
        if profile_id == tpl.BUILTIN_DEFAULT_ID:
            raise HTTPException(
                status_code=400,
                detail="Cannot suggest mappings for the built-in default",
            )
        profile = tpl.read_profile(templates_data, profile_id)
        if profile is None:
            raise HTTPException(status_code=404, detail="Template profile not found")
        template_file = tpl.profile_dir(templates_data, profile.id) / "template.pptx"
        inspection = inspect_template(template_file)

        model: object
        if agent_model is not None:
            model = agent_model
        else:
            selected_model = resolve_selected_model(provider_path)
            if selected_model is None:
                raise HTTPException(
                    status_code=409,
                    detail="Configure a model provider before using AI suggestion.",
                )
            configuration, api_key = selected_model
            model = build_provider_model(configuration, api_key)
        try:
            suggestions = await suggest_mappings_with_llm(inspection, model)
        except Exception as error:
            return SuggestMappingsResponse(
                mappings=map_semantic_layouts(inspection),
                source="heuristic-fallback",
                notice=f"AI assistance was unavailable; kept local heuristic suggestions: {error}",
            )
        return SuggestMappingsResponse(mappings=suggestions, source="ai")

    static_directory = Path(__file__).with_name("static")
    if static_directory.is_dir():
        app.mount("/", StaticFiles(directory=static_directory, html=True), name="frontend")

    return app
