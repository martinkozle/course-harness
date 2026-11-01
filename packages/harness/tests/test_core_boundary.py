import json
import subprocess
import sys

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
