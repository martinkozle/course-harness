"""The `course-classroom` command line. Settings come from the environment (see ``config``)."""

import logging
import os
from typing import Annotated

import typer
import uvicorn

from course_harness_classroom.app import create_app
from course_harness_classroom.config import ClassroomSettings, ConfigurationError
from course_harness_classroom.render_sandbox import render_sandbox_report
from course_harness_classroom.security import LOOPBACK_HOSTS

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
    if host not in LOOPBACK_HOSTS and settings.public_url is None:
        typer.echo(
            "Error: set CLASSROOM_PUBLIC_URL to the address people use to reach this Classroom"
            " when serving on a non-loopback host.",
            err=True,
        )
        raise typer.Exit(2)
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
    if settings.admin_token is None:
        logging.getLogger(__name__).warning(
            "CLASSROOM_ADMIN_TOKEN is not set, so /admin cannot be used"
        )
    sandbox = render_sandbox_report(os.environ)
    if sandbox is not None:
        active, message = sandbox
        logging.getLogger(__name__).log(logging.INFO if active else logging.WARNING, message)
    uvicorn.run(
        create_app(settings),
        host=host,
        port=port,
        # Trust forwarded client addresses and schemes only from configured proxies.
        proxy_headers=settings.trusted_proxies is not None,
        forwarded_allow_ips=settings.trusted_proxies,
    )


def main() -> None:
    cli()
