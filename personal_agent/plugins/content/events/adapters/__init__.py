"""Source adapters owned by the events plugin, keyed by ``type`` in sources.md."""

from __future__ import annotations

from collections.abc import Callable
from datetime import datetime
from zoneinfo import ZoneInfo

from personal_agent.kernel.context import RunContext
from personal_agent.kernel.models import Item, Source
from personal_agent.plugins.content.events.adapters import ical

Adapter = Callable[[Source, datetime, datetime, ZoneInfo, RunContext], list[Item]]

ADAPTERS: dict[str, Adapter] = {
    "ical": ical.fetch,
}
