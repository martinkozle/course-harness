from collections.abc import Mapping
from pathlib import Path

import pytest

from course_harness import workspaces
from course_harness.workspaces import folder_from_portal_response, native_folder_picker


def test_portal_response_gives_the_chosen_folder() -> None:
    results = {"uris": ("as", ["file:///home/you/My%20Courses/System%201"])}

    assert folder_from_portal_response(0, results) == Path("/home/you/My Courses/System 1")


@pytest.mark.parametrize(
    ("response", "results"),
    [
        (1, {"uris": ("as", ["file:///home/you/course"])}),
        (2, {}),
        (0, {}),
        (0, {"uris": ("as", [])}),
        (0, {"uris": ("as", ["https://example.com/course"])}),
    ],
)
def test_portal_response_without_a_local_folder_chooses_nothing(
    response: int, results: Mapping[str, tuple[str, object]]
) -> None:
    assert folder_from_portal_response(response, results) is None


@pytest.mark.skipif(not workspaces.sys.platform.startswith("linux"), reason="Linux picker")
def test_linux_uses_the_portal_before_dialog_programs(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(workspaces, "portal_folder_picker", lambda: None)
    monkeypatch.setattr(workspaces.shutil, "which", lambda _name: pytest.fail("ran a program"))

    assert native_folder_picker() is None


@pytest.mark.skipif(not workspaces.sys.platform.startswith("linux"), reason="Linux picker")
def test_linux_without_a_portal_or_dialog_program_says_how_to_open_a_folder(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def unavailable() -> Path | None:
        raise workspaces.PortalUnavailableError("No D-Bus session bus")

    monkeypatch.setattr(workspaces, "portal_folder_picker", unavailable)
    monkeypatch.setattr(workspaces.shutil, "which", lambda _name: None)

    with pytest.raises(RuntimeError, match="course-harness /path/to/folder"):
        native_folder_picker()
