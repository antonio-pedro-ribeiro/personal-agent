"""Kernel run loop: routing, idempotency, failure isolation, dry-run (ACs 2, 3, 6)."""

from collections.abc import Callable

from personal_agent.kernel.context import RunContext
from personal_agent.kernel.models import (
    Config,
    Dislike,
    Feedback,
    Interests,
    OutputConfig,
    Source,
    Topic,
)
from personal_agent.kernel.plugin import ContentPlugin, OutputPlugin, Registry
from personal_agent.kernel.runner import run
from tests.conftest import NOW, FakeContent, FakeOutput, make_item

TOPICS = [Topic(id="jazz", weight=0.9), Topic(id="ai", weight=0.6)]


def config(*outputs: OutputConfig, sources: list[Source] | None = None, **kw: object) -> Config:
    return Config(
        interests=Interests(topics=TOPICS, **kw),  # type: ignore[arg-type]
        sources=sources
        if sources is not None
        else [
            Source(id="s1", plugin="fake", type="x"),
            Source(id="s2", plugin="other", type="x"),
        ],
        outputs=list(outputs),
    )


def registry(contents: list[FakeContent], outputs: list[FakeOutput]) -> Registry:
    reg = Registry()
    for c in contents:
        assert isinstance(c, ContentPlugin)
        reg.add_content(c)
    for o in outputs:
        assert isinstance(o, OutputPlugin)
        reg.add_output(o)
    return reg


ITEMS = [
    make_item("jazz1", "jazz night"),
    make_item("ai1", "ai talk"),
    make_item("none1", "pottery"),
]


def test_routes_by_plugin_min_score_and_accepts(make_ctx: Callable[..., RunContext]) -> None:
    fake, other = (
        FakeContent(ITEMS),
        FakeContent([make_item("o1", "jazz other", plugin="other")], name="other"),
    )
    all_out = FakeOutput("all")
    high = FakeOutput("high")
    picky = FakeOutput("picky", accept=lambda i: "talk" in i.title)
    cfg = config(
        OutputConfig(id="a", type="all"),
        OutputConfig(id="h", type="high", plugins=["fake"], min_score=80),
        OutputConfig(id="p", type="picky"),
    )
    report = run(cfg, registry([fake, other], [all_out, high, picky]), make_ctx())

    assert {i.title for i in all_out.published} == {"jazz night", "ai talk", "jazz other"}
    assert [i.title for i in high.published] == ["jazz night"]
    assert [i.title for i in picky.published] == ["ai talk"]
    assert not report.errors
    # below threshold (50) is never routed
    assert "pottery" not in {i.title for i in all_out.published}


def test_second_run_publishes_nothing_new(make_ctx: Callable[..., RunContext]) -> None:
    out = FakeOutput("o")
    cfg = config(OutputConfig(id="o", type="o"))
    reg = registry([FakeContent(ITEMS)], [out])
    run(cfg, reg, make_ctx())
    first = len(out.published)
    report = run(cfg, reg, make_ctx())
    assert first == 2 and len(out.published) == 2
    assert report.outputs[0].routed == 0


def test_new_output_gets_items_already_delivered_elsewhere(
    make_ctx: Callable[..., RunContext],
) -> None:
    a, b = FakeOutput("a"), FakeOutput("b")
    content = FakeContent(ITEMS)
    run(config(OutputConfig(id="a", type="a")), registry([content], [a, b]), make_ctx())
    run(
        config(OutputConfig(id="a", type="a"), OutputConfig(id="b", type="b")),
        registry([content], [a, b]),
        make_ctx(),
    )
    assert len(a.published) == 2 and len(b.published) == 2


def test_failures_are_isolated(make_ctx: Callable[..., RunContext]) -> None:
    broken_content = FakeContent([], name="other", fail=True)
    good_out, bad_out = FakeOutput("good"), FakeOutput("bad", fail=True)
    cfg = config(OutputConfig(id="g", type="good"), OutputConfig(id="b", type="bad"))
    report = run(
        cfg, registry([FakeContent(ITEMS), broken_content], [good_out, bad_out]), make_ctx()
    )
    assert len(good_out.published) == 2
    assert "plugin other: RuntimeError: boom" in report.errors
    assert "output b: RuntimeError: output down" in report.errors


def test_failed_output_retries_next_run(make_ctx: Callable[..., RunContext]) -> None:
    out = FakeOutput("o", fail=True)
    cfg = config(OutputConfig(id="o", type="o"))
    reg = registry([FakeContent(ITEMS)], [out])
    run(cfg, reg, make_ctx())
    out.fail = False
    run(cfg, reg, make_ctx())
    assert len(out.published) == 2


def test_dry_run_previews_and_writes_nothing(make_ctx: Callable[..., RunContext]) -> None:
    out = FakeOutput("o", feedback=[Feedback(item_id="x", output_id="o", signal="up", at=NOW)])
    cfg = config(OutputConfig(id="o", type="o"))
    reg = registry([FakeContent(ITEMS)], [out])
    ctx = make_ctx(dry_run=True)
    report = run(cfg, reg, ctx)
    assert out.published == [] and out.previews == ["jazz night, ai talk"]
    assert report.outputs[0].preview == "jazz night, ai talk"
    assert report.feedback_recorded == 0 and out.synced == []
    rows = ctx.store.conn.execute(
        "SELECT (SELECT COUNT(*) FROM items) + (SELECT COUNT(*) FROM deliveries)"
        " + (SELECT COUNT(*) FROM feedback) + (SELECT COUNT(*) FROM rankings)"
    ).fetchone()[0]
    assert rows == 0


def test_unchecked_source_and_output_have_no_effect(make_ctx: Callable[..., RunContext]) -> None:
    content, out, off = FakeContent(ITEMS), FakeOutput("o"), FakeOutput("off")
    cfg = config(
        OutputConfig(id="o", type="o"),
        OutputConfig(id="off", type="off", enabled=False),
        sources=[Source(id="s1", plugin="fake", type="x", enabled=False)],
    )
    report = run(cfg, registry([content], [out, off]), make_ctx())
    assert content.collect_calls == 0 and out.published == [] and off.published == []
    assert report.plugins == []


def test_checked_dislikes_drop_items(make_ctx: Callable[..., RunContext]) -> None:
    out = FakeOutput("o")
    cfg = config(
        OutputConfig(id="o", type="o"),
        dislikes=[Dislike(text="Night"), Dislike(text="talk", enabled=False)],
    )
    run(cfg, registry([FakeContent(ITEMS)], [out]), make_ctx())
    assert [i.title for i in out.published] == ["ai talk"]


def test_feedback_sync_records_signals(make_ctx: Callable[..., RunContext]) -> None:
    out = FakeOutput("o")
    cfg = config(OutputConfig(id="o", type="o"))
    reg = registry([FakeContent(ITEMS)], [out])
    run(cfg, reg, make_ctx())
    out.feedback = [Feedback(item_id=ITEMS[0].id, output_id="ignored", signal="up", at=NOW)]
    ctx = make_ctx()
    report = run(cfg, reg, ctx)
    assert report.feedback_recorded == 1
    assert {d.item_id for d in out.synced} == {ITEMS[0].id, ITEMS[1].id}
    ((item, signal),) = ctx.store.recent_feedback("fake")
    assert item.title == "jazz night" and signal == "up"


def test_warnings_for_missing_plugins_and_outputs(make_ctx: Callable[..., RunContext]) -> None:
    cfg = config(OutputConfig(id="x", type="not_installed", origin="outputs.md:3"))
    cfg.sources.append(Source(id="n", plugin="news", type="rss"))
    report = run(cfg, registry([FakeContent(ITEMS)], []), make_ctx())
    joined = "\n".join(report.warnings)
    assert "output type 'not_installed' is not installed" in joined
    assert "no installed content plugin for section 'news'" in joined
    assert "no enabled output routes plugin 'fake'" in joined


def test_only_filters(make_ctx: Callable[..., RunContext]) -> None:
    a, b = FakeOutput("a"), FakeOutput("b")
    cfg = config(OutputConfig(id="a", type="a"), OutputConfig(id="b", type="b"))
    run(cfg, registry([FakeContent(ITEMS)], [a, b]), make_ctx(), only_output="b")
    assert a.published == [] and len(b.published) == 2
