import subprocess
from pathlib import Path

import pytest


def run_cli(workspace: Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["course-harness", str(workspace), "--no-browser"],
        check=False,
        capture_output=True,
        text=True,
        timeout=2,
    )


def test_cli_rejects_a_missing_workspace(tmp_path: Path) -> None:
    missing_workspace = tmp_path / "not-created"

    result = run_cli(missing_workspace)

    assert result.returncode == 2
    assert result.stderr == f"course-harness: Workspace does not exist: {missing_workspace}\n"


def test_cli_rejects_a_file_as_a_workspace(tmp_path: Path) -> None:
    file_path = tmp_path / "course.yaml"
    file_path.touch()

    result = run_cli(file_path)

    assert result.returncode == 2
    assert result.stderr == f"course-harness: Workspace is not a directory: {file_path}\n"


def test_cli_rejects_a_symbolic_link_as_a_workspace(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    link = tmp_path / "linked-workspace"
    try:
        link.symlink_to(workspace, target_is_directory=True)
    except OSError as error:
        pytest.skip(f"Symbolic links are unavailable: {error}")

    result = run_cli(link)

    assert result.returncode == 2
    assert (
        result.stderr == f"course-harness: Workspace path cannot contain symbolic links: {link}\n"
    )


def test_cli_rejects_a_workspace_below_a_symbolic_link(tmp_path: Path) -> None:
    real_parent = tmp_path / "real-parent"
    workspace = real_parent / "workspace"
    workspace.mkdir(parents=True)
    linked_parent = tmp_path / "linked-parent"
    try:
        linked_parent.symlink_to(real_parent, target_is_directory=True)
    except OSError as error:
        pytest.skip(f"Symbolic links are unavailable: {error}")
    linked_workspace = linked_parent / "workspace"

    result = run_cli(linked_workspace)

    assert result.returncode == 2
    assert result.stderr == (
        f"course-harness: Workspace path cannot contain symbolic links: {linked_workspace}\n"
    )


def test_cli_rejects_the_filesystem_root() -> None:
    root = Path(Path.cwd().anchor)

    result = run_cli(root)

    assert result.returncode == 2
    assert result.stderr == "course-harness: The filesystem root cannot be used as a Workspace\n"


def test_cli_rejects_parent_traversal_in_a_workspace_path(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    intermediate = tmp_path / "intermediate"
    intermediate.mkdir()
    traversed_workspace = intermediate / ".." / "workspace"

    result = run_cli(traversed_workspace)

    assert result.returncode == 2
    assert result.stderr == (
        f"course-harness: Workspace path cannot contain parent traversal: {traversed_workspace}\n"
    )
