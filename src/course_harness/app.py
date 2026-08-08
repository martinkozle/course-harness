import asyncio
import json
import logging
import subprocess
from collections.abc import Callable
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Literal, cast

from fastapi import FastAPI, HTTPException, Response
from fastapi.exceptions import RequestValidationError
from pydantic import BaseModel, ConfigDict, Field
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
from course_harness import template_profiles as tpl
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
from course_harness.export import ExportError, export_presentation, validate_export_mapping
from course_harness.presentation import (
    Presentation,
    Slide,
    SlideCitation,
    SlideOrderRequest,
    SlidePatchRequest,
    delete_presentation_file,
    list_presentations,
    read_presentation_for_lecture,
    reorder_slides,
    slide_by_id,
    write_presentation,
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
from course_harness.template_inspect import (
    inspect_template,
    map_semantic_layouts,
    suggest_mappings_with_llm,
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


def create_app(
    workspace: Path | None = None,
    *,
    folder_picker: Callable[[], Path | None] = native_folder_picker,
    recent_store_path: Path | None = None,
    provider_store_path: Path | None = None,
    chat_store_path: Path | None = None,
    library_data_path: Path | None = None,
    library_cache_path: Path | None = None,
    templates_data_path: Path | None = None,
    templates_cache_path: Path | None = None,
    agent_model: Model | None = None,
    provider_validator: ProviderCapabilityValidator = validate_provider_capabilities,
    provider_account_validator: ProviderAccountValidator = validate_provider_account,
) -> FastAPI:
    """Create the HTTP application, optionally bound to one Course Workspace."""
    if not logging.getLogger("course-harness").handlers:
        logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    logger = logging.getLogger("course-harness")

    app = FastAPI(title="Course Harness")

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
        logger.warning(
            "Validation error on %s %s: %s",
            request.method,
            request.url.path,
            exc.errors(),
        )
        return StarletteResponse(
            content=json.dumps({"detail": exc.errors()}).encode("utf-8"),
            status_code=422,
            media_type="application/json",
        )

    recent_path = recent_store_path or default_recent_store_path()
    provider_path = provider_store_path or default_provider_store_path()
    data_dir = library_data_path or library.library_data_dir()
    cache_dir = library_cache_path or library.library_cache_dir()
    templates_data = templates_data_path or tpl.templates_data_dir()
    templates_cache = templates_cache_path or tpl.templates_cache_dir()
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
                presentations=list_presentations(active),
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

    @app.patch("/api/course/profile", response_model=CoursePlan)
    async def api_pin_template_profile(request: Request) -> CoursePlan:
        body = await request.json()
        template_profile_id = body.get("template_profile_id")
        template_profile_version = body.get("template_profile_version")
        active, plan = require_course_plan()
        async with exclusive_mutation(active):
            if template_profile_id is not None:
                try:
                    tpl.resolve_profile(templates_data, template_profile_id)
                except ValueError as error:
                    raise HTTPException(status_code=422, detail=str(error)) from error
            updated = plan.model_copy(
                update={
                    "template_profile_id": template_profile_id,
                    "template_profile_version": template_profile_version,
                }
            )
            write_course_plan(active, updated)
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

    @app.post("/api/presentations/{lecture_id}", response_model=Presentation, status_code=201)
    async def api_create_presentation(
        lecture_id: str, request: PresentationRequest
    ) -> Presentation:
        from uuid import uuid4

        from course_harness.presentation import (
            SLIDE_CLASSES_BY_LAYOUT,
            fill_slide_layout_fields,
            write_presentation,
        )

        active, plan = require_course_plan()
        async with exclusive_mutation(active):
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
            write_presentation(active, presentation)

            updated_lectures = [
                lec.model_copy(update={"presentation_id": presentation_id})
                if lec.id == lecture_id
                else lec
                for lec in plan.lectures
            ]
            updated_plan = plan.model_copy(update={"lectures": updated_lectures})
            write_course_plan(active, updated_plan)

            return presentation

    @app.delete("/api/presentations/{lecture_id}", status_code=204)
    async def api_delete_presentation(lecture_id: str) -> Response:
        active, plan = require_course_plan()
        async with exclusive_mutation(active):
            pres = read_presentation_for_lecture(active, lecture_id)
            if pres is None:
                raise HTTPException(
                    status_code=404,
                    detail="No Presentation exists for this lecture",
                )
            delete_presentation_file(active, pres.id)

            updated_lectures = [
                lec.model_copy(update={"presentation_id": None}) if lec.id == lecture_id else lec
                for lec in plan.lectures
            ]
            updated_plan = plan.model_copy(update={"lectures": updated_lectures})
            write_course_plan(active, updated_plan)
            return Response(status_code=204)

    @app.patch("/api/presentations/{lecture_id}/slides/{slide_id}", response_model=Presentation)
    async def api_patch_slide(
        lecture_id: str, slide_id: str, request: SlidePatchRequest
    ) -> Presentation:
        active, plan = require_course_plan()
        async with exclusive_mutation(active):
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
            write_presentation(active, updated)
            return updated

    @app.put("/api/presentations/{lecture_id}/slides/order", response_model=Presentation)
    async def api_reorder_slides(lecture_id: str, request: SlideOrderRequest) -> Presentation:
        active, plan = require_course_plan()
        async with exclusive_mutation(active):
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
            write_presentation(active, updated)
            return updated

    @app.get("/api/presentations/{lecture_id}/export")
    async def api_export_presentation(lecture_id: str, profile: str | None = None) -> Response:
        active = require_workspace()
        pres = read_presentation_for_lecture(active, lecture_id)
        if pres is None:
            raise HTTPException(
                status_code=404,
                detail="No Presentation exists for this lecture",
            )
        try:
            resolved_profile = tpl.resolve_profile(templates_data, profile)
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
                pres, profile=resolved_profile, template_path=template_file
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
            try:
                return sources_module.admit_source(
                    active,
                    data_dir,
                    request.resource_id,
                    label=request.label,
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

    @app.delete("/api/resources/cache", status_code=204)
    async def clear_resource_cache() -> Response:
        require_workspace()
        search_module.rebuild_index(cache_dir, data_dir)
        logger.info("Search index rebuilt from processed resources")
        return Response(status_code=204)

    @app.delete("/api/resources/{resource_id}", status_code=204)
    async def remove_resource(resource_id: str) -> Response:
        require_workspace()
        removed = library.remove_resource(data_dir, cache_dir, resource_id)
        if not removed:
            raise HTTPException(status_code=404, detail="Resource was not found")
        return Response(status_code=204)

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

    @app.post("/api/resources/{resource_id}/reprocess", response_model=res.ResourceState)
    async def reprocess_resource(resource_id: str) -> res.ResourceState:
        require_workspace()
        result = library.reprocess_resource(data_dir, cache_dir, resource_id)
        if result is None:
            raise HTTPException(
                status_code=404, detail="Resource was not found or has no Snapshot."
            )
        if result.status != "ready":
            raise HTTPException(status_code=422, detail=result.error or "Reprocessing failed.")
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
            return await inspect_web_url(request.url)
        except ValueError as error:
            raise HTTPException(status_code=422, detail=str(error)) from error

    @app.post("/api/resources/remote", response_model=res.ResourceState, status_code=201)
    async def register_remote(request: res.RemoteFetchRequest) -> res.ResourceState:
        require_workspace()
        try:
            return await library.register_remote_resource(
                data_dir, cache_dir, request.url, request.media_type
            )
        except ValueError as error:
            raise HTTPException(status_code=422, detail=str(error)) from error

    @app.post("/api/resources/{resource_id}/refresh", response_model=res.ResourceState)
    async def refresh_resource(resource_id: str) -> res.ResourceState:
        require_workspace()
        try:
            return await library.refresh_remote_resource(data_dir, cache_dir, resource_id)
        except ValueError as error:
            raise HTTPException(status_code=422, detail=str(error)) from error

    @app.post("/api/sources/{source_id}/adopt-version", response_model=sources_module.Source)
    async def adopt_source_version(source_id: str) -> sources_module.Source:
        active = require_workspace()
        async with exclusive_mutation(active):
            try:
                return sources_module.adopt_source_version(active, data_dir, source_id)
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

        form = await request.form()
        uploaded_file = form.get("file")
        if uploaded_file is None:
            raise HTTPException(status_code=422, detail="A .pptx or .potx file is required.")
        if isinstance(uploaded_file, str):
            raise HTTPException(status_code=422, detail="A file attachment is required.")

        filename = getattr(uploaded_file, "filename", "uploaded.pptx")
        content = await uploaded_file.read()
        if not isinstance(content, bytes):
            raise HTTPException(status_code=422, detail="File content must be binary.")

        media_type = getattr(uploaded_file, "content_type", None)
        if media_type and media_type != tpl.TEMPLATE_MEDIA_TYPE:
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
                )
                for m in mappings
            ],
        )
        tpl.write_profile_version(templates_data, profile)

        registry = tpl.read_registry(templates_data)
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

        return TemplateUploadResponse(profile=profile, inspection=inspection)

    @app.get("/api/templates/{profile_id}", response_model=tpl.TemplateProfile)
    async def api_get_template(profile_id: str) -> tpl.TemplateProfile:
        try:
            return tpl.resolve_profile(templates_data, profile_id)
        except ValueError as error:
            raise HTTPException(status_code=404, detail=str(error)) from error

    class MappingUpdate(BaseModel):
        model_config = ConfigDict(extra="forbid")
        semantic_layout: str
        template_layout_index: int

    class MappingUpdateRequest(BaseModel):
        model_config = ConfigDict(extra="forbid")
        mappings: list[MappingUpdate]

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
        new_mappings = {m.semantic_layout: m.template_layout_index for m in request.mappings}
        updated_layouts = []
        for m in profile.layouts:
            if m.semantic_layout in new_mappings:
                new_idx = new_mappings[m.semantic_layout]
                if new_idx == m.template_layout_index:
                    updated_layouts.append(m)
                else:
                    updated_layouts.append(
                        tpl.TemplateLayoutMapping(
                            semantic_layout=m.semantic_layout,
                            template_layout_index=new_idx,
                            confidence=1.0,
                            rationale=f"Manually corrected to layout index {new_idx}",
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

    @app.post(
        "/api/templates/{profile_id}/suggest-mappings",
        response_model=SuggestMappingsResponse,
    )
    async def api_suggest_mappings(profile_id: str) -> SuggestMappingsResponse:
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

        selected_model = resolve_selected_model(provider_path)
        if selected_model is None:
            raise HTTPException(
                status_code=409,
                detail="Configure a model provider before using AI suggestion.",
            )
        configuration, api_key = selected_model
        from course_harness.course_agent import build_provider_model

        model = build_provider_model(configuration, api_key)
        try:
            suggestions = await suggest_mappings_with_llm(inspection, model)
        except Exception as error:
            raise HTTPException(
                status_code=422,
                detail=f"AI suggestion failed: {error}",
            ) from error
        return SuggestMappingsResponse(mappings=suggestions)

    static_directory = Path(__file__).with_name("static")
    if static_directory.is_dir():
        app.mount("/", StaticFiles(directory=static_directory, html=True), name="frontend")

    return app
