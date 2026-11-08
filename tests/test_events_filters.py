from datetime import datetime, timedelta

import pytest

from personal_agent.kernel.models import Profile
from personal_agent.plugins.content.events import filters
from tests.conftest import LISBON, NOW, make_item


def event(start: datetime, hours: float = 2, **extra: object):  # type: ignore[no-untyped-def]
    return make_item(
        "e", "event", plugin="events", start=start, end=start + timedelta(hours=hours), extra=extra
    )


def lisbon(y: int, mo: int, d: int, h: int = 0, mi: int = 0) -> datetime:
    return datetime(y, mo, d, h, mi, tzinfo=LISBON)


@pytest.mark.parametrize(
    ("start", "hours", "keep"),
    [
        (NOW + timedelta(days=1), 2, True),
        (NOW + timedelta(days=13, hours=23), 2, True),
        (NOW + timedelta(days=14, hours=1), 2, False),  # too far ahead
        (NOW - timedelta(hours=1), 2, True),  # ongoing
        (NOW - timedelta(hours=3), 2, False),  # over
    ],
)
def test_in_window(start: datetime, hours: float, keep: bool) -> None:
    assert filters.in_window(event(start, hours), NOW) is keep


def test_in_window_requires_start() -> None:
    assert filters.in_window(make_item("x", "no date"), NOW) is False


@pytest.mark.parametrize(
    ("lat", "lon", "keep"),
    [
        (38.7197, -9.1452, True),  # Lisbon centre
        (38.6979, -9.4215, False),  # Cascais, ~24 km
        (41.1588, -8.6308, False),  # Porto
        (None, None, True),  # unknown location kept
    ],
)
def test_within_distance(lat: float | None, lon: float | None, keep: bool) -> None:
    profile = Profile(coordinates=(38.72, -9.14), max_km=15)
    extra = {} if lat is None else {"lat": lat, "lon": lon}
    assert filters.within_distance(event(NOW, **extra), profile) is keep


def test_haversine_lisbon_porto() -> None:
    assert filters.haversine_km((38.72, -9.14), (41.1588, -8.6308)) == pytest.approx(274, abs=3)


@pytest.mark.parametrize(("price", "keep"), [(0, True), (60, True), (60.5, False), (None, True)])
def test_within_price(price: float | None, keep: bool) -> None:
    extra = {} if price is None else {"price": price}
    assert filters.within_price(event(NOW, **extra), Profile(max_price=60)) is keep


WINDOWS = ["weekday_evenings", "weekends"]


@pytest.mark.parametrize(
    ("start", "hours", "keep"),
    [
        (lisbon(2026, 11, 10, 19), 2, True),  # Tue evening
        (lisbon(2026, 11, 10, 10), 2, False),  # Tue morning
        (lisbon(2026, 11, 10, 16), 3, True),  # Tue 16-19 overlaps the evening window
        (lisbon(2026, 11, 14, 10), 2, True),  # Saturday morning
        (lisbon(2026, 11, 13, 23, 30), 1, True),  # Fri late, runs into Saturday
        (lisbon(2026, 3, 30, 18), 1, True),  # Monday after DST change, local 18:00
        (lisbon(2026, 3, 30, 17), 0.9, False),  # ends 17:54 local, before the window
    ],
)
def test_time_windows(start: datetime, hours: float, keep: bool) -> None:
    assert filters.in_time_windows(event(start, hours), WINDOWS, LISBON) is keep


def test_time_windows_empty_means_any_time() -> None:
    assert filters.in_time_windows(event(lisbon(2026, 11, 10, 10)), [], LISBON)


def test_not_busy() -> None:
    start = lisbon(2026, 11, 10, 19)
    busy = [(lisbon(2026, 11, 10, 20), lisbon(2026, 11, 10, 21))]
    assert filters.not_busy(event(start, 2), busy) is False
    assert filters.not_busy(event(start, 1), busy) is True  # ends exactly when busy starts
    assert filters.not_busy(event(start, 2, all_day=True), busy) is True
