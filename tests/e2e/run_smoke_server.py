import sys
from pathlib import Path

from course_harness.cli import main

workspace = Path(".cache/playwright-workspace")
workspace.mkdir(parents=True, exist_ok=True)
if any(workspace.iterdir()):
    raise RuntimeError(f"Playwright smoke Workspace is not empty: {workspace}")

sys.argv = ["course-harness", str(workspace), "--no-browser", "--port", "8765"]
main()
