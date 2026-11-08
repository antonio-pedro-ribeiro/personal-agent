"""``agent outputs clear``: deletes only what the agent created, and resets those deliveries."""

from __future__ import annotations

import shutil
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest

from personal_agent.cli import main
from personal_agent.kernel.context import RunContext
from personal_agent.kernel.models import Delivery
from personal_agent.kernel.services import google
from personal_agent.kernel.services.store import Store
from personal_agent.plugins.outputs.google_calendar import GoogleCalendarOutput
from tests.fakes import FakeCalendarService, FakeGoogle
from tests.test_google_calendar import CFG, ranked

DEMO = Path(__file__).resolve().parent.parent / "examples" / "demo"
EPOCH = datetime(2000, 1, 1, tzinfo=UTC)


@pytest.fixture
def calendar(make_ctx: Callable[..., RunContext]):  # type: ignore[no-untyped-def]
    service = FakeCalendarService()
    ctx = make_ctx()
    ctx.google = FakeGoogle(service, granted={"calendar.app.created"})  # type: ignore[assignment]
    out = GoogleCalendarOutput()
    out.publish([ranked("a"), ranked("b"), ranked("c")], CFG, ctx)
    (cal_id,) = service.calendars_db
    # An event the user added to the Suggestions calendar by hand: never listed or deleted.
    service.events_db[cal_id].append({"id": "mine", "summary": "Dinner with Ana"})
    return out, service, ctx, cal_id


def test_lists_only_agent_events_across_pages(calendar) -> None:  # type: ignore[no-untyped-def]
    out, service, ctx, _ = calendar
    service.page_size = 2
    entries = out.owned_entries(CFG, ctx)
    assert sorted(e.title for e in entries) == ["Event a", "Event b", "Event c"]
    assert all(e.when for e in entries)


def test_deletes_agent_events_and_keeps_the_users(calendar) -> None:  # type: ignore[no-untyped-def]
    out, service, ctx, cal_id = calendar
    entries = out.owned_entries(CFG, ctx)
    service.events_db[cal_id] = [e for e in service.events_db[cal_id] if e["summary"] != "Event c"]
    service.fail_delete.add(entries[1].external_ref)  # "Event b": a transient API error
    gone = out.delete_entries(entries, CFG, ctx)
    # already-missing counts as gone; a failed delete is reported, not raised
    assert sorted(e.title for e in gone) == ["Event a", "Event c"]
    assert [e["summary"] for e in service.events_db[cal_id]] == ["Event b", "Dinner with Ana"]
    assert any("could not delete 'Event b'" in w for w in ctx.warnings)
    assert "primary" not in service.touched_calendars


def test_nothing_to_list_without_our_calendar(make_ctx: Callable[..., RunContext]) -> None:
    ctx = make_ctx()
    ctx.google = FakeGoogle(FakeCalendarService(), granted={"calendar.app.created"})  # type: ignore[assignment]
    assert GoogleCalendarOutput().owned_entries(CFG, ctx) == []


def test_store_forgets_deliveries(tmp_path: Path) -> None:
    store = Store(tmp_path / "a.db")
    now = datetime(2026, 11, 8, tzinfo=UTC)
    store.record_deliveries(
        [Delivery(item_id=i, output_id=o, external_ref="x") for i in "ab" for o in ("cal", "dig")],
        now,
    )
    assert store.forget_deliveries("cal", ["a", "zz"]) == 1
    assert store.delivered_outputs(["a", "b"]) == {"a": {"dig"}, "b": {"cal", "dig"}}


# --- CLI ---------------------------------------------------------------------------------


@pytest.fixture
def cli_setup(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> tuple[list[str], Any, Path]:
    config = tmp_path / "config"
    shutil.copytree(DEMO, config)
    (config / "outputs.md").write_text(
        "# Outputs\n\n- [x] **calendar** — google_calendar\n  - plugins: events\n"
        "- [x] **digest** — markdown_digest\n  - plugins: events\n  - path: out/digests\n",
        encoding="utf-8",
    )
    service = FakeCalendarService()
    fake = FakeGoogle(service, granted={"calendar.app.created", "calendar.freebusy"})
    monkeypatch.setattr(google, "from_saved", lambda: fake)
    monkeypatch.chdir(tmp_path)
    db = tmp_path / "a.db"
    base = ["--config-dir", str(config), "--db", str(db)]
    return base, service, db


def _agent_events(service: FakeCalendarService) -> list[dict[str, Any]]:
    return [e for evs in service.events_db.values() for e in evs if "extendedProperties" in e]


def test_cli_clear_dry_run_then_yes_then_rerun(
    cli_setup: tuple[list[str], Any, Path], capsys: pytest.CaptureFixture[str]
) -> None:
    base, service, db = cli_setup
    assert main([*base, "run", "--quiet"]) == 0
    published = len(_agent_events(service))
    assert published > 0
    capsys.readouterr()

    assert main([*base, "outputs", "clear", "calendar", "--dry-run"]) == 0
    assert "Dry run: nothing deleted." in capsys.readouterr().out
    assert len(_agent_events(service)) == published

    assert main([*base, "outputs", "clear", "calendar"]) == 2  # no terminal, no --yes
    assert len(_agent_events(service)) == published

    assert main([*base, "outputs", "clear", "calendar", "--yes"]) == 0
    assert f"Deleted {published} of {published}" in capsys.readouterr().out
    assert _agent_events(service) == []
    store = Store(db)
    assert store.deliveries_since("calendar", EPOCH) == []
    assert store.deliveries_since("digest", EPOCH)  # the digest's deliveries are untouched
    store.close()

    # Reset: the next run adds the still-relevant suggestions back, with no false "rejected".
    assert main([*base, "run", "--quiet"]) == 0
    assert len(_agent_events(service)) == published
    assert "feedback:" not in capsys.readouterr().out


def test_cli_clear_rejects_outputs_that_cannot_clear(
    cli_setup: tuple[list[str], Any, Path], capsys: pytest.CaptureFixture[str]
) -> None:
    base, _, _ = cli_setup
    assert main([*base, "outputs", "clear", "digest", "--yes"]) == 2
    assert "does not support clearing" in capsys.readouterr().err
    assert main([*base, "outputs", "clear", "nope"]) == 2
