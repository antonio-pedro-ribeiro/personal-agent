"""Events content plugin: collects events, filters them to what fits the profile."""

from __future__ import annotations

from collections.abc import Sequence
from datetime import timedelta
from zoneinfo import ZoneInfo

from personal_agent.kernel.context import RunContext
from personal_agent.kernel.models import Interests, Item, RankHints, Source
from personal_agent.kernel.services.extractor import ExtractionSpec
from personal_agent.plugins.content.events import filters
from personal_agent.plugins.content.events.adapters import ADAPTERS, ical

RUBRIC = (
    "Score how well each event matches the user's topics. Prefer small, specific events "
    "over generic listings; penalise sales pitches and vague descriptions."
)

EXTRACTION = """Upcoming events a person could attend: talks, meetups, workshops, courses,
retreats, ceremonies, concerts, festivals, screenings, exhibitions, gatherings.
- Only events that take place today or later and whose date is stated or can be resolved.
- Skip articles that only report on past events, and generic news without an event.
- title: the event's own name. summary: one or two sentences on what it is.
- location: venue and town; online: true for online-only events.
- price_eur: lowest ticket price in euros if stated, 0 if free, otherwise null."""


class EventsPlugin:
    name = "events"
    title = "Plan"

    def extraction_spec(self) -> ExtractionSpec:
        return ExtractionSpec(plugin=self.name, instructions=EXTRACTION, require_start=True)

    def collect(
        self, sources: Sequence[Source], interests: Interests, ctx: RunContext
    ) -> list[Item]:
        tz = ZoneInfo(interests.profile.timezone)
        window = timedelta(days=filters.WINDOW_DAYS)
        items: list[Item] = []
        for source in sources:
            try:
                items.extend(self._collect_one(source, interests, ctx, tz, window))
            except Exception as exc:
                ctx.warn(f"source '{source.id}' failed: {type(exc).__name__}: {exc}")
        return items

    def _collect_one(
        self,
        source: Source,
        interests: Interests,
        ctx: RunContext,
        tz: ZoneInfo,
        window: timedelta,
    ) -> list[Item]:
        if adapter := ADAPTERS.get(source.type):
            return adapter(source, ctx.now, ctx.now + window, tz, ctx)
        if source.type in ("web_page", "web_search"):
            if ctx.web is None:
                raise RuntimeError("generic web sources are not available in this run")
            if source.type == "web_search":
                return ctx.web.search(
                    source, self.extraction_spec(), interests, tz, ctx.now, window
                )
            result = ctx.web.page(source, self.extraction_spec(), tz, ctx.now)
            items = list(result.items)
            if result.ical_url:  # a calendar feed: parse it directly, no LLM
                feed = source.model_copy(update={"attrs": {**source.attrs, "url": result.ical_url}})
                items.extend(ical.fetch(feed, ctx.now, ctx.now + window, tz, ctx))
            return items
        ctx.warn(f"{source.origin}: unknown source type '{source.type}' (source '{source.id}')")
        return []

    def filter(self, items: Sequence[Item], interests: Interests, ctx: RunContext) -> list[Item]:
        profile = interests.profile
        tz = ZoneInfo(profile.timezone)
        try:
            busy = ctx.availability.busy(ctx.now, ctx.now + timedelta(days=filters.WINDOW_DAYS))
        except Exception as exc:
            ctx.warn(
                f"free/busy unavailable ({type(exc).__name__}: {exc}); not checking busy slots"
            )
            busy = []
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
