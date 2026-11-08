from collections.abc import Callable
from datetime import datetime, timedelta
from pathlib import Path

import pytest

from personal_agent.kernel.context import RunContext
from personal_agent.kernel.models import Source
from personal_agent.plugins.content.events.adapters.ical import fetch, parse_price
from tests.conftest import LISBON, NOW

VTIMEZONE = [
    "BEGIN:VTIMEZONE", "TZID:Europe/Lisbon",
    "BEGIN:STANDARD", "DTSTART:19701025T020000", "TZOFFSETFROM:+0100", "TZOFFSETTO:+0000",
    "RRULE:FREQ=YEARLY;BYMONTH=10;BYDAY=-1SU", "END:STANDARD",
    "BEGIN:DAYLIGHT", "DTSTART:19700329T010000", "TZOFFSETFROM:+0000", "TZOFFSETTO:+0100",
    "RRULE:FREQ=YEARLY;BYMONTH=3;BYDAY=-1SU", "END:DAYLIGHT",
    "END:VTIMEZONE",
]  # fmt: skip


def calendar(*events: list[str]) -> str:
    lines = ["BEGIN:VCALENDAR", "VERSION:2.0", "PRODID:-//test//EN", *VTIMEZONE]
    for ev in events:
        lines += ["BEGIN:VEVENT", "DTSTAMP:20260101T000000Z", *ev, "END:VEVENT"]
    lines.append("END:VCALENDAR")
    return "\r\n".join(lines) + "\r\n"


EVENTS = calendar(
    [
        "UID:tz@x", "DTSTART;TZID=Europe/Lisbon:20261112T213000",
        "DTEND;TZID=Europe/Lisbon:20261112T233000", "SUMMARY:Jazz quartet",
        "DESCRIPTION:Tickets €12\\, students €8", "LOCATION:Hot Clube", "GEO:38.7197;-9.1452",
        "URL:https://venue.test/jazz",
    ],
    ["UID:allday@x", "DTSTART;VALUE=DATE:20261114", "SUMMARY:Craft fair", "DESCRIPTION:Free entry"],
    ["UID:utc@x", "DTSTART:20261110T183000Z", "DURATION:PT2H", "SUMMARY:AI meetup"],
    ["UID:floating@x", "DTSTART:20261111T190000", "SUMMARY:Floating time"],
    ["UID:weekly@x", "DTSTART;TZID=Europe/Lisbon:20260105T190000",
     "DTEND;TZID=Europe/Lisbon:20260105T200000", "RRULE:FREQ=WEEKLY;BYDAY=MO", "SUMMARY:Weekly"],
    ["UID:cancel@x", "DTSTART:20261113T190000Z", "STATUS:CANCELLED", "SUMMARY:Cancelled"],
    ["UID:past@x", "DTSTART:20261001T190000Z", "SUMMARY:Past"],
    ["UID:far@x", "DTSTART:20270101T190000Z", "SUMMARY:Far future"],
)  # fmt: skip


@pytest.fixture
def items(tmp_path: Path, make_ctx: Callable[..., RunContext]):  # type: ignore[no-untyped-def]
    (tmp_path / "cal.ics").write_text(EVENTS, encoding="utf-8", newline="")
    source = Source(
        id="cal",
        plugin="events",
        type="ical",
        attrs={"url": "cal.ics", "page": "https://venue.test"},
    )
    result = fetch(source, NOW, NOW + timedelta(days=14), LISBON, make_ctx())
    return {i.title: i for i in result}, result


def test_window_cancelled_and_recurring(items) -> None:  # type: ignore[no-untyped-def]
    by_title, all_items = items
    assert "Cancelled" not in by_title and "Past" not in by_title and "Far future" not in by_title
    weekly = sorted(i.start for i in all_items if i.title == "Weekly")
    assert [d.astimezone(LISBON).date().isoformat() for d in weekly] == ["2026-11-09", "2026-11-16"]
    assert len({i.id for i in all_items}) == len(all_items)  # occurrences get distinct ids


def test_timezone_event_fields(items) -> None:  # type: ignore[no-untyped-def]
    jazz = items[0]["Jazz quartet"]
    assert jazz.start == datetime(2026, 11, 12, 21, 30, tzinfo=LISBON)
    assert jazz.end == datetime(2026, 11, 12, 23, 30, tzinfo=LISBON)
    assert jazz.location == "Hot Clube"
    assert jazz.url == "https://venue.test/jazz"
    assert jazz.extra["price"] == 8.0
    assert (jazz.extra["lat"], jazz.extra["lon"]) == (38.7197, -9.1452)
    assert jazz.extra["all_day"] is False


def test_all_day_floating_and_duration(items) -> None:  # type: ignore[no-untyped-def]
    by_title = items[0]
    fair = by_title["Craft fair"]
    assert fair.extra["all_day"] is True and fair.extra["price"] == 0.0
    assert fair.start == datetime(2026, 11, 14, tzinfo=LISBON)
    assert fair.end == datetime(2026, 11, 15, tzinfo=LISBON)
    assert fair.url == "https://venue.test"  # falls back to the source page
    floating = by_title["Floating time"]
    assert floating.start == datetime(2026, 11, 11, 19, tzinfo=LISBON)
    meetup = by_title["AI meetup"]
    assert meetup.end - meetup.start == timedelta(hours=2)


def test_missing_url_is_an_error(make_ctx: Callable[..., RunContext]) -> None:
    with pytest.raises(ValueError, match="needs a 'url'"):
        fetch(Source(id="x", plugin="events", type="ical"), NOW, NOW, LISBON, make_ctx())


@pytest.mark.parametrize(
    ("text", "price"),
    [("Bilhetes 15€", 15.0), ("€ 7,50 at the door", 7.5), ("10 EUR or 8 euros", 8.0),
     ("Entrada livre", 0.0), ("Free!", 0.0), ("Call for prices", None)],
)  # fmt: skip
def test_parse_price(text: str, price: float | None) -> None:
    assert parse_price(text) == price
