"""Feedback loop (AC 8): calendar and digest read-back, `agent rate`, and the ranking prompt."""

from __future__ import annotations

import shutil
from collections.abc import Callable
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

import pytest

from personal_agent.cli import main
from personal_agent.kernel.context import RunContext
from personal_agent.kernel.models import (
    Config,
    Delivery,
    Feedback,
    Interests,
    OutputConfig,
    RankedItem,
    Source,
    Topic,
)
from personal_agent.kernel.plugin import Registry
from personal_agent.kernel.runner import run
from personal_agent.kernel.services.checklist import parse_feedback, render_item
from personal_agent.kernel.services.ranker import LLMRanker
from personal_agent.plugins.outputs.google_calendar import GoogleCalendarOutput
from personal_agent.plugins.outputs.markdown_digest import MarkdownDigestOutput
from tests.conftest import LISBON, NOW, FakeContent, make_item
from tests.fakes import FakeCalendarService, FakeGoogle, FakeLLM

DEMO = Path(__file__).resolve().parent.parent / "examples" / "demo"


def ranked(item_id: str, title: str, **kw: Any) -> RankedItem:
    base = make_item(item_id, title, plugin="events", **kw)
    return RankedItem(**base.model_dump(), score=90, reason="Matches jazz")


# --- shared checklist parser ----------------------------------------------------------------


def test_parse_feedback_reads_ticks_crlf_and_skips_ambiguous() -> None:
    text = (
        "# Digest\r\n\r\n## Plan\r\n\r\n"
        + render_item("Up", "", "https://a", "aaaaaa1111").replace("[ ] 👍", "[x] 👍")
        + render_item("Down", "", "https://b", "bbbbbb2222").replace("[ ] 👎", "[X] 👎")
        + render_item("Both", "", "https://c", "cccccc3333").replace("[ ]", "[x]")
        + render_item("None", "", "https://d", "dddddd4444")
    ).replace("\n", "\r\n")
    assert parse_feedback(text) == {"aaaaaa1111": "up", "bbbbbb2222": "down"}


def test_parse_feedback_ignores_boxes_outside_entries() -> None:
    text = "Free text\n  - [x] 👍\n## Plan\n- note without id\n  - [x] 👎\n"
    assert parse_feedback(text) == {}


# --- markdown_digest.sync_feedback ------------------------------------------------------------


@pytest.fixture
def digest(tmp_path: Path, make_ctx: Callable[..., RunContext], monkeypatch: pytest.MonkeyPatch):  # type: ignore[no-untyped-def]
    monkeypatch.chdir(tmp_path)
    ctx = make_ctx()
    ctx.plugin_titles = {"events": "Plan"}
    return MarkdownDigestOutput(), OutputConfig(id="digest", type="markdown_digest"), ctx


def test_ticked_digest_box_gives_up_and_down(digest) -> None:  # type: ignore[no-untyped-def]
    out, cfg, ctx = digest
    items = [ranked("a", "Jazz"), ranked("b", "Opera"), ranked("c", "Untouched")]
    deliveries = out.publish(items, cfg, ctx)
    path = Path("digests/2026-11-08.md")
    text = path.read_text(encoding="utf-8")
    a, b = items[0].short_id, items[1].short_id
    text = text.replace(f"`id:{a}`\n  - [ ] 👍", f"`id:{a}`\n  - [x] 👍")
    text = text.replace(f"`id:{b}`\n  - [ ] 👍\n  - [ ] 👎", f"`id:{b}`\n  - [ ] 👍\n  - [x] 👎")
    path.write_text(text, encoding="utf-8")

    feedback = out.sync_feedback(deliveries, cfg, ctx)
    assert {(f.item_id, f.signal) for f in feedback} == {
        (items[0].id, "up"),
        (items[1].id, "down"),
    }
    assert all(f.output_id == "digest" and f.at == NOW for f in feedback)


def test_missing_digest_file_gives_no_signal(digest) -> None:  # type: ignore[no-untyped-def]
    out, cfg, ctx = digest
    deliveries = out.publish([ranked("a", "Jazz")], cfg, ctx)
    Path("digests/2026-11-08.md").unlink()
    assert out.sync_feedback(deliveries, cfg, ctx) == []


# --- google_calendar.sync_feedback ------------------------------------------------------------


@pytest.fixture
def calendar(make_ctx: Callable[..., RunContext]):  # type: ignore[no-untyped-def]
    service = FakeCalendarService()
    ctx = make_ctx()
    ctx.google = FakeGoogle(service, granted={"calendar.app.created"})  # type: ignore[assignment]
    return GoogleCalendarOutput(), OutputConfig(id="calendar", type="google_calendar"), service, ctx


def test_deleted_event_is_rejected_kept_past_event_accepted(calendar) -> None:  # type: ignore[no-untyped-def]
    out, cfg, service, ctx = calendar
    past = datetime(2026, 11, 7, 20, 0, tzinfo=LISBON)
    future = datetime(2026, 11, 12, 21, 30, tzinfo=LISBON)
    items = [
        ranked("del", "Deleted", start=future),
        ranked("can", "Cancelled", start=future),
        ranked("kept", "Kept and over", start=past),
        ranked("soon", "Still upcoming", start=future),
    ]
    deliveries = out.publish(items, cfg, ctx)
    (cal_id,) = service.calendars_db
    events = service.events_db[cal_id]
    events.remove(next(e for e in events if e["summary"] == "Deleted"))
    next(e for e in events if e["summary"] == "Cancelled")["status"] = "cancelled"

    feedback = out.sync_feedback(deliveries, cfg, ctx)
    assert {(f.item_id, f.signal) for f in feedback} == {
        (items[0].id, "rejected"),
        (items[1].id, "rejected"),
        (items[2].id, "accepted"),
    }
    assert set(service.touched_calendars) == {cal_id}  # AC 5: never another calendar


def test_all_day_event_accepted_after_its_day(calendar) -> None:  # type: ignore[no-untyped-def]
    out, cfg, _, ctx = calendar
    day = datetime(2026, 11, 7, tzinfo=LISBON)
    deliveries = out.publish([ranked("fair", "Fair", start=day, extra={"all_day": True})], cfg, ctx)
    late_evening = datetime(2026, 11, 7, 23, 0, tzinfo=LISBON)
    ctx.now = late_evening
    assert out.sync_feedback(deliveries, cfg, ctx) == []
    ctx.now = late_evening + timedelta(hours=2)
    assert [f.signal for f in out.sync_feedback(deliveries, cfg, ctx)] == ["accepted"]


def test_calendar_feedback_without_deliveries_needs_no_google(calendar) -> None:  # type: ignore[no-untyped-def]
    out, cfg, _, ctx = calendar
    ctx.google = None
    assert out.sync_feedback([], cfg, ctx) == []


# --- store: latest signal wins, idempotent ---------------------------------------------------


def test_record_feedback_is_idempotent_and_latest_wins(make_ctx: Callable[..., RunContext]) -> None:
    ctx = make_ctx()
    item = make_item("a", "Jazz", plugin="events")
    ctx.store.record_items([item], NOW)

    def fb(signal: str, hours: int) -> Feedback:
        return Feedback(
            item_id=item.id, output_id="digest", signal=signal, at=NOW + timedelta(hours=hours)
        )

    assert ctx.store.record_feedback([fb("up", 0)]) == 1
    assert ctx.store.record_feedback([fb("up", 1)]) == 0  # same signal on the next run
    assert ctx.store.record_feedback([fb("down", 2)]) == 1
    assert ctx.store.record_feedback([fb("up", 3)]) == 1  # changed back: up is newest again
    assert ctx.store.recent_feedback("events") == [(item, "up")]


# --- AC 8 end to end: feedback recorded on the next run and in the next ranking prompt ------


def test_feedback_reaches_the_next_ranking_prompt(
    tmp_path: Path, make_ctx: Callable[..., RunContext], monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.chdir(tmp_path)
    liked = make_item("aa11", "Gypsy jazz trio", plugin="events")  # ids are hex
    new = make_item("bb22", "Bebop quartet", plugin="events")
    reg = Registry()
    reg.add_content(FakeContent([liked], name="events"))
    reg.add_output(MarkdownDigestOutput())
    config = Config(
        interests=Interests(topics=[Topic(id="jazz", weight=0.9)]),
        sources=[Source(id="s", plugin="events", type="x")],
        outputs=[OutputConfig(id="digest", type="markdown_digest")],
    )
    ctx = make_ctx()
    run(config, reg, ctx)
    path = Path("digests/2026-11-08.md")
    path.write_text(path.read_text(encoding="utf-8").replace("[ ] 👍", "[x] 👍"), "utf-8")

    reg.content["events"] = FakeContent([new], name="events")
    llm = FakeLLM({"scores": [{"id": new.short_id, "score": 80, "reason": "jazz"}]})
    ctx2 = make_ctx(now=NOW + timedelta(days=1))
    ctx2.ranker = LLMRanker(llm)
    report = run(config, reg, ctx2)
    assert report.feedback_recorded == 1
    prompt = llm.calls[0]["prompt"]
    assert "<feedback" in prompt and "- up: Gypsy jazz trio" in prompt
    assert prompt.index("</feedback>") < prompt.index("<candidates")


# --- agent rate ------------------------------------------------------------------------------


def test_agent_rate(
    capsys: pytest.CaptureFixture[str], tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    config = tmp_path / "config"
    shutil.copytree(DEMO, config)
    monkeypatch.chdir(tmp_path)
    db = str(tmp_path / "a.db")
    assert main(["--config-dir", str(config), "--db", db, "run"]) == 0
    digest = next((tmp_path / "out" / "digests").glob("*.md")).read_text(encoding="utf-8")
    short = digest.split("`id:")[1].split("`")[0]
    capsys.readouterr()

    assert main(["--db", db, "rate", f"id:{short}", "down"]) == 0
    assert "Rated down:" in capsys.readouterr().out
    assert main(["--db", db, "rate", "ffffffffff", "up"]) == 2
    assert main(["--db", db, "rate", "abc", "up"]) == 2  # too short

    from personal_agent.kernel.services.store import Store

    store = Store(db)
    try:
        ((item, signal),) = store.recent_feedback("events")
    finally:
        store.close()
    assert signal == "down" and item.short_id == short


def test_run_fail_on_error_exit_code(
    capsys: pytest.CaptureFixture[str], tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    config = tmp_path / "config"
    shutil.copytree(DEMO, config)
    (config / "outputs.md").write_text(
        (config / "outputs.md").read_text(encoding="utf-8") + "\n- [x] **cal** — google_calendar\n",
        encoding="utf-8",
    )
    monkeypatch.chdir(tmp_path)

    def args(db: str) -> list[str]:
        return ["--config-dir", str(config), "--db", str(tmp_path / db), "run"]

    assert main(args("a.db")) == 0  # the calendar fails (no Google), the run itself doesn't
    assert main([*args("b.db"), "--fail-on-error"]) == 4
    captured = capsys.readouterr()
    assert "output cal" in captured.err and "(digest) 3 item(s) published" in captured.out


def test_log_file_captures_output(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    import sys

    monkeypatch.setattr(sys, "stdout", sys.stdout)
    monkeypatch.setattr(sys, "stderr", sys.stderr)
    log = tmp_path / "logs" / "agent.log"
    assert main(["--log-file", str(log), "--config-dir", str(DEMO), "plugins"]) == 0
    sys.stdout.flush()
    text = log.read_text(encoding="utf-8")
    assert "===" in text and "Content plugins:" in text


def test_delivery_without_ref_is_ignored(calendar) -> None:  # type: ignore[no-untyped-def]
    out, cfg, _, ctx = calendar
    out.publish([ranked("a", "A", start=datetime(2026, 11, 12, 20, tzinfo=LISBON))], cfg, ctx)
    assert out.sync_feedback([Delivery(item_id="x", output_id="calendar")], cfg, ctx) == []


def test_fail_on_error_when_llm_is_missing(
    capsys: pytest.CaptureFixture[str], tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A scheduled run that silently fell back to keywords should still go red."""
    monkeypatch.chdir(tmp_path)
    args = ["--config-dir", str(DEMO), "--db", str(tmp_path / "a.db"), "run", "--dry-run"]
    assert main(args) == 0
    assert main([*args, "--fail-on-error"]) == 4
    assert "no working LLM provider" in capsys.readouterr().err
