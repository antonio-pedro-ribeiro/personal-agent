"""Google Calendar output: tentative events on a dedicated *Suggestions* calendar.

Namespace: the agent creates its own secondary calendar (scope ``calendar.app.created``,
which cannot touch any other calendar) and remembers its id in the store. Idempotent: every
event carries the private extended property ``agentItemId``, checked before inserting.
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import date, datetime, time, timedelta, tzinfo
from typing import Any
from zoneinfo import ZoneInfo

from personal_agent.kernel.context import RunContext
from personal_agent.kernel.models import (
    Delivery,
    Feedback,
    OutputConfig,
    OwnedEntry,
    RankedItem,
    Signal,
)

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


def _event_end(event: dict[str, Any], tz: tzinfo) -> datetime | None:
    end = event.get("end") or {}
    if value := end.get("dateTime"):
        return datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    if value := end.get("date"):  # all-day: exclusive end date, midnight in the calendar tz
        return datetime.combine(date.fromisoformat(str(value)), time(0), tz)
    return None


def event_signal(
    events: Any, cal_id: str, event_id: str, now: datetime, tz: tzinfo
) -> Signal | None:
    """Feedback from one suggestion: deleted → rejected, ended and kept → accepted."""
    try:
        event = events.get(calendarId=cal_id, eventId=event_id).execute()
    except Exception as exc:
        if _http_status(exc) in (404, 410):
            return "rejected"
        raise
    if event.get("status") == "cancelled":
        return "rejected"
    end = _event_end(event, tz)
    if end is not None and end <= now:
        return "accepted"
    return None  # still upcoming: no signal yet


class GoogleCalendarOutput:
    type = "google_calendar"
    google_scopes: list[str] = [SCOPE]  # noqa: RUF012 - protocol attribute

    def accepts(self, item: RankedItem) -> bool:
        return item.start is not None

    def _service(self, ctx: RunContext) -> Any:
        if ctx.google is None:
            raise RuntimeError("Google is not connected: run `agent auth google`")
        return ctx.google.client("calendar", "v3", self.google_scopes)

    @staticmethod
    def _calendar_key(cfg: OutputConfig) -> str:
        return f"google_calendar:{cfg.id}:calendar_id"

    def calendar_id(self, service: Any, cfg: OutputConfig, ctx: RunContext) -> str:
        """Id of this output's own calendar, created on first use (or if deleted)."""
        key = self._calendar_key(cfg)
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
        """Deleted suggestion → ``rejected``; still there after it ended → ``accepted``."""
        if not deliveries:
            return []
        cal_id = ctx.store.get_kv(self._calendar_key(cfg))
        if not cal_id:
            return []  # our calendar was never created, so nothing was delivered here
        events = self._service(ctx).events()
        feedback: list[Feedback] = []
        for d in deliveries:
            if not d.external_ref:
                continue
            signal = event_signal(events, cal_id, d.external_ref, ctx.now, ctx.tz)
            if signal is not None:
                feedback.append(
                    Feedback(item_id=d.item_id, output_id=cfg.id, signal=signal, at=ctx.now)
                )
        return feedback

    def owned_entries(self, cfg: OutputConfig, ctx: RunContext) -> list[OwnedEntry]:
        """Events the agent created in its own calendar: those carrying ``agentItemId``.
        Events you added to the Suggestions calendar yourself are not listed."""
        cal_id = ctx.store.get_kv(self._calendar_key(cfg))
        if not cal_id:
            return []
        events = self._service(ctx).events()
        entries: list[OwnedEntry] = []
        page_token: str | None = None
        while True:
            try:
                page = events.list(
                    calendarId=cal_id, showDeleted=False, maxResults=250, pageToken=page_token
                ).execute()
            except Exception as exc:
                if _http_status(exc) in (404, 410):
                    return []  # the calendar itself is gone
                raise
            for event in page.get("items") or []:
                private = (event.get("extendedProperties") or {}).get("private") or {}
                if item_id := private.get(PROPERTY):
                    start = event.get("start") or {}
                    entries.append(
                        OwnedEntry(
                            item_id=str(item_id),
                            external_ref=str(event["id"]),
                            title=str(event.get("summary", "")),
                            when=str(start.get("dateTime") or start.get("date") or ""),
                        )
                    )
            page_token = page.get("nextPageToken")
            if not page_token:
                return entries

    def delete_entries(
        self, entries: Sequence[OwnedEntry], cfg: OutputConfig, ctx: RunContext
    ) -> list[OwnedEntry]:
        cal_id = ctx.store.get_kv(self._calendar_key(cfg))
        if not cal_id or not entries:
            return []
        events = self._service(ctx).events()
        gone: list[OwnedEntry] = []
        for entry in entries:
            try:
                events.delete(calendarId=cal_id, eventId=entry.external_ref).execute()
            except Exception as exc:
                if _http_status(exc) not in (404, 410):
                    ctx.warn(f"could not delete '{entry.title}': {type(exc).__name__}: {exc}")
                    continue
            gone.append(entry)
        return gone

    def preview(self, items: Sequence[RankedItem], cfg: OutputConfig, ctx: RunContext) -> str:
        name = cfg.attrs.get("calendar_name") or DEFAULT_NAME
        lines = [f"→ Google Calendar '{name}' (tentative events)", ""]
        for item in items:
            assert item.start is not None
            local = item.start.astimezone(ctx.tz)
            when = f"{local:%a %d %b}" if item.extra.get("all_day") else f"{local:%a %d %b %H:%M}"
            lines.append(f"- {when}  {item.title}  ({item.score:.0f}: {item.reason})")
        return "\n".join(lines)
