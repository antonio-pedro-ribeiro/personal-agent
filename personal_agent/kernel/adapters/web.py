"""Generic ``web_page`` and ``web_search`` adapters (spec §1, "Generic adapters").

A content plugin calls ``ctx.web.page(...)`` or ``ctx.web.search(...)`` with its
``ExtractionSpec``; the kernel fetches, discovers feeds, and extracts items. An iCal feed is
returned as a URL so the plugin parses it with its own adapter, without the LLM.
"""

from __future__ import annotations

import calendar
from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Any
from zoneinfo import ZoneInfo

import feedparser

from personal_agent.kernel.adapters.html import discover_feed, html_to_text, strip_tags
from personal_agent.kernel.models import Interests, Item, Source, Topic
from personal_agent.kernel.services.checklist import as_list
from personal_agent.kernel.services.extractor import Document, ExtractionSpec, Extractor
from personal_agent.kernel.services.http import Http, Response
from personal_agent.kernel.services.llm import WebSearch

GENERIC_TYPES = ("web_page", "web_search")
DEFAULT_MAX_QUERIES = 3
DEFAULT_FETCH_PAGES = 2  # listing pages the model may open per query
FEED_MAX_AGE = timedelta(days=60)  # feed entries older than this are not sent to the LLM
DEFAULT_QUERY = "{topic} events in {city} between {start} and {end}"


@dataclass
class PageResult:
    items: list[Item] = field(default_factory=list)
    ical_url: str | None = None  # the plugin parses this with its own iCal adapter
    feed_url: str | None = None


def _int(value: str | None, default: int) -> int:
    try:
        return max(0, int(value)) if value not in (None, "") else default
    except ValueError:
        return default


def _kind(response: Response) -> str:
    ctype = response.content_type.lower()
    head = response.content[:500].lstrip().lower()
    if "calendar" in ctype or head.startswith(b"begin:vcalendar"):
        return "ical"
    if any(t in ctype for t in ("rss", "atom", "xml")) or head.startswith(
        (b"<?xml", b"<rss", b"<feed")
    ):
        return "rss"
    return "html"


def _entry_time(entry: Any) -> datetime | None:
    parsed = entry.get("published_parsed") or entry.get("updated_parsed")
    return datetime.fromtimestamp(calendar.timegm(parsed), UTC) if parsed else None


def feed_documents(content: bytes, now: datetime) -> list[Document]:
    feed = feedparser.parse(content)
    docs: list[Document] = []
    for entry in feed.entries:
        link = entry.get("link", "")
        if not link:
            continue
        published = _entry_time(entry)
        if published and now - published > FEED_MAX_AGE:
            continue
        body = ""
        if entry.get("content"):
            body = entry["content"][0].get("value", "")
        body = body or entry.get("summary", "")
        docs.append(
            Document(
                url=link,
                title=strip_tags(entry.get("title", "")),
                text=strip_tags(body),
                published=published,
            )
        )
    return docs


class WebSources:
    def __init__(self, http: Http, extractor: Extractor | None) -> None:
        self.http = http
        self.extractor = extractor

    # --- web_page -----------------------------------------------------------------------

    def page(self, source: Source, spec: ExtractionSpec, tz: ZoneInfo, now: datetime) -> PageResult:
        url = source.attrs.get("url", "")
        if not url or url.upper() == "TBD":
            raise ValueError(f"{source.origin}: web_page source '{source.id}' needs a 'url'")
        if not self.http.allowed(url):
            raise PermissionError(f"robots.txt disallows fetching {url}")
        response = self.http.get(url)
        kind = _kind(response)

        feed_setting = source.attrs.get("feed", "auto").strip()
        if kind == "html" and feed_setting.lower() != "none":
            feed: tuple[str, str] | None
            if feed_setting.lower() == "auto":
                feed = discover_feed(response.text, response.url)
            else:
                feed = ("ical" if feed_setting.lower().endswith(".ics") else "rss", feed_setting)
            if feed:
                kind, feed_url = feed
                if kind == "ical":
                    return PageResult(ical_url=feed_url, feed_url=feed_url)
                if not self.http.allowed(feed_url):
                    raise PermissionError(f"robots.txt disallows fetching {feed_url}")
                response = self.http.get(feed_url)
                kind = _kind(response)

        if kind == "ical":
            return PageResult(ical_url=response.url, feed_url=response.url)
        extractor = self._need_extractor(source)
        if kind == "rss":
            docs = feed_documents(response.content, now)
            items = extractor.extract(spec, docs, source.id, tz, now)
            return PageResult(items=items, feed_url=response.url)
        title, text = html_to_text(response.text, response.url)
        doc = Document(url=response.url, title=title, text=text)
        return PageResult(items=extractor.extract(spec, [doc], source.id, tz, now, page=True))

    # --- web_search ---------------------------------------------------------------------

    def search(
        self,
        source: Source,
        spec: ExtractionSpec,
        interests: Interests,
        tz: ZoneInfo,
        now: datetime,
        window: timedelta,
    ) -> list[Item]:
        extractor = self._need_extractor(source)
        queries = self.queries(source, interests.topics, interests.profile.city, tz, now, window)
        web_search = WebSearch(
            max_uses=1,
            fetch_uses=_int(source.attrs.get("fetch_pages"), DEFAULT_FETCH_PAGES),
            allowed_domains=as_list(source.attrs.get("allowed_domains")),
            city=interests.profile.city,
            timezone=tz.key,
        )
        items: list[Item] = []
        for query in queries:
            items.extend(extractor.search(spec, query, source.id, tz, now, web_search))
        return items

    @staticmethod
    def topic_coverage(source: Source, topics: Sequence[Topic]) -> tuple[list[Topic], list[Topic]]:
        """Topics this source's ``max_queries`` actually covers this run vs. capped out,
        highest weight first. Lets a caller warn when raising a topic's weight or adding a
        topic silently drops another one from ``web_search`` coverage."""
        limit = max(0, _int(source.attrs.get("max_queries"), DEFAULT_MAX_QUERIES))
        ranked = sorted((t for t in topics if t.enabled), key=lambda t: -t.weight)
        return ranked[:limit], ranked[limit:]

    @staticmethod
    def queries(
        source: Source,
        topics: Sequence[Topic],
        city: str | None,
        tz: ZoneInfo,
        now: datetime,
        window: timedelta,
    ) -> list[str]:
        """One query per topic, highest weight first, capped at ``max_queries``."""
        template = source.attrs.get("query") or DEFAULT_QUERY
        start = now.astimezone(tz).date()
        end = (now + window).astimezone(tz).date()
        included, _excluded = WebSources.topic_coverage(source, topics)
        out: list[str] = []
        for topic in included:
            terms = topic.description or ", ".join([topic.id, *topic.keywords])
            out.append(
                template.format(
                    topic=terms,
                    keywords=", ".join(topic.keywords) or topic.id,
                    city=city or "my area",
                    start=start.isoformat(),
                    end=end.isoformat(),
                )
            )
        return out

    def _need_extractor(self, source: Source) -> Extractor:
        if self.extractor is None:
            raise RuntimeError(
                f"source '{source.id}' needs an LLM: set PERSONAL_AGENT_ANTHROPIC_API_KEY"
            )
        return self.extractor
