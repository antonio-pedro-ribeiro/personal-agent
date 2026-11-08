"""Command-line interface: ``agent run | plugins | sources test | outputs test | auth google``."""

from __future__ import annotations

import argparse
import logging
import os
import sys
from collections.abc import Sequence
from datetime import UTC, datetime, time, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

from personal_agent import __version__
from personal_agent.kernel import runner
from personal_agent.kernel.adapters.web import WebSources
from personal_agent.kernel.config import default_config_dir, load_config
from personal_agent.kernel.context import RunContext
from personal_agent.kernel.models import Config, RankedItem, make_item_id
from personal_agent.kernel.plugin import Registry, discover
from personal_agent.kernel.services import google, llm
from personal_agent.kernel.services.availability import (
    Availability,
    GoogleAvailability,
    NoAvailability,
)
from personal_agent.kernel.services.extractor import Extractor
from personal_agent.kernel.services.http import HttpService
from personal_agent.kernel.services.ranker import KeywordRanker, LLMRanker, Ranker
from personal_agent.kernel.services.store import Store, default_db_path


def _setup_stdout() -> None:
    # Windows consoles/pipes may default to a legacy code page; never crash on emoji.
    for stream in (sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if reconfigure:
            reconfigure(encoding="utf-8", errors="replace")


def google_output_scopes(config: Config, registry: Registry) -> list[str]:
    scopes: list[str] = []
    for cfg in config.outputs:
        output = registry.outputs.get(cfg.type)
        if cfg.enabled and output is not None:
            scopes.extend(output.google_scopes)
    return scopes


def build_context(
    config: Config,
    config_dir: Path,
    db: Path | str,
    dry_run: bool,
    registry: Registry | None = None,
    now: datetime | None = None,
    use_llm: bool = True,
    use_google: bool = True,
) -> RunContext:
    tz = ZoneInfo(config.interests.profile.timezone)
    store = Store(db)
    http = HttpService(base_dir=config_dir)
    warnings: list[str] = []

    model: llm.Guarded | None = None
    if use_llm:
        model, notes = llm.select(resolve=http.resolve)
        if model is None:
            reason = "; ".join(notes)
            warnings.append(f"No LLM ({reason}): keyword ranking only, web sources skipped")
        else:
            warnings.extend(notes)
    ranker: Ranker = LLMRanker(model) if model else KeywordRanker()

    google_service: google.GoogleService | None = None
    availability: Availability = NoAvailability()
    if use_google:
        needed = google_output_scopes(config, registry) if registry else []
        try:
            google_service = google.from_saved()
        except Exception as exc:
            warnings.append(f"Google connection failed ({type(exc).__name__}: {exc})")
        if google_service is None and needed:
            warnings.append("Google is not connected: run `agent auth google`")
        if google_service is not None:
            if missing := google_service.missing(google.required_scopes(needed)):
                warnings.append(
                    f"Google token lacks {', '.join(missing)}: run `agent auth google` again"
                )
            availability = GoogleAvailability(google_service)

    ctx = RunContext(
        store=store,
        ranker=ranker,
        availability=availability,
        http=http,
        now=now or datetime.now(UTC),
        tz=tz,
        config_dir=config_dir,
        dry_run=dry_run,
        web=WebSources(http, Extractor(model, store) if model else None),
        google=google_service,
        llm=model,
    )
    for w in warnings:
        ctx.warn(w)
    return ctx


def _cost_line(ctx: RunContext) -> str | None:
    model = ctx.llm
    if model is None or not model.usage.calls:
        return None
    u = model.usage
    if model.billing == "plan":
        money = "included in plan"
        if u.notional_usd:
            money += f" (~${u.notional_usd:.3f} at API prices)"
    else:
        cost = u.cost_usd(model.model)
        money = f"~${cost:.3f}" if cost is not None else "cost unknown for this model"
    return (
        f"LLM: {model.provider} {model.model}, {u.calls} call(s), {u.seconds:.0f}s, "
        f"{u.input_tokens:,} in / {u.output_tokens:,} out tokens, "
        f"{u.web_searches} web search(es), {money}"
    )


def _print_explain(p: runner.PluginReport) -> None:
    """Every ranked item with its score/reason, and why it was kept or dropped (``--explain``)."""
    if p.error or not p.ranked:
        return
    for r in sorted(p.ranked, key=lambda r: -r.score):
        if r.id in p.kept_ids:
            verdict = "KEPT"
        elif r.score >= p.threshold:
            verdict = f"capped (score >= {p.threshold:.0f} threshold, but over max_items)"
        else:
            verdict = f"dropped (score < {p.threshold:.0f} threshold)"
        print(f"    [{r.score:5.1f}] {verdict:<45} {r.title}")
        print(f"             reason: {r.reason}")


def _print_warnings(warnings: Sequence[str]) -> None:
    if warnings:
        print("\nWarnings:")
        for w in dict.fromkeys(warnings):  # de-duplicate, keep order
            print(f"  - {w}")


def cmd_run(args: argparse.Namespace, registry: Registry) -> int:
    config = load_config(args.config_dir)
    ctx = build_context(config, args.config_dir, args.db, args.dry_run, registry)
    try:
        report = runner.run(config, registry, ctx, args.plugin, args.output)
    finally:
        ctx.store.close()
    report.warnings += getattr(ctx.ranker, "errors", [])
    if reason := getattr(ctx.llm, "unavailable", None):
        report.warnings.append(
            f"LLM provider unavailable ({reason}): keyword ranking used, web sources skipped"
        )

    if args.dry_run:
        if ctx.llm is None:
            mode = "DRY RUN (nothing published)"
        elif ctx.llm.billing == "plan":
            mode = (
                "DRY RUN (nothing published; ranking/extraction below still call the LLM, "
                "using your plan's usage limits)"
            )
        else:
            mode = (
                "DRY RUN (nothing published; ranking/extraction below may still call the LLM "
                "and cost money)"
            )
    else:
        mode = "Run"
    print(f"{mode} — {ctx.now.astimezone(ctx.tz):%Y-%m-%d %H:%M %Z}")
    for p in report.plugins:
        status = (
            f"error: {p.error}"
            if p.error
            else f"{p.collected} found, {p.filtered} after filters, {p.kept} kept"
        )
        print(f"  [{p.name}] {status}")
        if args.explain:
            _print_explain(p)
    for o in report.outputs:
        if o.error:
            status = f"error: {o.error}"
        elif args.dry_run:
            status = f"{o.routed} item(s) would be published"
        else:
            status = f"{o.published} item(s) published"
        print(f"  ({o.id}) {status}")
    if cost := _cost_line(ctx):
        print(f"  {cost}")
    for o in report.outputs:
        if o.preview:
            print(f"\n--- preview: {o.id} ---\n{o.preview}")
    _print_warnings([*config.warnings, *registry.warnings, *report.warnings])
    if args.max_cost is not None and ctx.llm is not None and ctx.llm.usage.calls:
        actual = ctx.llm.usage.cost_usd(ctx.llm.model)
        if actual is not None and actual > args.max_cost:
            print(
                f"\nThis run cost ~${actual:.3f}, above --max-cost ${args.max_cost:.2f} "
                "(the LLM calls already happened; this only flags it after the fact).",
                file=sys.stderr,
            )
            return 3
    return 0


def cmd_plugins(args: argparse.Namespace, registry: Registry) -> int:
    config = load_config(args.config_dir)
    enabled_sources = {s.plugin for s in config.sources if s.enabled}
    enabled_outputs = {o.type for o in config.outputs if o.enabled}
    print("Content plugins:")
    for name in sorted(registry.content):
        state = "enabled" if name in enabled_sources else "no enabled sources"
        print(f"  {name:<20} {state}")
    print("Output plugins:")
    for name in sorted(registry.outputs):
        state = "enabled" if name in enabled_outputs else "not enabled in outputs.md"
        print(f"  {name:<20} {state}")
    _print_warnings([*config.warnings, *registry.warnings])
    return 0


def cmd_sources_test(args: argparse.Namespace, registry: Registry) -> int:
    config = load_config(args.config_dir)
    source = next((s for s in config.sources if s.id == args.source_id), None)
    if source is None:
        print(f"Unknown source '{args.source_id}'", file=sys.stderr)
        return 2
    plugin = registry.content.get(source.plugin)
    if plugin is None:
        print(f"No content plugin installed for section '{source.plugin}'", file=sys.stderr)
        return 2
    ctx = build_context(config, args.config_dir, args.db, dry_run=True, use_google=False)
    try:
        items = plugin.collect([source], config.interests.for_plugin(source.plugin), ctx)
    finally:
        ctx.store.close()
    print(f"{source.id} ({source.type}): {len(items)} item(s), before filters")
    for item in items:
        when = f"{item.start.astimezone(ctx.tz):%Y-%m-%d %H:%M}  " if item.start else ""
        where = f"  @ {item.location}" if item.location else ""
        print(f"  {when}{item.title}{where}\n      {item.url}")
    if cost := _cost_line(ctx):
        print(cost)
    _print_warnings(ctx.warnings)
    return 0


def cmd_outputs_test(args: argparse.Namespace, registry: Registry) -> int:
    """Publish one sample item (tomorrow 20:00) to an output, inside its own namespace."""
    config = load_config(args.config_dir)
    cfg = next((o for o in config.outputs if o.id == args.output_id), None)
    if cfg is None:
        print(f"Unknown output '{args.output_id}'", file=sys.stderr)
        return 2
    output = registry.outputs.get(cfg.type)
    if output is None:
        print(f"Output type '{cfg.type}' is not installed", file=sys.stderr)
        return 2
    ctx = build_context(
        config, args.config_dir, args.db, dry_run=False, registry=registry, use_llm=False
    )
    start = datetime.combine(ctx.today + timedelta(days=1), time(20), ctx.tz)
    item = RankedItem(
        id=make_item_id("outputs-test", cfg.id, start.isoformat()),
        plugin="test",
        title="personal-agent test event",
        url="https://github.com/antonio-pedro-ribeiro/personal-agent",
        source_id="outputs-test",
        summary="Sample item from `agent outputs test`. Safe to delete.",
        start=start,
        end=start + timedelta(hours=1),
        score=100,
        reason="Test of the output; delete it whenever you like.",
    )
    ctx.plugin_titles = {"test": "Test"}
    try:
        deliveries = output.publish([item], cfg, ctx)
    finally:
        ctx.store.close()
    for d in deliveries:
        print(f"Published to '{cfg.id}': {d.external_ref}")
    _print_warnings(ctx.warnings)
    return 0


def cmd_auth_google(args: argparse.Namespace, registry: Registry) -> int:
    config = load_config(args.config_dir)
    secrets = args.client_secrets or os.environ.get(google.CLIENT_SECRETS_ENV)
    if not secrets or not Path(secrets).is_file():
        print(
            "Need the OAuth client file (Desktop app) from Google Cloud Console: pass "
            f"--client-secrets PATH or set {google.CLIENT_SECRETS_ENV}.",
            file=sys.stderr,
        )
        return 2
    scopes = google.required_scopes(google_output_scopes(config, registry))
    print("Requesting access to:\n" + "\n".join(f"  - {s}" for s in scopes))
    token = google.authorize(Path(secrets), scopes)
    print(f"Saved to the OS keyring ({google.KEYRING_SERVICE} / {google.KEYRING_USER}).")
    if args.print_token:
        print(f"\nToken JSON for the {google.TOKEN_ENV} secret (keep it private):\n{token}")
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="agent", description=__doc__)
    parser.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    parser.add_argument(
        "--config-dir",
        type=Path,
        default=None,
        help="default: $PERSONAL_AGENT_CONFIG_DIR or ./config",
    )
    parser.add_argument("--db", default=None, help="SQLite file (default: platform data dir)")
    parser.add_argument("-v", "--verbose", action="store_true")
    sub = parser.add_subparsers(dest="command", required=True)

    run_p = sub.add_parser("run", help="collect, rank and publish")
    run_p.add_argument("--plugin", help="only this content plugin")
    run_p.add_argument("--output", help="only this output id")
    run_p.add_argument("--dry-run", action="store_true", help="preview; write nothing")
    run_p.add_argument(
        "--explain",
        action="store_true",
        help="print every ranked item with its score/reason and why it was kept or dropped",
    )
    run_p.add_argument(
        "--max-cost",
        type=float,
        default=None,
        metavar="USD",
        help=(
            "exit with code 3 and a warning if this run's estimated LLM cost exceeds USD "
            "(checked after the run completes; does not stop calls already made)"
        ),
    )
    run_p.set_defaults(func=cmd_run)

    plugins_p = sub.add_parser("plugins", help="list installed plugins")
    plugins_p.set_defaults(func=cmd_plugins)

    sources_p = sub.add_parser("sources", help="source tools")
    sources_sub = sources_p.add_subparsers(dest="sources_command", required=True)
    test_p = sources_sub.add_parser("test", help="fetch one source and list what it returns")
    test_p.add_argument("source_id")
    test_p.set_defaults(func=cmd_sources_test)

    outputs_p = sub.add_parser("outputs", help="output tools")
    outputs_sub = outputs_p.add_subparsers(dest="outputs_command", required=True)
    otest_p = outputs_sub.add_parser("test", help="publish one sample item to an output")
    otest_p.add_argument("output_id")
    otest_p.set_defaults(func=cmd_outputs_test)

    auth_p = sub.add_parser("auth", help="connect accounts")
    auth_sub = auth_p.add_subparsers(dest="auth_command", required=True)
    google_p = auth_sub.add_parser("google", help="one-time Google consent in the browser")
    google_p.add_argument("--client-secrets", help="OAuth client JSON (Desktop app)")
    google_p.add_argument(
        "--print-token", action="store_true", help="also print the token for cloud mode"
    )
    google_p.set_defaults(func=cmd_auth_google)
    return parser


def main(argv: Sequence[str] | None = None, registry: Registry | None = None) -> int:
    _setup_stdout()
    args = build_parser().parse_args(argv)
    logging.basicConfig(
        level=logging.INFO if args.verbose else logging.ERROR,
        format="%(levelname)s %(name)s: %(message)s",
    )
    args.config_dir = args.config_dir or default_config_dir()
    args.db = args.db or default_db_path()
    result: int = args.func(args, registry or discover())
    return result


if __name__ == "__main__":
    sys.exit(main())
