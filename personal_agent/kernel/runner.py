"""The run loop and router (spec §5).

sync feedback → collect → dedupe → filter → dislikes → rank → route → publish/preview.
Every plugin and output call is isolated: a failure is reported and the run continues.
"""

from __future__ import annotations

import re
from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import timedelta

from personal_agent.kernel.context import RunContext
from personal_agent.kernel.models import (
    Config,
    Delivery,
    Dislike,
    Item,
    OutputConfig,
    RankedItem,
    Source,
)
from personal_agent.kernel.plugin import ContentPlugin, OutputPlugin, Registry
from personal_agent.kernel.services.progress import items as n_items

FEEDBACK_WINDOW = timedelta(days=14)


@dataclass
class PluginReport:
    name: str
    collected: int = 0
    filtered: int = 0
    kept: int = 0
    threshold: float = 0.0
    ranked: list[RankedItem] = field(default_factory=list)  # full ranked list, incl. rejected
    kept_ids: set[str] = field(default_factory=set)  # subset of `ranked` actually routed
    error: str | None = None


@dataclass
class OutputReport:
    id: str
    type: str
    routed: int = 0
    published: int = 0
    preview: str | None = None
    error: str | None = None


@dataclass
class RunReport:
    plugins: list[PluginReport] = field(default_factory=list)
    outputs: list[OutputReport] = field(default_factory=list)
    feedback_recorded: int = 0
    feedback_errors: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)

    @property
    def errors(self) -> list[str]:
        return [
            *(f"plugin {p.name}: {p.error}" for p in self.plugins if p.error),
            *(f"output {o.id}: {o.error}" for o in self.outputs if o.error),
            *self.feedback_errors,
        ]


def _phrase(text: str) -> re.Pattern[str]:
    return re.compile(rf"(?<!\w){re.escape(text.lower())}(?!\w)")


def drop_disliked(items: Sequence[Item], dislikes: Sequence[Dislike]) -> list[Item]:
    patterns = [_phrase(d.text) for d in dislikes if d.enabled]
    return [i for i in items if not any(p.search(i.search_text()) for p in patterns)]


def _dedupe(items: Sequence[Item]) -> list[Item]:
    seen: dict[str, Item] = {}
    for item in items:
        seen.setdefault(item.id, item)
    return list(seen.values())


def _keep(
    ranked: Sequence[RankedItem],
    threshold: float,
    max_items: int,
    delivered: dict[str, set[str]],
) -> list[RankedItem]:
    """Items at or above ``threshold``, in rank order. ``max_items`` caps only items not yet
    delivered anywhere: one already delivered to another output was counted when it went out
    (e.g. against ``max_events_per_week``), so sending it to a new or cleared output is free."""
    kept: list[RankedItem] = []
    fresh = max_items
    for r in ranked:
        if r.score < threshold:
            continue
        if delivered.get(r.id):
            kept.append(r)
        elif fresh > 0:
            kept.append(r)
            fresh -= 1
    return kept


def _error(exc: BaseException) -> str:
    return f"{type(exc).__name__}: {exc}"


def run(
    config: Config,
    registry: Registry,
    ctx: RunContext,
    only_plugin: str | None = None,
    only_output: str | None = None,
) -> RunReport:
    report = RunReport()
    store, now = ctx.store, ctx.now
    ctx.plugin_titles = {name: p.title for name, p in registry.content.items()}

    enabled_sections = {s.plugin for s in config.sources if s.enabled}
    for missing in sorted(enabled_sections - registry.content.keys()):
        ctx.warn(f"sources.md: no installed content plugin for section '{missing}'")

    active: list[tuple[OutputConfig, OutputPlugin]] = []
    for cfg in config.outputs:
        if not cfg.enabled or (only_output and cfg.id != only_output):
            continue
        output = registry.outputs.get(cfg.type)
        if output is None:
            ctx.warn(f"{cfg.origin}: output type '{cfg.type}' is not installed (skipped)")
            continue
        active.append((cfg, output))

    # Which content plugins run: they have enabled sources and an output that routes them.
    runnable: list[tuple[str, ContentPlugin, list[Source], set[str]]] = []
    for name, plugin in registry.content.items():
        if only_plugin and name != only_plugin:
            continue
        sources = [s for s in config.sources if s.enabled and s.plugin == name]
        if not sources:
            continue
        targets = {cfg.id for cfg, _ in active if cfg.routes(name)}
        if not targets:
            ctx.warn(f"no enabled output routes plugin '{name}' (skipped)")
            continue
        runnable.append((name, plugin, sources, targets))

    progress = ctx.progress
    progress.total = 2 + 2 * len(runnable)

    # 1. Feedback from previous deliveries (never in dry-run).
    if ctx.dry_run:
        progress.step("Feedback", "skipped (dry run)")
    elif not active:
        progress.step("Feedback", "no outputs")
    else:
        progress.step("Feedback")
        for cfg, output in active:
            try:
                with progress.task(cfg.id, cfg.type) as task:
                    deliveries = store.deliveries_since(cfg.id, now - FEEDBACK_WINDOW)
                    feedback = output.sync_feedback(deliveries, cfg, ctx)
                    recorded = store.record_feedback(
                        f.model_copy(update={"output_id": cfg.id}) for f in feedback
                    )
                    task.result = f"{recorded} new"
                report.feedback_recorded += recorded
            except Exception as exc:
                ctx.log.exception("feedback sync failed for output %s", cfg.id)
                message = f"output {cfg.id}: feedback sync failed: {_error(exc)}"
                report.feedback_errors.append(message)
                ctx.warn(message)

    # 2. Content plugins.
    kept: dict[str, list[RankedItem]] = {}
    for name, plugin, sources, targets in runnable:
        pr = PluginReport(name=name)
        report.plugins.append(pr)
        interests = config.interests.for_plugin(name)
        noun = "source" if len(sources) == 1 else "sources"
        progress.step(f"Collecting {name} ({len(sources)} {noun})")
        ranking_started = False
        try:
            items = _dedupe(plugin.collect(sources, interests, ctx))
            pr.collected = len(items)
            if not ctx.dry_run:
                store.record_items(items, now)
            delivered = store.delivered_outputs([i.id for i in items])
            items = [i for i in items if not targets <= delivered[i.id]]
            progress.line(f"→ {pr.collected} found, {len(items)} not yet delivered")
            progress.step(f"Filtering & ranking {name}")
            ranking_started = True
            with progress.task("filters + dislikes") as task:
                items = drop_disliked(plugin.filter(items, interests, ctx), interests.dislikes)
                task.result = f"{len(items)} left"
            pr.filtered = len(items)
            hints = plugin.rank_hints(interests, ctx)
            with progress.task(f"ranking ({n_items(len(items))})") as task:
                ranked = ctx.ranker.rank(items, interests, hints, store.recent_feedback(name))
                task.result = "done"
            if not ctx.dry_run:
                store.record_rankings(ranked, now)
            pr.threshold = hints.threshold
            pr.ranked = ranked
            kept[name] = _keep(ranked, hints.threshold, hints.max_items, delivered)
            pr.kept = len(kept[name])
            pr.kept_ids = {r.id for r in kept[name]}
            progress.line(f"→ {pr.kept} kept (threshold {hints.threshold:.0f})")
        except Exception as exc:
            ctx.log.exception("content plugin %s failed", name)
            pr.error = _error(exc)
            progress.line(f"✗ {name} failed: {pr.error}")
            if not ranking_started:
                progress.step(f"Filtering & ranking {name}", "skipped")

    # 3. Route and publish.
    progress.step("Outputs", "" if active else "none enabled")
    for cfg, output in active:
        orep = OutputReport(id=cfg.id, type=cfg.type)
        report.outputs.append(orep)
        candidates = [
            r
            for name, ranked_items in kept.items()
            if cfg.routes(name)
            for r in ranked_items
            if r.score >= cfg.min_score
        ]
        try:
            with progress.task(cfg.id, cfg.type) as task:
                candidates = [r for r in candidates if output.accepts(r)]
                delivered = store.delivered_outputs([r.id for r in candidates])
                to_send = [r for r in candidates if cfg.id not in delivered[r.id]]
                orep.routed = len(to_send)
                task.result = "nothing new"
                if to_send and ctx.dry_run:
                    orep.preview = output.preview(to_send, cfg, ctx)
                    task.result = f"{len(to_send)} to publish"
                elif to_send:
                    deliveries = output.publish(to_send, cfg, ctx)
                    store.record_deliveries(
                        (
                            Delivery(
                                item_id=d.item_id, output_id=cfg.id, external_ref=d.external_ref
                            )
                            for d in deliveries
                        ),
                        now,
                    )
                    orep.published = len(deliveries)
                    task.result = f"{orep.published} published"
        except Exception as exc:
            ctx.log.exception("output %s failed", cfg.id)
            orep.error = _error(exc)

    report.warnings = list(ctx.warnings)
    return report
