"""Live step log for ``agent run`` (spec §5): format, terminal vs. CI, failures, runner wiring."""

from __future__ import annotations

import io
import re
from collections.abc import Callable, Iterator

import pytest

from personal_agent.kernel.context import RunContext
from personal_agent.kernel.models import OutputConfig
from personal_agent.kernel.runner import run
from personal_agent.kernel.services.progress import Progress, format_seconds, items
from tests.conftest import FakeContent, FakeOutput
from tests.test_runner import ITEMS, config, registry


def fake_clock(*ticks: float) -> Callable[[], float]:
    values: Iterator[float] = iter(ticks)
    return lambda: next(values)


def lines(stream: io.StringIO) -> list[str]:
    return [line.rstrip() for line in stream.getvalue().strip().splitlines()]


def test_silent_without_a_stream() -> None:
    progress = Progress()
    progress.step("Feedback")
    with progress.task("x", "ical") as task:
        task.result = "1 item"
    progress.done()
    assert not progress.enabled


def test_ci_writes_complete_lines_with_counts_and_timings() -> None:
    out = io.StringIO()
    progress = Progress(out, live=False, clock=fake_clock(0, 1, 5.25, 10))
    progress.total = 2
    progress.step("Collecting events (1 source)")
    with progress.task("viral-agenda", "web_page") as task:
        task.result = items(41)
    progress.line("→ 41 found")
    progress.done()
    assert lines(out) == [
        "[1/2] Collecting events (1 source)",
        "      viral-agenda       web_page            41 items     4.2s",
        "      → 41 found",
        "Done in 10.0s",
    ]


def test_nested_tasks_show_the_parent_first() -> None:
    out = io.StringIO()
    progress = Progress(out, live=False, clock=fake_clock(0, 0, 1, 3, 3))
    with progress.task("web", "web_search") as parent:
        with progress.task("psychedelics") as child:
            child.result = items(6)
        parent.result = items(6)
    text = lines(out)
    assert text[0].strip().split() == ["web", "web_search"]
    assert text[1].split() == ["psychedelics", "6", "items", "2.0s"]
    assert text[2].split() == ["6", "items", "3.0s"]
    assert text[1].index("6 items") == text[2].index("6 items")  # columns line up


def test_terminal_shows_the_label_before_the_work_finishes() -> None:
    out = io.StringIO()
    progress = Progress(out, live=True, clock=fake_clock(0, 0, 2))
    with progress.task("champalimaud", "web_page") as task:
        assert out.getvalue().rstrip().endswith("web_page")  # visible while running
        assert "\n" not in out.getvalue()
        task.result = items(0)
    assert lines(out)[0].split() == ["champalimaud", "web_page", "0", "items", "2.0s"]


def test_a_failing_task_says_failed_and_reraises() -> None:
    out = io.StringIO()
    progress = Progress(out, live=False, clock=fake_clock(0, 0, 1))
    with pytest.raises(RuntimeError), progress.task("broken", "ical"):
        raise RuntimeError("down")
    assert "failed" in out.getvalue()


def test_format_seconds() -> None:
    assert format_seconds(4.14) == "4.1s"
    assert format_seconds(161) == "2m 41s"
    assert items(1) == "1 item"


def test_runner_reports_each_step(make_ctx: Callable[..., RunContext]) -> None:
    out = io.StringIO()
    ctx = make_ctx(dry_run=True)
    ctx.progress = Progress(out, live=False)
    broken = FakeContent([], name="other", fail=True)
    cfg = config(OutputConfig(id="o", type="o"))
    run(cfg, registry([FakeContent(ITEMS), broken], [FakeOutput("o")]), ctx)
    text = out.getvalue()
    assert "[1/6] Feedback" in text and "skipped (dry run)" in text
    assert "[2/6] Collecting fake (1 source)" in text
    assert "→ 3 found, 3 not yet delivered" in text
    assert "→ 2 kept (threshold 50)" in text
    assert "✗ other failed: RuntimeError: boom" in text
    assert re.search(r"\[5/6\] Filtering & ranking other \.+ skipped", text)
    assert "[6/6] Outputs" in text and "2 to publish" in text
