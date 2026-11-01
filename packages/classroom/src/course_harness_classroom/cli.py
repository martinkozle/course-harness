"""The `course-classroom` command line. Settings come from the environment (see ``config``)."""

import logging
from typing import Annotated

import typer
import uvicorn

from course_harness_classroom.app import create_app
from course_harness_classroom.config import ClassroomSettings, ConfigurationError

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
    try:
        settings = ClassroomSettings.from_environment()
    except ConfigurationError as error:
        typer.echo(f"Error: {error}", err=True)
        raise typer.Exit(2) from error
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
    if settings.admin_token is None:
        logging.getLogger(__name__).warning(
            "CLASSROOM_ADMIN_TOKEN is not set, so /admin cannot be used"
        )
    uvicorn.run(create_app(settings), host=host, port=port)


def main() -> None:
    cli()
