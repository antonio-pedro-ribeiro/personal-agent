"""Generic web adapters: feed discovery, HTML text, robots, query planning."""

from collections.abc import Callable
from datetime import timedelta

import pytest

from personal_agent.kernel.adapters.html import discover_feed, html_to_text
from personal_agent.kernel.adapters.web import WebSources, feed_documents
from personal_agent.kernel.context import RunContext
from personal_agent.kernel.models import Source, Topic
from personal_agent.kernel.services.extractor import ExtractionSpec, Extractor
from tests.conftest import LISBON, NOW
from tests.fakes import FakeHttp, FakeLLM

SPEC = ExtractionSpec(plugin="events", instructions="events", require_start=True)

PAGE = """<html><head><title>Eventos</title>
<link rel="alternate" type="application/rss+xml" title="Site &raquo; Feed"
 href="https://s.test/feed/" />
<link rel="alternate" type="application/rss+xml" title="Feed de comentários"
 href="https://s.test/comments/feed/" />
<link rel="alternate" type="application/rss+xml" title="Categoria Eventos"
 href="https://s.test/./eventos/feed/" />
</head><body><script>var x=1</script><h1>Eventos</h1>
<p>Encontro <time datetime="2026-11-20T19:00">20 nov</time> em <a href="/e/1">Lisboa</a></p>
</body></html>"""

RSS = """<?xml version="1.0"?><rss version="2.0"><channel><title>Eventos</title>
<item><title>Tertúlia &amp; conversa</title><link>https://s.test/e/1</link>
<pubDate>Mon, 02 Nov 2026 10:00:00 +0000</pubDate>
<description><![CDATA[<p>Encontro a 20 de novembro, 19h, Lisboa.</p>]]></description></item>
<item><title>Old report</title><link>https://s.test/e/0</link>
<pubDate>Mon, 02 Mar 2026 10:00:00 +0000</pubDate><description>old</description></item>
</channel></rss>"""

ONE_EVENT = {"items": [{"doc": 0, "title": "Tertúlia", "url": "https://s.test/e/1",
                        "start": "2026-11-20T19:00"}]}  # fmt: skip


def source(**attrs: str) -> Source:
    return Source(id="s", plugin="events", type="web_page", attrs=attrs, origin="sources.md:9")


def test_discover_prefers_own_section_feed_and_skips_comments() -> None:
    assert discover_feed(PAGE, "https://s.test/eventos/") == (
        "rss",
        "https://s.test/./eventos/feed/",
    )
    assert discover_feed(PAGE, "https://s.test/") == ("rss", "https://s.test/feed/")
    ical = '<link rel="alternate" type="text/calendar" href="/cal.ics">'
    assert discover_feed(PAGE + ical, "https://s.test/eventos/") == (
        "ical",
        "https://s.test/cal.ics",
    )
    assert discover_feed("<p>no feeds</p>", "https://s.test/") is None


def test_html_to_text_keeps_links_and_times() -> None:
    title, text = html_to_text(PAGE, "https://s.test/eventos/")
    assert title == "Eventos"
    assert "[2026-11-20T19:00]" in text and "<https://s.test/e/1>" in text
    assert "var x" not in text


def test_feed_documents_drop_old_entries_and_strip_html() -> None:
    (doc,) = feed_documents(RSS.encode(), NOW)
    assert doc.title == "Tertúlia & conversa" and doc.url == "https://s.test/e/1"
    assert doc.text == "Encontro a 20 de novembro, 19h, Lisboa."


def test_page_discovers_feed_then_extracts(make_ctx: Callable[..., RunContext]) -> None:
    ctx = make_ctx()
    http = FakeHttp({
        "https://s.test/eventos/": ("text/html", PAGE),
        "https://s.test/./eventos/feed/": ("application/rss+xml", RSS),
    })  # fmt: skip
    llm = FakeLLM(ONE_EVENT)
    result = WebSources(http, Extractor(llm, ctx.store)).page(
        source(url="https://s.test/eventos/"), SPEC, LISBON, NOW
    )
    assert [i.title for i in result.items] == ["Tertúlia"]
    assert result.feed_url == "https://s.test/./eventos/feed/"
    assert "Old report" not in llm.calls[0]["prompt"]


def test_ical_feed_is_handed_back_without_llm(make_ctx: Callable[..., RunContext]) -> None:
    ctx = make_ctx()
    page = '<link rel="alternate" type="text/calendar" href="https://s.test/cal.ics">'
    llm = FakeLLM({"items": []})
    http = FakeHttp({"https://s.test/": ("text/html", page)})
    result = WebSources(http, Extractor(llm, ctx.store)).page(
        source(url="https://s.test/"), SPEC, LISBON, NOW
    )
    assert result.ical_url == "https://s.test/cal.ics" and llm.calls == []


def test_feed_none_extracts_the_page_itself(make_ctx: Callable[..., RunContext]) -> None:
    ctx = make_ctx()
    llm = FakeLLM(ONE_EVENT)
    http = FakeHttp({"https://s.test/eventos/": ("text/html", PAGE)})
    WebSources(http, Extractor(llm, ctx.store)).page(
        source(url="https://s.test/eventos/", feed="none"), SPEC, LISBON, NOW
    )
    assert http.fetched == ["https://s.test/eventos/"]
    assert "[2026-11-20T19:00]" in llm.calls[0]["prompt"]


def test_robots_and_missing_url_and_no_llm(make_ctx: Callable[..., RunContext]) -> None:
    ctx = make_ctx()
    http = FakeHttp({"https://s.test/": ("text/html", "<p>x</p>")}, disallow={"https://s.test/"})
    web = WebSources(http, Extractor(FakeLLM({"items": []}), ctx.store))
    with pytest.raises(PermissionError):
        web.page(source(url="https://s.test/"), SPEC, LISBON, NOW)
    with pytest.raises(ValueError, match="needs a 'url'"):
        web.page(source(url="TBD"), SPEC, LISBON, NOW)
    http.disallow.clear()
    with pytest.raises(RuntimeError, match="PERSONAL_AGENT_ANTHROPIC_API_KEY"):
        WebSources(http, None).page(source(url="https://s.test/"), SPEC, LISBON, NOW)


def test_queries_one_per_topic_by_weight_and_capped() -> None:
    topics = [
        Topic(id="ai", description="AI meetups", weight=0.6),
        Topic(id="jazz", description="Live jazz", weight=0.9),
        Topic(id="off", weight=1.0, enabled=False),
    ]
    src = Source(id="web", plugin="events", type="web_search", attrs={"max_queries": "1"})
    (q,) = WebSources.queries(src, topics, "Lisbon", LISBON, NOW, timedelta(days=14))
    assert q == "Live jazz events in Lisbon between 2026-11-08 and 2026-11-22"
    src.attrs["max_queries"] = "5"
    assert len(WebSources.queries(src, topics, "Lisbon", LISBON, NOW, timedelta(days=14))) == 2
