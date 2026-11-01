"""Load canonical Course YAML without anchors, aliases or deep nesting.

Canonical Course files are written with plain ``yaml.safe_dump``: they never share nodes and nest
only a few levels deep. Rejecting anchors, aliases and deep nesting keeps a crafted file, such as
one read from an untrusted mirror, from expanding into an exponentially large document or
exhausting the parser's recursion.
"""

from __future__ import annotations

from typing import Any

import yaml
from yaml.composer import ComposerError
from yaml.nodes import Node

MAX_YAML_DEPTH = 64


class CanonicalYamlLoader(yaml.SafeLoader):
    """A SafeLoader that rejects YAML anchors, aliases and deeply nested collections."""

    def __init__(self, stream: str) -> None:
        super().__init__(stream)
        self.depth = 0

    def compose_node(self, parent: Node | None, index: object) -> Node | None:
        event = self.peek_event()
        if isinstance(event, yaml.AliasEvent) or getattr(event, "anchor", None) is not None:
            raise ComposerError(
                None, None, "canonical YAML must not use anchors or aliases", event.start_mark
            )
        if self.depth >= MAX_YAML_DEPTH:
            raise ComposerError(None, None, "canonical YAML is nested too deeply", event.start_mark)
        self.depth += 1
        try:
            return super().compose_node(parent, index)
        finally:
            self.depth -= 1


def load_canonical_yaml(text: str) -> Any:
    """Parse one canonical YAML document; raise ``yaml.YAMLError`` if it is unsafe."""
    try:
        return yaml.load(text, Loader=CanonicalYamlLoader)
    except RecursionError as error:
        raise yaml.YAMLError("canonical YAML is nested too deeply") from error
