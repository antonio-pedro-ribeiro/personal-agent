"""Events content plugin: collects events, filters them to what fits the profile."""

from __future__ import annotations

from collections.abc import Sequence
from datetime import timedelta
from zoneinfo import ZoneInfo

from personal_agent.kernel.context import RunContext
from personal_agent.kernel.models import Interests, Item, RankHints, Source
from personal_agent.plugins.content.events import filters
from personal_agent.plugins.content.events.adapters import ADAPTERS

RUBRIC = (
    "Score how well each event matches the user's topics. Prefer small, specific events "
    "over generic listings; penalise sales pitches and vague descriptions."
)


class EventsPlugin:
    name = "events"
    title = "Plan"

    def collect(
        self, sources: Sequence[Source], interests: Interests, ctx: RunContext
    ) -> list[Item]:
        tz = ZoneInfo(interests.profile.timezone)
        window_start = ctx.now
        window_end = ctx.now + timedelta(days=filters.WINDOW_DAYS)
        items: list[Item] = []
        for source in sources:
            adapter = ADAPTERS.get(source.type)
            if adapter is None:
                ctx.warn(
                    f"{source.origin}: source type '{source.type}' is not available yet "
                    f"(source '{source.id}' skipped)"
                )
                continue
            try:
                items.extend(adapter(source, window_start, window_end, tz, ctx))
            except Exception as exc:
                ctx.warn(f"source '{source.id}' failed: {type(exc).__name__}: {exc}")
        return items

    def filter(self, items: Sequence[Item], interests: Interests, ctx: RunContext) -> list[Item]:
        profile = interests.profile
        tz = ZoneInfo(profile.timezone)
        busy = ctx.availability.busy(ctx.now, ctx.now + timedelta(days=filters.WINDOW_DAYS))
        return [
            item
            for item in items
            if filters.in_window(item, ctx.now)
            and filters.within_distance(item, profile)
            and filters.within_price(item, profile)
            and filters.in_time_windows(item, profile.time_windows, tz)
            and filters.not_busy(item, busy)
        ]

    def rank_hints(self, interests: Interests, ctx: RunContext) -> RankHints:
        """Cap suggestions at ``max_events_per_week`` over a rolling 7 days."""
        already = ctx.store.count_delivered_since(self.name, ctx.now - timedelta(days=7))
        return RankHints(
            rubric=RUBRIC,
            threshold=50.0,
            max_items=max(0, interests.profile.max_events_per_week - already),
        )
