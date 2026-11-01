"""Check the Classroom image: only `course_harness.core` of Course Harness ships (ADR 0017).

Run by packages/classroom/Dockerfile during the build, so a dependency or copy that drags in
Course Harness, docling or the agent stack fails the build. ``--render`` also renders one slide
with LibreOffice, as a Classroom sync does.
"""

from __future__ import annotations

import importlib
import importlib.metadata
import importlib.util
import os
import pkgutil
import sys
import tempfile
from pathlib import Path

FORBIDDEN_MODULES = ("docling", "docling_core", "pydantic_ai", "fastmcp", "mcp", "keyring")
FORBIDDEN_DISTRIBUTIONS = ("course-harness", "docling-slim", "pydantic-ai-slim", "fastmcp-slim")


def check_import_boundary() -> list[str]:
    problems = [
        f"{name} is importable" for name in FORBIDDEN_MODULES if importlib.util.find_spec(name)
    ]
    for name in FORBIDDEN_DISTRIBUTIONS:
        try:
            importlib.metadata.distribution(name)
        except importlib.metadata.PackageNotFoundError:
            continue
        problems.append(f"the {name} distribution is installed")

    import course_harness

    package_dir = Path(next(iter(course_harness.__path__)))
    shipped = sorted(
        path.name for path in package_dir.iterdir() if path.name not in {"__pycache__"}
    )
    if shipped != ["__init__.py", "core"]:
        problems.append(f"course_harness holds more than __init__.py and core/: {shipped}")
    core = importlib.import_module("course_harness.core")
    for module in pkgutil.walk_packages(core.__path__, prefix="course_harness.core."):
        importlib.import_module(module.name)
    importlib.import_module("course_harness_classroom.app")
    importlib.import_module("course_harness_classroom.cli")
    outside_core = sorted(
        name
        for name in sys.modules
        if name.startswith("course_harness.") and not name.startswith("course_harness.core")
    )
    if outside_core:
        problems.append(f"modules outside course_harness.core were imported: {outside_core}")
    return problems


def check_render() -> list[str]:
    from pptx import Presentation

    from course_harness.core import slide_preview

    if not slide_preview.renderer_capability().available:
        return ["LibreOffice was not found"]
    presentation = Presentation()
    slide = presentation.slides.add_slide(presentation.slide_layouts[0])
    slide.shapes.title.text = "Classroom image check"
    with tempfile.TemporaryDirectory() as directory:
        source = Path(directory) / "check.pptx"
        presentation.save(str(source))
        target = Path(directory) / "check.png"
        try:
            slide_preview._render_pptx_png(source.read_bytes(), target)
        except RuntimeError as error:
            return [str(error)]
        if not target.is_file() or target.stat().st_size == 0:
            return ["LibreOffice rendered no image"]
    return []


def main() -> int:
    problems = check_import_boundary()
    if "--render" in sys.argv[1:]:
        from course_harness_classroom.render_sandbox import render_sandbox_report

        sandbox = render_sandbox_report(os.environ)
        if sandbox is not None:
            print(f"Render sandbox {'active' if sandbox[0] else 'not active'}: {sandbox[1]}")
        problems += check_render()
    for problem in problems:
        print(f"Classroom image check failed: {problem}", file=sys.stderr)
    if not problems:
        print("Classroom image check passed: only course_harness.core ships")
    return 1 if problems else 0


if __name__ == "__main__":
    raise SystemExit(main())
