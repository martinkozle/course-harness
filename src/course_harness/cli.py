import sys
import threading
import webbrowser
from pathlib import Path
from typing import Annotated, NoReturn

import typer
import uvicorn

from course_harness.app import create_app

app = typer.Typer(
    add_completion=False,
    help="Open Course Harness on one local Course Workspace.",
    pretty_exceptions_enable=False,
)


def _fail(message: str) -> NoReturn:
    print(f"course-harness: {message}", file=sys.stderr)
    raise SystemExit(2)


def _workspace_from(value: str) -> Path:
    candidate = Path(value).expanduser()
    if ".." in candidate.parts:
        _fail(f"Workspace path cannot contain parent traversal: {candidate}")
    if not candidate.exists():
        _fail(f"Workspace does not exist: {candidate}")
    absolute_candidate = candidate.absolute()
    filesystem_root = Path(absolute_candidate.anchor)
    path_components = (absolute_candidate, *absolute_candidate.parents)
    if any(path.is_symlink() and path.parent != filesystem_root for path in path_components):
        _fail(f"Workspace path cannot contain symbolic links: {candidate}")
    if not candidate.is_dir():
        _fail(f"Workspace is not a directory: {candidate}")

    workspace = candidate.resolve(strict=True)
    if workspace == filesystem_root:
        _fail("The filesystem root cannot be used as a Workspace")
    return workspace


@app.command()
def launch(
    workspace_path: Annotated[
        str,
        typer.Argument(
            metavar="WORKSPACE",
            help="Existing directory to use as the Course Workspace",
        ),
    ],
    port: Annotated[int, typer.Option(min=1, max=65535, help="Local port")] = 8765,
    no_browser: Annotated[
        bool,
        typer.Option("--no-browser", help="Start without opening the application in a browser"),
    ] = False,
) -> None:
    workspace = _workspace_from(workspace_path)
    host = "127.0.0.1"
    url = f"http://{host}:{port}"

    if not no_browser:
        threading.Timer(0.75, webbrowser.open, args=(url,)).start()

    uvicorn.run(create_app(workspace), host=host, port=port)


def main() -> None:
    app(prog_name="course-harness")
