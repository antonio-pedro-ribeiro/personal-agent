"""Small stdlib-only HTML helpers: feed discovery and HTML → readable text."""

from __future__ import annotations

import re
from html.parser import HTMLParser
from urllib.parse import urljoin, urlparse

FEED_TYPES = {
    "application/rss+xml": "rss",
    "application/atom+xml": "rss",
    "text/calendar": "ical",
}
_SKIP_TAGS = {"script", "style", "noscript", "svg", "head", "template", "iframe"}
_BLOCK_TAGS = {
    "p",
    "div",
    "br",
    "li",
    "ul",
    "ol",
    "tr",
    "table",
    "section",
    "article",
    "header",
    "footer",
    "h1",
    "h2",
    "h3",
    "h4",
    "h5",
    "h6",
    "time",
    "dd",
    "dt",
}


class _FeedLinks(HTMLParser):
    def __init__(self, base: str) -> None:
        super().__init__(convert_charrefs=True)
        self.base = base
        self.feeds: list[tuple[str, str, str]] = []  # (kind, url, title)

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag != "link":
            return
        a = {k.lower(): (v or "") for k, v in attrs}
        kind = FEED_TYPES.get(a.get("type", "").lower())
        if kind and "alternate" in a.get("rel", "").lower().split() and a.get("href"):
            self.feeds.append((kind, urljoin(self.base, a["href"]), a.get("title", "")))


def _norm_path(url: str) -> str:
    path = re.sub(r"/\./", "/", urlparse(url).path)
    return path.rstrip("/") or "/"


def discover_feed(html: str, page_url: str) -> tuple[str, str] | None:
    """(kind, url) of the best feed advertised by the page, or None.

    Prefers a calendar feed, then a feed under the page's own path (e.g. a WordPress
    category feed for /eventos/), then the first site feed. Comment feeds are ignored.
    """
    parser = _FeedLinks(page_url)
    parser.feed(html)
    feeds = [
        (kind, url)
        for kind, url, title in parser.feeds
        if "comment" not in url.lower() and "coment" not in title.lower()
    ]
    if not feeds:
        return None
    page_path = _norm_path(page_url)

    def rank(feed: tuple[str, str]) -> tuple[int, int]:
        kind, url = feed
        own = page_path != "/" and _norm_path(url).startswith(page_path + "/")
        return (0 if kind == "ical" else 1, 0 if own else 1)

    return min(feeds, key=rank)


class _Text(HTMLParser):
    def __init__(self, base: str) -> None:
        super().__init__(convert_charrefs=True)
        self.base = base
        self.parts: list[str] = []
        self.skip = 0
        self.href: str | None = None
        self.title = ""
        self._in_title = False

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag == "title":
            self._in_title = True
        if tag in _SKIP_TAGS:
            self.skip += 1
        elif tag in _BLOCK_TAGS:
            self.parts.append("\n")
        if tag == "a":
            href = dict(attrs).get("href") or ""
            self.href = urljoin(self.base, href) if href.startswith(("/", "http")) else None
        if tag == "time":
            dt = dict(attrs).get("datetime")
            if dt and not self.skip:
                self.parts.append(f"[{dt}] ")

    def handle_endtag(self, tag: str) -> None:
        if tag == "title":
            self._in_title = False
        if tag in _SKIP_TAGS and self.skip:
            self.skip -= 1
        elif tag == "a" and self.href and not self.skip:
            self.parts.append(f" <{self.href}>")
            self.href = None
        elif tag in _BLOCK_TAGS:
            self.parts.append("\n")

    def handle_data(self, data: str) -> None:
        if self._in_title:
            self.title += data
        if not self.skip:
            self.parts.append(data)


def html_to_text(html: str, base_url: str) -> tuple[str, str]:
    """(title, text): visible text with links as <url> and <time datetime> values kept."""
    parser = _Text(base_url)
    parser.feed(html)
    text = "".join(parser.parts)
    text = re.sub(r"[ \t\r\f\v]+", " ", text)
    text = re.sub(r"\n\s*\n+", "\n\n", text)
    return parser.title.strip(), text.strip()


def strip_tags(fragment: str) -> str:
    return html_to_text(fragment, "")[1]
