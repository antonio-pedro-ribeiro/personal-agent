"""Command-line interface: ``agent run | plugins | sources test``."""

from __future__ import annotations

import argparse
import logging
import sys
from collections.abc import Sequence
from datetime import UTC, datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from personal_agent import __version__
from personal_agent.kernel import runner
from personal_agent.kernel.config import default_config_dir, load_config
from personal_agent.kernel.context import RunContext
from personal_agent.kernel.models import Config
from personal_agent.kernel.plugin import Registry, discover
from personal_agent.kernel.services.availability import NoAvailability
from personal_agent.kernel.services.http import HttpService
from personal_agent.kernel.services.ranker import KeywordRanker
from personal_agent.kernel.services.store import Store, default_db_path


def _setup_stdout() -> None:
    # Windows consoles/pipes may default to a legacy code page; never crash on emoji.
    for stream in (sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if reconfigure:
            reconfigure(encoding="utf-8", errors="replace")


def build_context(
    config: Config, config_dir: Path, db: Path | str, dry_run: bool, now: datetime | None = None
) -> RunContext:
    tz = ZoneInfo(config.interests.profile.timezone)
    return RunContext(
        store=Store(db),
        ranker=KeywordRanker(),
        availability=NoAvailability(),
        http=HttpService(base_dir=config_dir),
        now=now or datetime.now(UTC),
        tz=tz,
        config_dir=config_dir,
        dry_run=dry_run,
    )


def _print_warnings(warnings: Sequence[str]) -> None:
    if warnings:
        print("\nWarnings:")
        for w in dict.fromkeys(warnings):  # de-duplicate, keep order
            print(f"  - {w}")


def cmd_run(args: argparse.Namespace, registry: Registry) -> int:
    config = load_config(args.config_dir)
    ctx = build_context(config, args.config_dir, args.db, args.dry_run)
    try:
        report = runner.run(config, registry, ctx, args.plugin, args.output)
    finally:
        ctx.store.close()

    mode = "DRY RUN (nothing written)" if args.dry_run else "Run"
    print(f"{mode} — {ctx.now.astimezone(ctx.tz):%Y-%m-%d %H:%M %Z}")
    for p in report.plugins:
        status = (
            f"error: {p.error}"
            if p.error
            else f"{p.collected} found, {p.filtered} after filters, {p.kept} kept"
        )
        print(f"  [{p.name}] {status}")
    for o in report.outputs:
        if o.error:
            status = f"error: {o.error}"
        elif args.dry_run:
            status = f"{o.routed} item(s) would be published"
        else:
            status = f"{o.published} item(s) published"
        print(f"  ({o.id}) {status}")
    for o in report.outputs:
        if o.preview:
            print(f"\n--- preview: {o.id} ---\n{o.preview}")
    _print_warnings([*config.warnings, *registry.warnings, *report.warnings])
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
    ctx = build_context(config, args.config_dir, ":memory:", dry_run=True)
    items = plugin.collect([source], config.interests.for_plugin(source.plugin), ctx)
    print(f"{source.id} ({source.type}): {len(items)} item(s), before filters")
    for item in items:
        when = f"{item.start.astimezone(ctx.tz):%Y-%m-%d %H:%M}  " if item.start else ""
        print(f"  {when}{item.title}  {item.url}")
    _print_warnings(ctx.warnings)
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="agent", description=__doc__)
    parser.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    parser.add_argument(
        "--config-dir", type=Path, default=None, help="default: $AGENT_CONFIG_DIR or ./config"
    )
    parser.add_argument("--db", default=None, help="SQLite file (default: platform data dir)")
    parser.add_argument("-v", "--verbose", action="store_true")
    sub = parser.add_subparsers(dest="command", required=True)

    run_p = sub.add_parser("run", help="collect, rank and publish")
    run_p.add_argument("--plugin", help="only this content plugin")
    run_p.add_argument("--output", help="only this output id")
    run_p.add_argument("--dry-run", action="store_true", help="preview; write nothing")
    run_p.set_defaults(func=cmd_run)

    plugins_p = sub.add_parser("plugins", help="list installed plugins")
    plugins_p.set_defaults(func=cmd_plugins)

    sources_p = sub.add_parser("sources", help="source tools")
    sources_sub = sources_p.add_subparsers(dest="sources_command", required=True)
    test_p = sources_sub.add_parser("test", help="fetch one source and list what it returns")
    test_p.add_argument("source_id")
    test_p.set_defaults(func=cmd_sources_test)
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
