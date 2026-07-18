from pathlib import Path

from fastapi import FastAPI
from pydantic import BaseModel
from starlette.staticfiles import StaticFiles


class HealthResponse(BaseModel):
    status: str


class WorkspaceResponse(BaseModel):
    name: str
    path: str


def create_app(workspace: Path) -> FastAPI:
    """Create the HTTP application bound to one explicit Course Workspace."""
    app = FastAPI(title="Course Harness")

    @app.get("/api/health", response_model=HealthResponse)
    async def health() -> HealthResponse:
        return HealthResponse(status="ok")

    @app.get("/api/workspace", response_model=WorkspaceResponse)
    async def active_workspace() -> WorkspaceResponse:
        return WorkspaceResponse(name=workspace.name, path=str(workspace))

    static_directory = Path(__file__).with_name("static")
    if static_directory.is_dir():
        app.mount("/", StaticFiles(directory=static_directory, html=True), name="frontend")

    return app
