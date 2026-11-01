from pathlib import Path

from course_harness import library
from course_harness import runtime_paths as runtime_paths_module
from course_harness.providers import default_provider_store_path
from course_harness.runtime_paths import RuntimePaths
from course_harness.template_profiles import templates_cache_dir, templates_data_dir
from course_harness.workspaces import default_recent_store_path


def test_default_path_helpers_delegate_to_runtime_paths() -> None:
    paths = RuntimePaths.platform()

    assert default_recent_store_path() == paths.recent_store_path
    assert default_provider_store_path() == paths.provider_store_path
    assert library.library_data_dir() == paths.library_data_path
    assert library.library_cache_dir() == paths.library_cache_path
    assert templates_data_dir() == paths.templates_data_path
    assert templates_cache_dir() == paths.templates_cache_path


def test_runtime_paths_derive_semantic_locations_from_injected_roots(tmp_path: Path) -> None:
    paths = RuntimePaths(
        state=tmp_path / "state",
        data=tmp_path / "data",
        cache=tmp_path / "cache",
        config=tmp_path / "config",
    )

    assert paths.recent_store_path == tmp_path / "state" / "recent-workspaces.json"
    assert paths.chat_store_path == tmp_path / "state" / "chat"
    assert paths.library_data_path == tmp_path / "data" / "library"
    assert paths.library_cache_path == tmp_path / "cache"
    assert paths.templates_data_path == tmp_path / "data" / "templates"
    assert paths.templates_cache_path == tmp_path / "cache" / "templates"
    assert paths.release_data_path == tmp_path / "data" / "releases"
    assert paths.provider_credentials_path == tmp_path / "config" / "provider" / "credentials.json"


def test_runtime_paths_honor_xdg_locations(
    tmp_path: Path,
    monkeypatch,
) -> None:
    monkeypatch.setattr(runtime_paths_module.sys, "platform", "linux")
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "state-root"))
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "data-root"))
    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path / "cache-root"))
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "config-root"))

    paths = RuntimePaths.platform()

    assert paths.state == tmp_path / "state-root" / "course-harness"
    assert paths.data == tmp_path / "data-root" / "course-harness"
    assert paths.cache == tmp_path / "cache-root" / "course-harness"
    assert paths.config == tmp_path / "config-root" / "course-harness"


def test_runtime_paths_use_macos_application_locations(
    tmp_path: Path,
    monkeypatch,
) -> None:
    monkeypatch.setattr(runtime_paths_module.sys, "platform", "darwin")
    monkeypatch.setenv("HOME", str(tmp_path))

    paths = RuntimePaths.platform()

    support = tmp_path / "Library" / "Application Support" / "Course Harness"
    assert paths.state == support
    assert paths.data == support
    assert paths.config == support
    assert paths.cache == tmp_path / "Library" / "Caches" / "Course Harness"


def test_runtime_paths_use_windows_local_application_data(
    tmp_path: Path,
    monkeypatch,
) -> None:
    monkeypatch.setattr(runtime_paths_module.sys, "platform", "win32")
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path / "LocalAppData"))

    paths = RuntimePaths.platform()

    application = tmp_path / "LocalAppData" / "Course Harness"
    assert paths.state == application
    assert paths.data == application
    assert paths.config == application
    assert paths.cache == application / "cache"
