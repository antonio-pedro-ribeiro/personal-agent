"""Free/busy service: busy slots of the user's primary Google Calendar.

Uses only the ``calendar.freebusy`` scope, which reveals busy intervals, not event contents.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any, Protocol

from personal_agent.kernel.services.google import FREEBUSY_SCOPE, GoogleService

BusySlot = tuple[datetime, datetime]


class Availability(Protocol):
    def busy(self, start: datetime, end: datetime) -> list[BusySlot]: ...


class NoAvailability:
    def busy(self, start: datetime, end: datetime) -> list[BusySlot]:
        return []


class StaticAvailability:
    """Fixed busy slots; used in tests."""

    def __init__(self, slots: list[BusySlot]) -> None:
        self.slots = slots

    def busy(self, start: datetime, end: datetime) -> list[BusySlot]:
        return [(s, e) for s, e in self.slots if s < end and e > start]


def parse_freebusy(response: dict[str, Any], calendar_id: str = "primary") -> list[BusySlot]:
    cal = response.get("calendars", {}).get(calendar_id, {})
    if cal.get("errors"):
        raise RuntimeError(f"free/busy error: {cal['errors']}")
    return [
        (
            datetime.fromisoformat(b["start"].replace("Z", "+00:00")),
            datetime.fromisoformat(b["end"].replace("Z", "+00:00")),
        )
        for b in cal.get("busy", [])
    ]


class GoogleAvailability:
    def __init__(self, google: GoogleService) -> None:
        self.google = google
        self._cache: dict[tuple[datetime, datetime], list[BusySlot]] = {}

    def busy(self, start: datetime, end: datetime) -> list[BusySlot]:
        key = (start, end)
        if key not in self._cache:
            service = self.google.client("calendar", "v3", [FREEBUSY_SCOPE])
            body = {
                "timeMin": start.isoformat(),
                "timeMax": end.isoformat(),
                "items": [{"id": "primary"}],
            }
            self._cache[key] = parse_freebusy(service.freebusy().query(body=body).execute())
        return self._cache[key]
