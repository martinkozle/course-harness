import subprocess
from collections.abc import Callable
from pathlib import Path
from typing import Literal, cast

from fastapi import FastAPI, HTTPException, Response
from pydantic import BaseModel, Field
from pydantic_ai import AgentRunResult
from pydantic_ai.models import Model
from pydantic_ai.ui.ag_ui import AGUIAdapter
from starlette.requests import Request
from starlette.responses import Response as StarletteResponse
from starlette.staticfiles import StaticFiles

from course_harness.chat_history import (
    ChatTranscript,
    read_chat_history,
    read_chat_transcript,
    save_chat_history,
)
from course_harness.course_agent import (
    CourseAgentDeps,
    CourseAgentState,
    build_provider_model,
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
    ProviderCapabilityError,
    ProviderConfigurationRequest,
    ProviderStatus,
    default_provider_store_path,
    provider_status,
    read_provider_api_key,
    read_provider_configuration,
    require_planning_capabilities,
    save_provider_configuration,
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


def create_app(
    workspace: Path | None = None,
    *,
    folder_picker: Callable[[], Path | None] = native_folder_picker,
    recent_store_path: Path | None = None,
    provider_store_path: Path | None = None,
    chat_store_path: Path | None = None,
    agent_model: Model | None = None,
) -> FastAPI:
    """Create the HTTP application, optionally bound to one Course Workspace."""
    app = FastAPI(title="Course Harness")
    recent_path = recent_store_path or default_recent_store_path()
    provider_path = provider_store_path or default_provider_store_path()
    chat_path = chat_store_path or recent_path.parent / "chat"
    course_agent = create_course_agent()

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
        require_workspace()
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
        return provider_status(provider_path)

    @app.put("/api/provider", response_model=ProviderStatus, response_model_exclude_none=True)
    async def configure_provider(request: ProviderConfigurationRequest) -> ProviderStatus:
        try:
            require_planning_capabilities(request.capabilities)
        except ProviderCapabilityError as error:
            raise HTTPException(status_code=422, detail=str(error)) from error
        save_provider_configuration(provider_path, request)
        return provider_status(provider_path)

    @app.get("/api/chat", response_model=ChatTranscript)
    async def chat_transcript() -> ChatTranscript:
        active = require_workspace()
        return read_chat_transcript(chat_path, active)

    @app.post("/api/agent")
    async def run_course_agent(request: Request) -> StarletteResponse:
        active = require_workspace()
        configuration = read_provider_configuration(provider_path)
        api_key = read_provider_api_key(provider_path)
        if configuration is None or api_key is None:
            raise HTTPException(
                status_code=409,
                detail="Configure a model provider before starting the Course Agent.",
            )
        try:
            require_planning_capabilities(configuration.capabilities)
        except ProviderCapabilityError as error:
            raise HTTPException(status_code=422, detail=str(error)) from error

        try:
            plan = read_course_plan(active)
        except InvalidCoursePlan as error:
            raise HTTPException(
                status_code=422, detail=f"course.yaml is invalid: {error}"
            ) from error
        deps = CourseAgentDeps(state=CourseAgentState(course=plan), workspace=active)
        history = read_chat_history(chat_path, active)
        model = agent_model or build_provider_model(configuration, api_key)

        async def persist(result: object) -> None:
            completed = cast(AgentRunResult[str], result)
            save_chat_history(chat_path, active, completed.all_messages())

        return await AGUIAdapter.dispatch_request(
            request,
            agent=course_agent,
            model=model,
            deps=deps,
            message_history=history,
            conversation_id="course-agent",
            on_complete=persist,
            allowed_file_url_schemes=frozenset(),
        )

    @app.post("/api/course", response_model=CoursePlan, status_code=201)
    async def create_course(course_input: CoursePlanInput) -> CoursePlan:
        active = require_workspace()
        if (active / "course.yaml").exists():
            raise HTTPException(status_code=409, detail="This Workspace already contains a Course")
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
        current_ids = [lecture.id for lecture in plan.lectures]
        if len(order.lecture_ids) != len(current_ids) or set(order.lecture_ids) != set(current_ids):
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

    static_directory = Path(__file__).with_name("static")
    if static_directory.is_dir():
        app.mount("/", StaticFiles(directory=static_directory, html=True), name="frontend")

    return app
