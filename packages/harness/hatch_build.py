"""Hatch build hook that puts README.md, LICENSE and uv.lock into the sdist as real files.

In the repository they live at the workspace root, and this project links to them so that
hatchling finds the readme and license and hatch-pinned-extra finds the lock file. Hatchling would
archive the links themselves, which point outside the sdist. In an unpacked sdist they are plain
files, so resolving them is a no-op and an sdist rebuilt from it keeps uv.lock.
"""

import os
from typing import Any

from hatchling.builders.hooks.plugin.interface import BuildHookInterface

LINKED_FILES = ("README.md", "LICENSE", "uv.lock")


class DereferenceWorkspaceLinks(BuildHookInterface):
    def initialize(self, version: str, build_data: dict[str, Any]) -> None:
        force_include: dict[str, str] = build_data["force_include"]
        for name in LINKED_FILES:
            path = os.path.join(self.root, name)
            force_include.pop(path, None)
            force_include[os.path.realpath(path)] = name
