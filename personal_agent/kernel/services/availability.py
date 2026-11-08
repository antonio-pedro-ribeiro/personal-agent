"""Free/busy service. M1 has no calendar: nothing is busy. Google free/busy arrives in M2."""

from __future__ import annotations

from datetime import datetime
from typing import Protocol

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
