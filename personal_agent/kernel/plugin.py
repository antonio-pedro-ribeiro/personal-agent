"""Plugin protocols and the entry-point based registry."""

from __future__ import annotations

import logging
from collections.abc import Sequence
from dataclasses import dataclass, field
from importlib.metadata import entry_points
from typing import TYPE_CHECKING, Protocol, runtime_checkable

from personal_agent.kernel.models import (
    Delivery,
    Feedback,
    Interests,
    Item,
    OutputConfig,
    OwnedEntry,
    RankedItem,
    RankHints,
    Source,
)

if TYPE_CHECKING:
    from personal_agent.kernel.context import RunContext

CONTENT_GROUP = "personal_agent.content"
OUTPUT_GROUP = "personal_agent.outputs"

log = logging.getLogger(__name__)


@runtime_checkable
class ContentPlugin(Protocol):
    """Finds and judges items. ``name`` matches a ``## <Name>`` section in sources.md."""

    name: str
    title: str  # section heading in digests, e.g. "Plan"

    def collect(
        self, sources: Sequence[Source], interests: Interests, ctx: RunContext
    ) -> list[Item]: ...

    def filter(self, items: Sequence[Item], interests: Interests, ctx: RunContext) -> list[Item]:
        """Deterministic filtering. Must not call the LLM."""
        ...

    def rank_hints(self, interests: Interests, ctx: RunContext) -> RankHints: ...


@runtime_checkable
class OutputPlugin(Protocol):
    """Publishes items and reads back feedback. ``type`` matches the type in outputs.md."""

    type: str
    google_scopes: list[str]

    def accepts(self, item: RankedItem) -> bool: ...

    def publish(
        self, items: Sequence[RankedItem], cfg: OutputConfig, ctx: RunContext
    ) -> list[Delivery]:
        """Must be idempotent per (item, output) and write only inside its own namespace."""
        ...

    def sync_feedback(
        self, deliveries: Sequence[Delivery], cfg: OutputConfig, ctx: RunContext
    ) -> list[Feedback]: ...

    def preview(self, items: Sequence[RankedItem], cfg: OutputConfig, ctx: RunContext) -> str: ...


@runtime_checkable
class ClearableOutput(Protocol):
    """Optional output capability behind ``agent outputs clear``: list and delete what the
    output itself created. Never touches user-created content, even in the output's namespace.
    """

    def owned_entries(self, cfg: OutputConfig, ctx: RunContext) -> list[OwnedEntry]: ...

    def delete_entries(
        self, entries: Sequence[OwnedEntry], cfg: OutputConfig, ctx: RunContext
    ) -> list[OwnedEntry]:
        """Delete ``entries``; return those now gone (already-missing ones count as gone)."""
        ...


@dataclass
class Registry:
    content: dict[str, ContentPlugin] = field(default_factory=dict)
    outputs: dict[str, OutputPlugin] = field(default_factory=dict)
    warnings: list[str] = field(default_factory=list)

    def add_content(self, plugin: ContentPlugin) -> None:
        self.content[plugin.name.lower()] = plugin

    def add_output(self, plugin: OutputPlugin) -> None:
        self.outputs[plugin.type.lower()] = plugin


def discover() -> Registry:
    """Load plugins from installed entry points; a broken plugin is a warning, not a crash."""
    registry = Registry()
    for plugin in _load(CONTENT_GROUP, registry.warnings):
        if isinstance(plugin, ContentPlugin):
            registry.add_content(plugin)
        else:
            registry.warnings.append(f"{plugin!r} does not implement the content plugin API")
    for plugin in _load(OUTPUT_GROUP, registry.warnings):
        if isinstance(plugin, OutputPlugin):
            registry.add_output(plugin)
        else:
            registry.warnings.append(f"{plugin!r} does not implement the output plugin API")
    return registry


def _load(group: str, warnings: list[str]) -> list[object]:
    plugins: list[object] = []
    for ep in entry_points(group=group):
        try:
            plugins.append(ep.load()())
        except Exception as exc:
            warnings.append(f"plugin {ep.name!r} ({group}) failed to load: {exc}")
            log.exception("failed to load plugin %s", ep.name)
    return plugins
