"""Whether the Docker image's LibreOffice wrapper can render templates without network access.

The image's ``libreoffice`` wrapper (``packages/classroom/docker/libreoffice``) runs soffice in a
new user and network namespace when the container's seccomp profile allows it, and reads
``CLASSROOM_RENDER_SANDBOX`` (``auto`` or ``required``) to decide what to do otherwise. This makes
the same check once, so the server log says which case applies. Outside the image the variable is
unset and LibreOffice runs as installed.
"""

from __future__ import annotations

import shutil
import subprocess
from collections.abc import Callable, Mapping

SANDBOX_VARIABLE = "CLASSROOM_RENDER_SANDBOX"
PROBE = ("unshare", "--user", "--map-root-user", "--net", "true")


def sandbox_available() -> bool:
    """Can this process create the wrapper's user and network namespace?"""
    if shutil.which(PROBE[0]) is None:
        return False
    try:
        result = subprocess.run(
            PROBE, stdin=subprocess.DEVNULL, capture_output=True, timeout=10, check=False
        )
    except OSError, subprocess.TimeoutExpired:
        return False
    return result.returncode == 0


def render_sandbox_report(
    environ: Mapping[str, str], available: Callable[[], bool] | None = None
) -> tuple[bool, str] | None:
    """``(active, message)`` for the server log, or ``None`` outside the Docker image."""
    mode = environ.get(SANDBOX_VARIABLE, "").strip().lower()
    if not mode:
        return None
    if (available or sandbox_available)():
        return True, "LibreOffice renders templates in a sandbox without network access"
    if mode == "required":
        return False, (
            "LibreOffice cannot run in a sandbox without network access here, and "
            f"{SANDBOX_VARIABLE}=required, so slides get plain backgrounds"
        )
    return False, (
        "LibreOffice cannot run in a sandbox without network access here, so it renders "
        f"templates with network access ({SANDBOX_VARIABLE}={mode})"
    )
