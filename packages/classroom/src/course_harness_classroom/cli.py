"""The `course-classroom` command line."""

from typing import Annotated

import typer
import uvicorn

from course_harness_classroom.app import create_app

cli = typer.Typer(no_args_is_help=True, help="Course Harness Classroom.")


@cli.callback()
def _root() -> None:
    """Keep `serve` a named subcommand even while it is the only one."""


@cli.command()
def serve(
    host: Annotated[str, typer.Option(help="Interface to listen on.")] = "127.0.0.1",
    port: Annotated[int, typer.Option(help="Port to listen on.")] = 8000,
) -> None:
    """Serve the Classroom over HTTP."""
    uvicorn.run(create_app(), host=host, port=port)


def main() -> None:
    cli()
