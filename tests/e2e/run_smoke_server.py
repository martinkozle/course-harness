from pathlib import Path
from tempfile import TemporaryDirectory

import uvicorn

from course_harness.app import create_app

temporary_root = TemporaryDirectory(prefix="course-harness-e2e-", dir=".cache")
root = Path(temporary_root.name)
workspace = root / "playwright-workspace"
workspace.mkdir()

uvicorn.run(
    create_app(
        folder_picker=lambda: workspace,
        recent_store_path=root / "user-data" / "recent-workspaces.json",
    ),
    host="127.0.0.1",
    port=8765,
)
