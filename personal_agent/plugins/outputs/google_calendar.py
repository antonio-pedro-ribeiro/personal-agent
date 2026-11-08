"""Google Calendar output: tentative events on a dedicated *Suggestions* calendar.

Namespace: the agent creates its own secondary calendar (scope ``calendar.app.created``,
which cannot touch any other calendar) and remembers its id in the store. Idempotent: every
event carries the private extended property ``agentItemId``, checked before inserting.
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import timedelta
from typing import Any
from zoneinfo import ZoneInfo

from personal_agent.kernel.context import RunContext
from personal_agent.kernel.models import Delivery, Feedback, OutputConfig, RankedItem

SCOPE = "calendar.app.created"
DEFAULT_NAME = "Suggestions"
DEFAULT_DURATION = timedelta(hours=2)
PROPERTY = "agentItemId"
CALENDAR_DESCRIPTION = (
    "Event suggestions from personal-agent. Keep an event to accept it, delete it to reject it."
)


def event_body(item: RankedItem, tz: ZoneInfo) -> dict[str, Any]:
    assert item.start is not None
    lines = [item.reason, "", item.url, *(f"Also: {u}" for u in item.also_at)]
    lines += ["", f"agent-id:{item.short_id}"]
    body: dict[str, Any] = {
        "summary": item.title,
        "description": "\n".join(lines).strip(),
        "status": "tentative",
        "transparency": "transparent",  # suggestions never show you as busy
        "extendedProperties": {"private": {PROPERTY: item.id}},
        "reminders": {"useDefault": False},
    }
    if item.location:
        body["location"] = item.location
    if item.url.startswith(("http://", "https://")):
        body["source"] = {"title": "personal-agent", "url": item.url}
    if item.extra.get("all_day"):
        first = item.start.astimezone(tz).date()
        last = item.end.astimezone(tz).date() if item.end else first + timedelta(days=1)
        body["start"] = {"date": first.isoformat()}
        body["end"] = {"date": max(last, first + timedelta(days=1)).isoformat()}
    else:
        end = item.end if item.end and item.end > item.start else item.start + DEFAULT_DURATION
        body["start"] = {"dateTime": item.start.isoformat(), "timeZone": tz.key}
        body["end"] = {"dateTime": end.isoformat(), "timeZone": tz.key}
    return body


def _http_status(exc: Exception) -> int | None:
    resp = getattr(exc, "resp", None)
    status = getattr(resp, "status", None)
    return int(status) if status is not None else None


class GoogleCalendarOutput:
    type = "google_calendar"
    google_scopes: list[str] = [SCOPE]  # noqa: RUF012 - protocol attribute

    def accepts(self, item: RankedItem) -> bool:
        return item.start is not None

    def _service(self, ctx: RunContext) -> Any:
        if ctx.google is None:
            raise RuntimeError("Google is not connected: run `agent auth google`")
        return ctx.google.client("calendar", "v3", self.google_scopes)

    def calendar_id(self, service: Any, cfg: OutputConfig, ctx: RunContext) -> str:
        """Id of this output's own calendar, created on first use (or if deleted)."""
        key = f"google_calendar:{cfg.id}:calendar_id"
        cal_id = ctx.store.get_kv(key)
        if cal_id:
            try:
                service.calendars().get(calendarId=cal_id).execute()
                return cal_id
            except Exception as exc:
                if _http_status(exc) not in (404, 410):
                    raise
        created = (
            service.calendars()
            .insert(
                body={
                    "summary": cfg.attrs.get("calendar_name") or DEFAULT_NAME,
                    "description": CALENDAR_DESCRIPTION,
                    "timeZone": ctx.tz.key,
                }
            )
            .execute()
        )
        cal_id = str(created["id"])
        ctx.store.set_kv(key, cal_id)
        return cal_id

    def publish(
        self, items: Sequence[RankedItem], cfg: OutputConfig, ctx: RunContext
    ) -> list[Delivery]:
        service = self._service(ctx)
        cal_id = self.calendar_id(service, cfg, ctx)
        events = service.events()
        deliveries: list[Delivery] = []
        for item in items:
            found = events.list(
                calendarId=cal_id,
                privateExtendedProperty=f"{PROPERTY}={item.id}",
                maxResults=1,
                showDeleted=False,
            ).execute()
            existing = found.get("items") or []
            if existing:
                event_id = existing[0]["id"]
            else:
                event_id = events.insert(
                    calendarId=cal_id, body=event_body(item, ctx.tz)
                ).execute()["id"]
            deliveries.append(Delivery(item_id=item.id, output_id=cfg.id, external_ref=event_id))
        return deliveries

    def sync_feedback(
        self, deliveries: Sequence[Delivery], cfg: OutputConfig, ctx: RunContext
    ) -> list[Feedback]:
        return []  # kept/deleted suggestions are read back in M3

    def preview(self, items: Sequence[RankedItem], cfg: OutputConfig, ctx: RunContext) -> str:
        name = cfg.attrs.get("calendar_name") or DEFAULT_NAME
        lines = [f"→ Google Calendar '{name}' (tentative events)", ""]
        for item in items:
            assert item.start is not None
            local = item.start.astimezone(ctx.tz)
            when = f"{local:%a %d %b}" if item.extra.get("all_day") else f"{local:%a %d %b %H:%M}"
            lines.append(f"- {when}  {item.title}  ({item.score:.0f}: {item.reason})")
        return "\n".join(lines)
