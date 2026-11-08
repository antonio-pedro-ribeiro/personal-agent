"""Google Calendar output and free/busy (ACs 5, 7, 12) against fake Google clients."""

from collections.abc import Callable
from datetime import datetime, timedelta
from typing import Any

import pytest

from personal_agent.kernel.context import RunContext
from personal_agent.kernel.models import OutputConfig, RankedItem
from personal_agent.kernel.services.availability import parse_freebusy
from personal_agent.kernel.services.google import full_scope, required_scopes
from personal_agent.plugins.outputs.google_calendar import GoogleCalendarOutput, event_body
from tests.conftest import LISBON, make_item
from tests.fakes import FakeCalendarService, FakeGoogle

CFG = OutputConfig(id="calendar", type="google_calendar", attrs={"calendar_name": "Suggestions"})
START = datetime(2026, 11, 12, 21, 30, tzinfo=LISBON)


def ranked(item_id: str, **kw: Any) -> RankedItem:
    kw.setdefault("start", START)
    base = make_item(item_id, f"Event {item_id}", plugin="events", **kw)
    return RankedItem(**base.model_dump(), score=90, reason="Matches jazz")


@pytest.fixture
def setup(make_ctx: Callable[..., RunContext]):  # type: ignore[no-untyped-def]
    service = FakeCalendarService()
    ctx = make_ctx()
    ctx.google = FakeGoogle(service, granted={"calendar.app.created"})  # type: ignore[assignment]
    return GoogleCalendarOutput(), service, ctx


def test_creates_own_calendar_once_and_inserts(setup) -> None:  # type: ignore[no-untyped-def]
    out, service, ctx = setup
    out.publish([ranked("a")], CFG, ctx)
    out.publish([ranked("b")], CFG, ctx)
    assert len(service.calendars_db) == 1
    (cal_id,) = service.calendars_db
    assert service.calendars_db[cal_id]["summary"] == "Suggestions"
    assert [e["summary"] for e in service.events_db[cal_id]] == ["Event a", "Event b"]


def test_idempotent_even_without_local_delivery_record(setup) -> None:  # type: ignore[no-untyped-def]
    """AC 7: the extended property prevents duplicates if a run crashed before recording."""
    out, service, ctx = setup
    first = out.publish([ranked("a")], CFG, ctx)
    second = out.publish([ranked("a")], CFG, ctx)
    (cal_id,) = service.calendars_db
    assert len(service.events_db[cal_id]) == 1
    assert first[0].external_ref == second[0].external_ref


def test_never_touches_other_calendars(setup) -> None:  # type: ignore[no-untyped-def]
    """AC 5: every call targets the agent's own calendar, never 'primary'."""
    out, service, ctx = setup
    out.publish([ranked("a"), ranked("b")], CFG, ctx)
    assert set(service.touched_calendars) == set(service.calendars_db)
    assert "primary" not in service.touched_calendars
    assert ctx.google.requested == [["calendar.app.created"]]


def test_recreates_calendar_if_user_deleted_it(setup) -> None:  # type: ignore[no-untyped-def]
    out, service, ctx = setup
    out.publish([ranked("a")], CFG, ctx)
    service.calendars_db.clear()
    out.publish([ranked("b")], CFG, ctx)
    assert len(service.calendars_db) == 1


def test_missing_scope_or_connection_fails_clearly(setup) -> None:  # type: ignore[no-untyped-def]
    out, _, ctx = setup
    ctx.google.granted = set()
    with pytest.raises(PermissionError):
        out.publish([ranked("a")], CFG, ctx)
    ctx.google = None
    with pytest.raises(RuntimeError, match="agent auth google"):
        out.publish([ranked("a")], CFG, ctx)


def test_event_body_timed_and_all_day() -> None:
    timed = event_body(ranked("a", location="Hot Clube"), LISBON)
    assert timed["status"] == "tentative" and timed["transparency"] == "transparent"
    assert timed["start"] == {"dateTime": START.isoformat(), "timeZone": "Europe/Lisbon"}
    assert timed["end"]["dateTime"] == (START + timedelta(hours=2)).isoformat()
    assert timed["extendedProperties"]["private"]["agentItemId"] == ranked("a").id
    assert "agent-id:" in timed["description"] and timed["location"] == "Hot Clube"
    day = datetime(2026, 11, 14, tzinfo=LISBON)
    all_day = event_body(ranked("b", start=day, extra={"all_day": True}), LISBON)
    assert all_day["start"] == {"date": "2026-11-14"} and all_day["end"] == {"date": "2026-11-15"}


def test_accepts_only_dated_items_and_preview() -> None:
    out = GoogleCalendarOutput()
    assert out.accepts(ranked("a")) and not out.accepts(ranked("n", start=None))


def test_scopes_are_least_privilege() -> None:
    """AC 12: enabled outputs' scopes plus free/busy, nothing else."""
    assert GoogleCalendarOutput.google_scopes == ["calendar.app.created"]
    assert required_scopes(["calendar.app.created"]) == [
        full_scope("calendar.app.created"),
        full_scope("calendar.freebusy"),
    ]


def test_parse_freebusy() -> None:
    resp = {"calendars": {"primary": {"busy": [
        {"start": "2026-11-12T20:00:00Z", "end": "2026-11-12T22:00:00Z"}]}}}  # fmt: skip
    ((s, e),) = parse_freebusy(resp)
    assert (e - s) == timedelta(hours=2) and s.tzinfo is not None
    with pytest.raises(RuntimeError):
        parse_freebusy({"calendars": {"primary": {"errors": [{"reason": "notFound"}]}}})
