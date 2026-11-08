"""Offline fakes for the LLM, HTTP and Google Calendar (tests never touch the network)."""

from __future__ import annotations

import itertools
from collections.abc import Callable
from typing import Any

from pydantic import BaseModel

from personal_agent.kernel.services.http import Response
from personal_agent.kernel.services.llm import ParseResult, Usage, WebSearch


class FakeLLM:
    """Returns canned outputs; ``respond`` maps (system, prompt) to a dict for the schema."""

    model = "claude-opus-5-5"

    def __init__(
        self,
        respond: Callable[[str, str], dict[str, Any]] | dict[str, Any],
        result_urls: set[str] | None = None,
        fail: bool = False,
    ) -> None:
        self.respond = respond
        self.result_urls = result_urls or set()
        self.fail = fail
        self.calls: list[dict[str, Any]] = []
        self.usage = Usage()

    def parse(
        self,
        *,
        system: str,
        prompt: str,
        schema: type[Any],
        web_search: WebSearch | None = None,
        max_tokens: int = 16000,
    ) -> ParseResult[Any]:
        self.calls.append({"system": system, "prompt": prompt, "web_search": web_search})
        self.usage.calls += 1
        if self.fail:
            raise RuntimeError("llm down")
        data = self.respond(system, prompt) if callable(self.respond) else self.respond
        output: BaseModel = schema.model_validate(data)
        return ParseResult(output=output, result_urls=set(self.result_urls))


class FakeHttp:
    def __init__(self, pages: dict[str, tuple[str, str]], disallow: set[str] | None = None):
        self.pages = pages  # url -> (content type, body)
        self.disallow = disallow or set()
        self.fetched: list[str] = []

    def get(self, url: str) -> Response:
        self.fetched.append(url)
        if url not in self.pages:
            raise FileNotFoundError(url)
        ctype, body = self.pages[url]
        return Response(url=url, content=body.encode("utf-8"), content_type=ctype)

    def get_bytes(self, url: str) -> bytes:
        return self.get(url).content

    def allowed(self, url: str) -> bool:
        return url not in self.disallow


class HttpError(Exception):
    def __init__(self, status: int) -> None:
        super().__init__(f"HTTP {status}")
        self.resp = type("R", (), {"status": status})()


class _Call:
    def __init__(self, fn: Callable[[], Any]) -> None:
        self.fn = fn

    def execute(self) -> Any:
        return self.fn()


class FakeCalendarService:
    """Just enough of the Calendar v3 discovery client for the output plugin."""

    def __init__(self) -> None:
        self.calendars_db: dict[str, dict[str, Any]] = {}
        self.events_db: dict[str, list[dict[str, Any]]] = {}
        self.touched_calendars: list[str] = []
        self._ids = itertools.count(1)

    def calendars(self) -> FakeCalendarService:
        return self

    def events(self) -> _Events:
        return _Events(self)

    def get(self, calendarId: str) -> _Call:
        def run() -> dict[str, Any]:
            self.touched_calendars.append(calendarId)
            if calendarId not in self.calendars_db:
                raise HttpError(404)
            return self.calendars_db[calendarId]

        return _Call(run)

    def insert(self, body: dict[str, Any]) -> _Call:
        def run() -> dict[str, Any]:
            cal_id = f"cal{next(self._ids)}@group.calendar.google.com"
            self.calendars_db[cal_id] = {"id": cal_id, **body}
            self.events_db[cal_id] = []
            return self.calendars_db[cal_id]

        return _Call(run)


class _Events:
    def __init__(self, svc: FakeCalendarService) -> None:
        self.svc = svc

    def list(self, calendarId: str, privateExtendedProperty: str, **_: Any) -> _Call:
        key, _, value = privateExtendedProperty.partition("=")

        def run() -> dict[str, Any]:
            self.svc.touched_calendars.append(calendarId)
            items = [
                e
                for e in self.svc.events_db.get(calendarId, [])
                if e["extendedProperties"]["private"].get(key) == value
            ]
            return {"items": items}

        return _Call(run)

    def insert(self, calendarId: str, body: dict[str, Any]) -> _Call:
        def run() -> dict[str, Any]:
            self.svc.touched_calendars.append(calendarId)
            event = {"id": f"ev{next(self.svc._ids)}", **body}
            self.svc.events_db.setdefault(calendarId, []).append(event)
            return event

        return _Call(run)


class FakeGoogle:
    def __init__(self, service: Any, granted: set[str]) -> None:
        self.service = service
        self.granted = granted
        self.requested: list[list[str]] = []

    def missing(self, scopes: Any) -> list[str]:
        return [s for s in scopes if s not in self.granted]

    def client(self, api: str, version: str, scopes: list[str]) -> Any:
        self.requested.append(list(scopes))
        if missing := self.missing(scopes):
            raise PermissionError(f"missing {missing}")
        return self.service
