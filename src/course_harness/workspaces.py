import hashlib
import json
import os
import shutil
import subprocess
import sys
from collections.abc import Callable
from pathlib import Path


class WorkspaceSelectionError(ValueError):
    """A selected path cannot be used as a Course Workspace."""


FolderPicker = Callable[[], Path | None]


def workspace_identity(workspace: Path) -> str:
    return hashlib.sha256(os.fsencode(workspace)).hexdigest()[:16]


def validate_workspace_path(value: str | Path) -> Path:
    candidate = Path(value).expanduser()
    if ".." in candidate.parts:
        raise WorkspaceSelectionError(
            f"Workspace path cannot contain parent traversal: {candidate}"
        )
    if not candidate.exists():
        raise WorkspaceSelectionError(f"Workspace does not exist: {candidate}")

    absolute_candidate = candidate.absolute()
    filesystem_root = Path(absolute_candidate.anchor)
    path_components = (absolute_candidate, *absolute_candidate.parents)
    if any(path.is_symlink() and path.parent != filesystem_root for path in path_components):
        raise WorkspaceSelectionError(f"Workspace path cannot contain symbolic links: {candidate}")
    if not candidate.is_dir():
        raise WorkspaceSelectionError(f"Workspace is not a directory: {candidate}")

    workspace = candidate.resolve(strict=True)
    if workspace == filesystem_root:
        raise WorkspaceSelectionError("The filesystem root cannot be used as a Workspace")
    if not os.access(workspace, os.R_OK | os.W_OK | os.X_OK):
        raise WorkspaceSelectionError(f"Workspace is not readable and writable: {candidate}")
    return workspace


def default_recent_store_path() -> Path:
    if sys.platform == "win32":
        root = Path(os.environ.get("LOCALAPPDATA", Path.home() / "AppData" / "Local"))
        return root / "Course Harness" / "recent-workspaces.json"
    if sys.platform == "darwin":
        return (
            Path.home()
            / "Library"
            / "Application Support"
            / "Course Harness"
            / "recent-workspaces.json"
        )
    root = Path(os.environ.get("XDG_STATE_HOME", Path.home() / ".local" / "state"))
    return root / "course-harness" / "recent-workspaces.json"


def remember_workspace(store_path: Path, workspace: Path) -> None:
    store_path.parent.mkdir(parents=True, exist_ok=True)
    existing = read_recent_workspaces(store_path)
    paths = [workspace, *(path for path in existing.values() if path != workspace)]
    payload = {
        "version": 1,
        "workspaces": [{"id": workspace_identity(path), "path": str(path)} for path in paths[:8]],
    }
    temporary_path = store_path.with_suffix(f"{store_path.suffix}.tmp")
    try:
        temporary_path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
        temporary_path.replace(store_path)
    finally:
        temporary_path.unlink(missing_ok=True)


def read_recent_workspaces(store_path: Path) -> dict[str, Path]:
    if not store_path.is_file():
        return {}
    try:
        payload = json.loads(store_path.read_text(encoding="utf-8"))
        entries = payload.get("workspaces", [])
        return {
            str(entry["id"]): Path(entry["path"])
            for entry in entries
            if isinstance(entry, dict) and "id" in entry and "path" in entry
        }
    except OSError, json.JSONDecodeError, AttributeError, TypeError:
        return {}


def native_folder_picker() -> Path | None:
    """Open an operating-system folder chooser without exposing paths through HTTP."""
    if sys.platform == "darwin":
        result = subprocess.run(
            [
                "osascript",
                "-e",
                'POSIX path of (choose folder with prompt "Open Course Workspace")',
            ],
            check=False,
            capture_output=True,
            text=True,
        )
    elif sys.platform == "win32":
        script = (
            "Add-Type -AssemblyName System.Windows.Forms; "
            "$dialog = New-Object System.Windows.Forms.FolderBrowserDialog; "
            "$dialog.Description = 'Open Course Workspace'; "
            "if ($dialog.ShowDialog() -eq 'OK') { $dialog.SelectedPath }"
        )
        result = subprocess.run(
            ["powershell", "-NoProfile", "-Command", script],
            check=False,
            capture_output=True,
            text=True,
        )
    else:
        picker = shutil.which("zenity") or shutil.which("kdialog")
        if picker is None:
            raise RuntimeError("No native folder picker is available (install zenity or kdialog)")
        arguments = [picker, "--file-selection", "--directory", "--title=Open Course Workspace"]
        if Path(picker).name == "kdialog":
            arguments = [picker, "--getexistingdirectory", str(Path.home())]
        result = subprocess.run(arguments, check=False, capture_output=True, text=True)

    if result.returncode != 0:
        return None
    selection = result.stdout.strip()
    return Path(selection) if selection else None
