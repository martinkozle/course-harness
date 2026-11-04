import hashlib
import json
import os
import secrets
import shutil
import subprocess
import sys
from collections.abc import Callable, Mapping
from pathlib import Path
from urllib.parse import unquote, urlparse

from course_harness.runtime_paths import RuntimePaths


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
    return RuntimePaths.platform().recent_store_path


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
        try:
            return portal_folder_picker()
        except PortalUnavailableError:
            pass
        picker = shutil.which("zenity") or shutil.which("kdialog")
        if picker is None:
            raise RuntimeError(
                "No folder picker is available. Install zenity or kdialog, or start Course Harness "
                "with the folder: course-harness /path/to/folder"
            )
        arguments = [picker, "--file-selection", "--directory", "--title=Open Course Workspace"]
        if Path(picker).name == "kdialog":
            arguments = [picker, "--getexistingdirectory", str(Path.home())]
        result = subprocess.run(arguments, check=False, capture_output=True, text=True)

    if result.returncode != 0:
        return None
    selection = result.stdout.strip()
    return Path(selection) if selection else None


class PortalUnavailableError(RuntimeError):
    """The desktop has no XDG portal that can choose a folder."""


PORTAL_PATH = "/org/freedesktop/portal/desktop"
# The FileChooser portal can choose folders from interface version 3 on.
PORTAL_DIRECTORY_VERSION = 3


def portal_folder_picker() -> Path | None:
    """Choose a folder with the XDG desktop portal, as browsers and other desktop apps do on Linux.

    The portal shows the desktop's own file chooser (GNOME, KDE, or the GTK one on Hyprland and
    Sway), so no dialog program has to be installed. Returns ``None`` when the dialog is cancelled
    and raises ``PortalUnavailableError`` when there is no session bus or folder-choosing portal.
    """
    try:
        from jeepney import DBusAddress, MatchRule, Properties, message_bus, new_method_call
        from jeepney.io.blocking import Proxy, open_dbus_connection
        from jeepney.wrappers import DBusErrorResponse, unwrap_msg
    except ImportError as error:
        raise PortalUnavailableError("jeepney is not installed") from error

    portal = DBusAddress(
        PORTAL_PATH,
        bus_name="org.freedesktop.portal.Desktop",
        interface="org.freedesktop.portal.FileChooser",
    )
    try:
        connection = open_dbus_connection(bus="SESSION")
    except (KeyError, OSError, ValueError) as error:
        raise PortalUnavailableError("No D-Bus session bus") from error
    with connection:
        try:
            (version_variant,) = unwrap_msg(
                connection.send_and_get_reply(Properties(portal).get("version"), timeout=5)
            )
        except (DBusErrorResponse, TimeoutError) as error:
            raise PortalUnavailableError("No FileChooser portal") from error
        if version_variant[1] < PORTAL_DIRECTORY_VERSION:
            raise PortalUnavailableError("The FileChooser portal cannot choose folders")

        # Subscribe to the Response signal on the request path before asking, so it is not missed.
        token = f"course_harness_{secrets.token_hex(8)}"
        sender = (connection.unique_name or "").removeprefix(":").replace(".", "_")
        rule = MatchRule(
            type="signal",
            interface="org.freedesktop.portal.Request",
            member="Response",
            path=f"{PORTAL_PATH}/request/{sender}/{token}",
        )
        unwrap_msg(connection.send_and_get_reply(Proxy(message_bus, connection).AddMatch(rule)))
        with connection.filter(rule) as responses:
            options = {
                "handle_token": ("s", token),
                "directory": ("b", True),
                "modal": ("b", True),
            }
            request = new_method_call(
                portal, "OpenFile", "ssa{sv}", ("", "Open Course Workspace", options)
            )
            try:
                unwrap_msg(connection.send_and_get_reply(request, timeout=10))
            except (DBusErrorResponse, TimeoutError) as error:
                raise PortalUnavailableError("The FileChooser portal did not open") from error
            response, results = connection.recv_until_filtered(responses).body
    return folder_from_portal_response(response, results)


def folder_from_portal_response(
    response: int, results: Mapping[str, tuple[str, object]]
) -> Path | None:
    """The chosen folder of a FileChooser Response, or ``None`` when nothing was chosen."""
    if response != 0:
        return None
    _signature, uris = results.get("uris", ("as", []))
    if not isinstance(uris, list) or not uris:
        return None
    location = urlparse(str(uris[0]))
    if location.scheme != "file":
        return None
    return Path(unquote(location.path))
