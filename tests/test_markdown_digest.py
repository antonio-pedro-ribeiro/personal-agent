from collections.abc import Callable
from datetime import datetime
from pathlib import Path

import pytest

from personal_agent.kernel.context import RunContext
from personal_agent.kernel.models import OutputConfig, RankedItem
from personal_agent.plugins.outputs.markdown_digest import MarkdownDigestOutput, insert_block
from tests.conftest import LISBON, make_item


def ranked(item_id: str, title: str, plugin: str = "events", **kw: object) -> RankedItem:
    return RankedItem(
        **make_item(item_id, title, plugin=plugin, **kw).model_dump(),
        score=90,
        reason="Matches 'jazz'",
    )


@pytest.fixture
def setup(tmp_path: Path, make_ctx: Callable[..., RunContext], monkeypatch: pytest.MonkeyPatch):  # type: ignore[no-untyped-def]
    monkeypatch.chdir(tmp_path)
    ctx = make_ctx()
    ctx.plugin_titles = {"events": "Plan", "news": "Read"}
    return (
        MarkdownDigestOutput(),
        OutputConfig(id="digest", type="markdown_digest", attrs={"path": "d"}),
        ctx,
    )


def test_publish_creates_file_with_sections(setup) -> None:  # type: ignore[no-untyped-def]
    out, cfg, ctx = setup
    start = datetime(2026, 11, 12, 21, 30, tzinfo=LISBON)
    items = [
        ranked("a", "Jazz", start=start, location="Hot Clube"),
        ranked("b", "Story", plugin="news"),
    ]
    deliveries = out.publish(items, cfg, ctx)
    text = Path("d/2026-11-08.md").read_text(encoding="utf-8")
    assert text.startswith(
        "# Digest 2026-11-08\n\n## Plan\n\n"
        "- **Jazz** — Thu 12 Nov 21:30 · Hot Clube · Matches 'jazz'"
    )
    assert "\n## Read\n\n- **Story**" in text
    assert [d.external_ref for d in deliveries] == [f"d/2026-11-08.md#{i.short_id}" for i in items]
    assert "\r\n" not in Path("d/2026-11-08.md").read_bytes().decode("utf-8")


def test_publish_is_idempotent_and_appends(setup) -> None:  # type: ignore[no-untyped-def]
    out, cfg, ctx = setup
    out.publish([ranked("a", "First")], cfg, ctx)
    out.publish(
        [ranked("a", "First"), ranked("c", "Second"), ranked("n", "News", plugin="news")], cfg, ctx
    )
    text = Path("d/2026-11-08.md").read_text(encoding="utf-8")
    assert text.count("**First**") == 1
    assert text.index("**First**") < text.index("**Second**") < text.index("## Read")


def test_preview_writes_nothing(setup) -> None:  # type: ignore[no-untyped-def]
    out, cfg, ctx = setup
    preview = out.preview([ranked("a", "Jazz")], cfg, ctx)
    assert preview.startswith("→ d/2026-11-08.md") and "## Plan" in preview
    assert not Path("d").exists()


def test_all_day_shows_date_only(setup) -> None:  # type: ignore[no-untyped-def]
    out, cfg, ctx = setup
    item = ranked("a", "Fair", start=datetime(2026, 11, 14, tzinfo=LISBON), extra={"all_day": True})
    assert "Sat 14 Nov · Matches" in out.preview([item], cfg, ctx)


def test_insert_block_into_existing_section_before_next() -> None:
    text = "# D\n\n## Plan\n\n- a\n\n## Read\n\n- r\n"
    assert insert_block(text, "Plan", "- b\n") == "# D\n\n## Plan\n\n- a\n- b\n\n## Read\n\n- r\n"
    assert insert_block(text, "Other", "- o\n").endswith("- r\n\n## Other\n\n- o\n")
