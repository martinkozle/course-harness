import sys
import threading
import webbrowser
from pathlib import Path
from typing import Annotated, NoReturn

import typer
import uvicorn

from course_harness.app import create_app
from course_harness.workspaces import (
    WorkspaceSelectionError,
    default_recent_store_path,
    remember_workspace,
    validate_workspace_path,
)

app = typer.Typer(
    add_completion=False,
    help="Open Course Harness on one local Course Workspace.",
    pretty_exceptions_enable=False,
)


def _fail(message: str) -> NoReturn:
    print(f"course-harness: {message}", file=sys.stderr)
    raise SystemExit(2)


def _workspace_from(value: str) -> Path:
    try:
        return validate_workspace_path(value)
    except WorkspaceSelectionError as error:
        _fail(str(error))


@app.command()
def launch(
    workspace_path: Annotated[
        str | None,
        typer.Argument(
            metavar="WORKSPACE",
            help="Existing Course Workspace; omit to open the Workspace Launcher",
        ),
    ] = None,
    port: Annotated[int, typer.Option(min=1, max=65535, help="Local port")] = 8765,
    no_browser: Annotated[
        bool,
        typer.Option("--no-browser", help="Start without opening the application in a browser"),
    ] = False,
) -> None:
    workspace = _workspace_from(workspace_path) if workspace_path is not None else None
    recent_store_path = default_recent_store_path()
    if workspace is not None:
        try:
            remember_workspace(recent_store_path, workspace)
        except OSError as error:
            _fail(f"Recent Workspace data could not be saved: {error}")
    host = "127.0.0.1"
    url = f"http://{host}:{port}"

    if not no_browser:
        threading.Timer(0.75, webbrowser.open, args=(url,)).start()

    uvicorn.run(create_app(workspace, recent_store_path=recent_store_path), host=host, port=port)


def main() -> None:
    app(prog_name="course-harness")
