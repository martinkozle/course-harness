import asyncio
import subprocess
from collections.abc import Callable
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Literal, cast

from fastapi import FastAPI, HTTPException, Response
from pydantic import BaseModel, Field
from pydantic_ai import AgentRunResult, DeferredToolRequests
from pydantic_ai.models import Model
from pydantic_ai.ui.ag_ui import AGUIAdapter
from starlette.background import BackgroundTask
from starlette.requests import Request
from starlette.responses import Response as StarletteResponse
from starlette.staticfiles import StaticFiles

from course_harness import library
from course_harness import resources as res
from course_harness import search as search_module
from course_harness import sources as sources_module
from course_harness.chat_history import (
    ChatTranscript,
    read_chat_history,
    read_chat_transcript,
    save_chat_history,
)
from course_harness.course_agent import (
    AgentMode,
    CourseAgentDeps,
    CourseAgentState,
    build_provider_model,
    create_autonomous_course_agent,
    create_course_agent,
)
from course_harness.course_plan import (
    CoursePlan,
    CoursePlanInput,
    InvalidCoursePlan,
    create_course_plan,
    create_course_plan_file,
    initialize_workspace_history,
    read_course_plan,
    write_course_plan,
)
from course_harness.providers import (
    ModelCatalog,
    ModelPreset,
    ModelPresetRequest,
    ProviderAccount,
    ProviderAccountRequest,
    ProviderAccountValidator,
    ProviderCapabilityError,
    ProviderCapabilityValidator,
    ProviderConfigurationRequest,
    ProviderStatus,
    ProviderValidationError,
    default_provider_store_path,
    provider_request_for_model,
    provider_status,
    read_model_catalog,
    read_provider_api_key,
    read_provider_configuration,
    require_planning_capabilities,
    resolve_selected_model,
    save_model_preset,
    save_provider_account,
    save_provider_configuration,
    select_model_preset,
    validate_provider_account,
    validate_provider_capabilities,
)
from course_harness.workspaces import (
    WorkspaceSelectionError,
    default_recent_store_path,
    native_folder_picker,
    read_recent_workspaces,
    remember_workspace,
    validate_workspace_path,
)


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


class WorkspaceEntry(BaseModel):
    path: str
    kind: Literal["file", "directory"]


class ModelSelection(BaseModel):
    model_id: str = Field(min_length=1)


def create_app(
    workspace: Path | None = None,
    *,
    folder_picker: Callable[[], Path | None] = native_folder_picker,
    recent_store_path: Path | None = None,
    provider_store_path: Path | None = None,
    chat_store_path: Path | None = None,
    library_data_path: Path | None = None,
    library_cache_path: Path | None = None,
    agent_model: Model | None = None,
    provider_validator: ProviderCapabilityValidator = validate_provider_capabilities,
    provider_account_validator: ProviderAccountValidator = validate_provider_account,
) -> FastAPI:
    """Create the HTTP application, optionally bound to one Course Workspace."""
    app = FastAPI(title="Course Harness")
    recent_path = recent_store_path or default_recent_store_path()
    provider_path = provider_store_path or default_provider_store_path()
    data_dir = library_data_path or library.library_data_dir()
    cache_dir = library_cache_path or library.library_cache_dir()
    chat_path = chat_store_path or recent_path.parent / "chat"
    course_agent = create_course_agent()
    autonomous_agent = create_autonomous_course_agent()
    mutation_locks: dict[Path, asyncio.Lock] = {}
    cancel_events: dict[Path, asyncio.Event] = {}

    def require_workspace() -> Path:
        if workspace is None:
            raise HTTPException(status_code=409, detail="No Course Workspace is active")
        return workspace

    def require_course_plan() -> tuple[Path, CoursePlan]:
        active = require_workspace()
        try:
            plan = read_course_plan(active)
        except InvalidCoursePlan as error:
            raise HTTPException(
                status_code=422, detail=f"course.yaml is invalid: {error}"
            ) from error
        if plan is None:
            raise HTTPException(status_code=404, detail="This Workspace does not contain a Course")
        return active, plan

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

    @app.get("/api/chat", response_model=ChatTranscript)
    async def chat_transcript() -> ChatTranscript:
        active = require_workspace()
        return read_chat_transcript(chat_path, active)

    @app.post("/api/agent/cancel", status_code=204)
    async def cancel_agent_run() -> Response:
        active = require_workspace()
        cancel_event(active).set()
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

        selected_model = resolve_selected_model(provider_path)
        if selected_model is None:
            configuration = read_provider_configuration(provider_path)
            api_key = read_provider_api_key(provider_path)
        else:
            configuration, api_key = selected_model
        if configuration is None or api_key is None:
            lock.release()
            raise HTTPException(
                status_code=409,
                detail="Configure a model provider before starting the Course Agent.",
            )
        try:
            require_planning_capabilities(configuration.capabilities)
        except ProviderCapabilityError as error:
            lock.release()
            raise HTTPException(status_code=422, detail=str(error)) from error

        try:
            plan = read_course_plan(active)
        except InvalidCoursePlan as error:
            lock.release()
            raise HTTPException(
                status_code=422, detail=f"course.yaml is invalid: {error}"
            ) from error

        sources_index = sources_module.read_sources_index(active)

        import json as _json_mod

        body = await request.body()
        props: dict[str, object] = {}
        try:
            body_json = _json_mod.loads(body) if body else {}
            if isinstance(body_json, dict):
                props = cast(dict[str, object], body_json.get("forwardedProps", {}))
        except _json_mod.JSONDecodeError:
            pass
        mode = AgentMode(props.get("mode", AgentMode.GUIDED))

        deps = CourseAgentDeps(
            course_state=CourseAgentState(
                course=plan,
                sources=sources_index.sources if sources_index else [],
            ),
            workspace=active,
            data_dir=data_dir,
            cache_dir=cache_dir,
        )
        history = read_chat_history(chat_path, active)

        async def persist_if_not_cancelled(result: object) -> None:
            if not cancel.is_set():
                completed = cast(AgentRunResult[str], result)
                save_chat_history(chat_path, active, completed.all_messages())

        try:
            model = agent_model or build_provider_model(configuration, api_key)
            active_agent = autonomous_agent if mode == AgentMode.AUTONOMOUS else course_agent
            response = await AGUIAdapter.dispatch_request(
                request,
                agent=active_agent,
                model=model,
                deps=deps,
                output_type=[str, DeferredToolRequests],
                message_history=history,
                conversation_id="course-agent",
                on_complete=persist_if_not_cancelled,
                allowed_file_url_schemes=frozenset(),
            )
        except Exception:
            lock.release()
            raise

        async def release_lock() -> None:
            lock.release()

        response.background = BackgroundTask(release_lock)
        return response

    @app.post("/api/course", response_model=CoursePlan, status_code=201)
    async def create_course(course_input: CoursePlanInput) -> CoursePlan:
        active = require_workspace()
        async with exclusive_mutation(active):
            if (active / "course.yaml").exists():
                raise HTTPException(
                    status_code=409, detail="This Workspace already contains a Course"
                )
            plan = create_course_plan(course_input)
            try:
                initialize_workspace_history(active)
                create_course_plan_file(active, plan)
            except FileExistsError as error:
                raise HTTPException(
                    status_code=409, detail="This Workspace already contains a Course"
                ) from error
            except subprocess.CalledProcessError as error:
                raise HTTPException(
                    status_code=500, detail="Course history could not be initialized"
                ) from error
            return plan

    @app.patch("/api/course/lectures/{lecture_id}", response_model=CoursePlan)
    async def rename_lecture(lecture_id: str, rename: LectureRename) -> CoursePlan:
        active, plan = require_course_plan()
        async with exclusive_mutation(active):
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
            write_course_plan(active, updated)
            return updated

    @app.put("/api/course/lectures/order", response_model=CoursePlan)
    async def reorder_lectures(order: LectureOrder) -> CoursePlan:
        active, plan = require_course_plan()
        async with exclusive_mutation(active):
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
            write_course_plan(active, updated)
            return updated

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
            try:
                return sources_module.admit_source(
                    active,
                    data_dir,
                    request.resource_id,
                    label=request.label,
                    cache_dir=cache_dir,
                )
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
            index = sources_module.read_sources_index(active)
            if index is None:
                raise HTTPException(status_code=404, detail="Source was not found")
            remaining = [s for s in index.sources if s.id != source_id]
            if len(remaining) == len(index.sources):
                raise HTTPException(status_code=404, detail="Source was not found")
            updated = sources_module.SourcesIndex(version=index.version, sources=remaining)
            sources_module.write_sources_index(active, updated)
            return Response(status_code=204)

    @app.post(
        "/api/sources/search",
        response_model=list[search_module.SearchResult],
    )
    async def search_sources(
        request: search_module.SearchRequest,
    ) -> list[search_module.SearchResult]:
        active = require_workspace()
        index = sources_module.read_sources_index(active)
        if index is None or not index.sources:
            return []

        sources_by_version: dict[str, tuple[str, str, str]] = {}
        for s in index.sources:
            sources_by_version[s.source_version_id] = (s.id, s.resource_id, s.label)

        hits = search_module.search_raw(cache_dir, request.query, limit=request.limit or 10)
        return search_module.enrich_search_results(hits, sources_by_version)

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
            end = min(len(lines), line_end or len(lines))
            if start >= len(lines) or end <= start:
                raise HTTPException(status_code=422, detail="Invalid coordinate range")
            content = "\n".join(lines[start:end])

        if len(content) > max_chars:
            content = content[:max_chars]

        return StarletteResponse(content=content, media_type="text/plain")

    @app.get("/api/resources", response_model=list[res.ResourceState])
    async def list_resources() -> list[res.ResourceState]:
        require_workspace()
        return library.list_resources_with_state(data_dir, cache_dir)

    @app.get("/api/resources/{resource_id}", response_model=res.ResourceState)
    async def resource_detail(resource_id: str) -> res.ResourceState:
        require_workspace()
        state = library.get_resource_state(data_dir, cache_dir, resource_id)
        if state is None:
            raise HTTPException(status_code=404, detail="Resource was not found")
        return state

    @app.post("/api/resources", response_model=res.Resource, status_code=201)
    async def register_resource_route(request: res.ResourceRegistrationRequest) -> res.Resource:
        require_workspace()
        registry = library.registry_path(data_dir)
        return res.register_resource(registry, request)

    @app.post("/api/resources/{resource_id}/process", response_model=res.ResourceState)
    async def process_resource(resource_id: str) -> res.ResourceState:
        require_workspace()
        index = res.read_library_index(library.registry_path(data_dir))
        resource = next((r for r in index.resources if r.id == resource_id), None)
        if resource is None:
            raise HTTPException(status_code=404, detail="Resource was not found")

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

        result = library.process_existing_resource(data_dir, cache_dir, resource_id, content)
        if result is None:
            raise HTTPException(status_code=404, detail="Resource was not found")
        if result.status != "ready":
            raise HTTPException(status_code=422, detail=result.error or "Processing failed.")
        return result

    @app.post("/api/resources/upload", response_model=res.ResourceState, status_code=201)
    async def upload_resource(request: Request) -> res.ResourceState:
        require_workspace()
        form = await request.form()
        uploaded_file = form.get("file")
        if uploaded_file is None:
            raise HTTPException(status_code=422, detail="A file attachment is required.")
        if isinstance(uploaded_file, str):
            raise HTTPException(status_code=422, detail="A file attachment is required.")

        filename = getattr(uploaded_file, "filename", "upload")
        if not isinstance(filename, str) or not filename:
            filename = "uploaded-file"
        content = await uploaded_file.read()
        if not isinstance(content, bytes):
            raise HTTPException(status_code=422, detail="File content must be binary.")

        media_type = getattr(uploaded_file, "content_type", None)
        if not isinstance(media_type, str) or not media_type:
            media_type = res.identify_media_type(str(filename), content)

        record = res.ResourceRegistrationRequest(
            kind="upload",
            location=str(filename),
            media_type=media_type,
        )
        _, _, state = library.register_and_snapshot(data_dir, cache_dir, record, content)
        return state

    @app.delete("/api/resources/cache", status_code=204)
    async def clear_resource_cache() -> Response:
        require_workspace()
        library.clear_processing_cache(cache_dir)
        return Response(status_code=204)

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

    static_directory = Path(__file__).with_name("static")
    if static_directory.is_dir():
        app.mount("/", StaticFiles(directory=static_directory, html=True), name="frontend")

    return app
