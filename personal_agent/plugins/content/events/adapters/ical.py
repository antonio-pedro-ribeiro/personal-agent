"""iCalendar (.ics) adapter: expands recurring events within the search window.

Source attributes: ``url`` (http(s), file://, or a path relative to the config dir) and
optional ``page`` (a human-readable link used when an event has no URL of its own).
"""

from __future__ import annotations

import re
from datetime import date, datetime, time, timedelta
from typing import Any
from zoneinfo import ZoneInfo

import icalendar
import recurring_ical_events

from personal_agent.kernel.context import RunContext
from personal_agent.kernel.models import Item, Source, make_item_id

PLUGIN = "events"
SUMMARY_MAX = 1000
_PRICE = re.compile(
    r"(?:€\s*(\d+(?:[.,]\d{1,2})?)|(\d+(?:[.,]\d{1,2})?)\s*(?:€|eur\b|euros?\b))", re.I
)
_FREE = re.compile(r"\b(free|grátis|gratis|gratuit[oa]|entrada livre)\b", re.I)


def to_datetime(value: date | datetime, tz: ZoneInfo) -> datetime:
    """Aware datetime; floating times and all-day dates are placed in ``tz``."""
    if isinstance(value, datetime):
        return value if value.tzinfo else value.replace(tzinfo=tz)
    return datetime.combine(value, time(), tz)


def parse_price(text: str) -> float | None:
    """Lowest euro amount mentioned, 0 for 'free', else None."""
    amounts = [float((a or b).replace(",", ".")) for a, b in _PRICE.findall(text)]
    if amounts:
        return min(amounts)
    return 0.0 if _FREE.search(text) else None


def _text(component: Any, key: str) -> str:
    value = component.get(key)
    return str(value).strip() if value is not None else ""


def _link(source: Source, event_url: str) -> str:
    if event_url:
        return event_url
    if page := source.attrs.get("page"):
        return page
    url = source.attrs.get("url", "")
    return url if url.startswith(("http://", "https://")) else ""


def fetch(
    source: Source, window_start: datetime, window_end: datetime, tz: ZoneInfo, ctx: RunContext
) -> list[Item]:
    url = source.attrs.get("url")
    if not url:
        raise ValueError(f"{source.origin}: ical source needs a 'url'")
    calendar = icalendar.Calendar.from_ical(ctx.http.get_bytes(url))
    occurrences = recurring_ical_events.of(calendar).between(window_start, window_end)

    items: list[Item] = []
    for event in occurrences:
        if event.name != "VEVENT" or _text(event, "STATUS").upper() == "CANCELLED":
            continue
        raw_start = event.get("DTSTART").dt
        all_day = not isinstance(raw_start, datetime)
        start = to_datetime(raw_start, tz)
        if event.get("DTEND") is not None:
            end = to_datetime(event.get("DTEND").dt, tz)
        elif event.get("DURATION") is not None:
            end = start + event.get("DURATION").dt
        else:
            end = start + timedelta(days=1) if all_day else start

        title = _text(event, "SUMMARY") or "(untitled event)"
        description = _text(event, "DESCRIPTION")
        uid = _text(event, "UID") or title
        extra: dict[str, Any] = {"all_day": all_day, "uid": uid}
        if (geo := event.get("GEO")) is not None:
            extra["lat"], extra["lon"] = float(geo.latitude), float(geo.longitude)
        price = parse_price(f"{title} {description}")
        if price is not None:
            extra["price"] = price

        items.append(
            Item(
                id=make_item_id(source.id, uid, start.isoformat()),
                plugin=PLUGIN,
                title=title,
                url=_link(source, _text(event, "URL")),
                source_id=source.id,
                summary=description[:SUMMARY_MAX],
                start=start,
                end=end,
                location=_text(event, "LOCATION") or None,
                extra=extra,
            )
        )
    return items
