"""Platform-specific runtime locations for Course Harness.

This module centralizes locations outside a Course Workspace.  A RuntimePaths
value can also be injected by embedding code and tests.
"""

from __future__ import annotations

import os
import sys
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class RuntimePaths:
    """Roots for mutable application state outside the selected Workspace."""

    state: Path
    data: Path
    cache: Path
    config: Path

    @classmethod
    def platform(cls) -> RuntimePaths:
        """Return the established platform locations without creating them."""
        if sys.platform == "win32":
            root = Path(os.environ.get("LOCALAPPDATA", Path.home() / "AppData" / "Local"))
            application = root / "Course Harness"
            return cls(
                state=application,
                data=application,
                cache=application / "cache",
                config=application,
            )
        if sys.platform == "darwin":
            support = Path.home() / "Library" / "Application Support" / "Course Harness"
            return cls(
                state=support,
                data=support,
                cache=Path.home() / "Library" / "Caches" / "Course Harness",
                config=support,
            )
        return cls(
            state=(
                Path(os.environ.get("XDG_STATE_HOME", Path.home() / ".local" / "state"))
                / "course-harness"
            ),
            data=(
                Path(os.environ.get("XDG_DATA_HOME", Path.home() / ".local" / "share"))
                / "course-harness"
            ),
            cache=Path(os.environ.get("XDG_CACHE_HOME", Path.home() / ".cache")) / "course-harness",
            config=(
                Path(os.environ.get("XDG_CONFIG_HOME", Path.home() / ".config")) / "course-harness"
            ),
        )

    @property
    def recent_store_path(self) -> Path:
        return self.state / "recent-workspaces.json"

    @property
    def chat_store_path(self) -> Path:
        return self.state / "chat"

    @property
    def provider_store_path(self) -> Path:
        return self.config / "provider"

    @property
    def provider_credentials_path(self) -> Path:
        return self.provider_store_path / "credentials.json"

    @property
    def library_data_path(self) -> Path:
        return self.data / "library"

    @property
    def library_cache_path(self) -> Path:
        return self.cache

    @property
    def templates_data_path(self) -> Path:
        return self.data / "templates"

    @property
    def templates_cache_path(self) -> Path:
        return self.cache / "templates"

    @property
    def release_data_path(self) -> Path:
        return self.data / "releases"


@dataclass(frozen=True)
class RuntimeLocations:
    """Effective application locations after optional host overrides are applied."""

    recent_store_path: Path
    chat_store_path: Path
    library_data_path: Path
    library_cache_path: Path
    templates_data_path: Path
    templates_cache_path: Path
    release_data_path: Path
    provider_store_path: Path

    @classmethod
    def from_runtime_paths(cls, paths: RuntimePaths) -> RuntimeLocations:
        return cls(
            recent_store_path=paths.recent_store_path,
            chat_store_path=paths.chat_store_path,
            library_data_path=paths.library_data_path,
            library_cache_path=paths.library_cache_path,
            templates_data_path=paths.templates_data_path,
            templates_cache_path=paths.templates_cache_path,
            release_data_path=paths.release_data_path,
            provider_store_path=paths.provider_store_path,
        )

    @property
    def provider_credentials_path(self) -> Path:
        return self.provider_store_path / "credentials.json"
