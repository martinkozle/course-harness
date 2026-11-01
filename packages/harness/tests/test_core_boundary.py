import ast
import json
import subprocess
import sys
from pathlib import Path

# Course Harness Classroom installs only the core and its light dependencies.
FORBIDDEN_DEPENDENCIES = (
    "docling",
    "pydantic_ai",
    "fastmcp",
    "keyring",
    "fastapi",
    "httpx",
    "httpx2",
    "typer",
    "uvicorn",
)

PROBE = """
import importlib
import json
import pkgutil
import sys

import course_harness.core

modules = [
    module.name
    for module in pkgutil.walk_packages(course_harness.core.__path__, "course_harness.core.")
]
for name in modules:
    importlib.import_module(name)
print(json.dumps({"imported": modules, "loaded": sorted(sys.modules)}))
"""


def test_core_imports_only_core_modules_and_light_dependencies() -> None:
    result = subprocess.run(
        [sys.executable, "-c", PROBE], check=True, capture_output=True, text=True
    )
    report = json.loads(result.stdout)
    loaded = report["loaded"]

    assert "course_harness.core.releases" in report["imported"]
    assert "course_harness.core.slide_preview" in report["imported"]
    harness_modules = [
        name
        for name in loaded
        if name.startswith("course_harness.")
        and name != "course_harness.core"
        and not name.startswith("course_harness.core.")
    ]
    assert harness_modules == []
    dependencies = [
        name
        for name in loaded
        if any(
            name == forbidden or name.startswith(f"{forbidden}.")
            for forbidden in FORBIDDEN_DEPENDENCIES
        )
    ]
    assert dependencies == []


# Imports inside functions or under TYPE_CHECKING never run on import, so check the source too.
ALLOWED_THIRD_PARTY = {"pydantic", "yaml", "pptx", "PIL"}
CORE_DIRECTORY = Path(__file__).parents[1] / "src" / "course_harness" / "core"


def _disallowed_imports(path: Path) -> list[str]:
    disallowed = []
    for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"), filename=str(path))):
        if isinstance(node, ast.Import):
            modules = [alias.name for alias in node.names]
        elif isinstance(node, ast.ImportFrom):
            if node.level > 0:
                continue
            modules = [node.module or ""]
        else:
            continue
        for module in modules:
            root = module.split(".")[0]
            if (
                root in sys.stdlib_module_names
                or root in ALLOWED_THIRD_PARTY
                or module == "course_harness.core"
                or module.startswith("course_harness.core.")
            ):
                continue
            disallowed.append(f"{path.name}:{node.lineno} imports {module}")
    return disallowed


def test_core_source_imports_only_core_and_light_dependencies() -> None:
    paths = sorted(CORE_DIRECTORY.glob("*.py"))

    assert paths
    assert [problem for path in paths for problem in _disallowed_imports(path)] == []
