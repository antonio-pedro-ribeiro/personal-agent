"""Deterministic event filters (spec: next 14 days, distance, price, time windows, free/busy).

Each filter is a pure function returning True to keep the item. Missing data (no
coordinates, no price) keeps the item: the agent should not hide events it can't judge.
"""

from __future__ import annotations

import math
from collections.abc import Sequence
from datetime import datetime, time, timedelta
from zoneinfo import ZoneInfo

from personal_agent.kernel.models import Item, Profile

WINDOW_DAYS = 14
EARTH_RADIUS_KM = 6371.0
POINT_EVENT = timedelta(minutes=1)


def interval(item: Item) -> tuple[datetime, datetime]:
    """Event interval; events without a usable end are treated as a 1-minute point."""
    assert item.start is not None
    end = item.end if item.end and item.end > item.start else item.start + POINT_EVENT
    return item.start, end


def in_window(item: Item, now: datetime, days: int = WINDOW_DAYS) -> bool:
    """Not over yet, and starting within the next ``days`` days."""
    if item.start is None:
        return False
    start, end = interval(item)
    return end > now and start < now + timedelta(days=days)


def haversine_km(a: tuple[float, float], b: tuple[float, float]) -> float:
    lat1, lon1, lat2, lon2 = map(math.radians, (*a, *b))
    h = (
        math.sin((lat2 - lat1) / 2) ** 2
        + math.cos(lat1) * math.cos(lat2) * math.sin((lon2 - lon1) / 2) ** 2
    )
    return 2 * EARTH_RADIUS_KM * math.asin(math.sqrt(h))


def within_distance(item: Item, profile: Profile) -> bool:
    lat, lon = item.extra.get("lat"), item.extra.get("lon")
    if profile.max_km is None or profile.coordinates is None or lat is None or lon is None:
        return True
    return haversine_km(profile.coordinates, (float(lat), float(lon))) <= profile.max_km


def within_price(item: Item, profile: Profile) -> bool:
    price = item.extra.get("price")
    if profile.max_price is None or price is None:
        return True
    return float(price) <= profile.max_price


def _window_intervals(name: str, day: datetime, tz: ZoneInfo) -> list[tuple[datetime, datetime]]:
    d = day.date()
    midnight = datetime.combine(d, time(), tz)
    next_midnight = datetime.combine(d + timedelta(days=1), time(), tz)
    weekday = d.weekday()  # Mon=0 .. Sun=6
    if name == "weekday_evenings" and weekday < 5:
        return [(datetime.combine(d, time(18), tz), next_midnight)]
    if name == "weekends" and weekday >= 5:
        return [(midnight, next_midnight)]
    return []


def in_time_windows(item: Item, windows: Sequence[str], tz: ZoneInfo) -> bool:
    """True if the event overlaps any configured window (in the profile timezone)."""
    if not windows:
        return True
    start, end = interval(item)
    local_start, local_end = start.astimezone(tz), end.astimezone(tz)
    day = local_start.replace(hour=0, minute=0, second=0, microsecond=0)
    while day < local_end:
        for name in windows:
            for ws, we in _window_intervals(name, day, tz):
                if start < we and end > ws:
                    return True
        day += timedelta(days=1)
    return False


def not_busy(item: Item, busy: Sequence[tuple[datetime, datetime]]) -> bool:
    """Timed events must not overlap a busy slot. All-day events are not checked."""
    if item.extra.get("all_day"):
        return True
    start, end = interval(item)
    return not any(start < be and end > bs for bs, be in busy)
