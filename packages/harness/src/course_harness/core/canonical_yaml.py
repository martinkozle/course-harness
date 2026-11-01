"""Load canonical Course YAML without anchors or aliases.

Canonical Course files are written with plain ``yaml.safe_dump`` and never share nodes. Rejecting
anchors and aliases keeps a crafted file, such as one read from an untrusted mirror, from
expanding into an exponentially large document after loading.
"""

from __future__ import annotations

from typing import Any

import yaml
from yaml.composer import ComposerError
from yaml.nodes import Node


class CanonicalYamlLoader(yaml.SafeLoader):
    """A SafeLoader that rejects YAML anchors and aliases."""

    def compose_node(self, parent: Node | None, index: object) -> Node | None:
        event = self.peek_event()
        if isinstance(event, yaml.AliasEvent) or getattr(event, "anchor", None) is not None:
            raise ComposerError(
                None, None, "canonical YAML must not use anchors or aliases", event.start_mark
            )
        return super().compose_node(parent, index)


def load_canonical_yaml(text: str) -> Any:
    """Parse one canonical YAML document; raise ``yaml.YAMLError`` if it is unsafe."""
    return yaml.load(text, Loader=CanonicalYamlLoader)
