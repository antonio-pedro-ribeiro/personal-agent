"""End-to-end through the CLI with the offline demo config and installed entry points."""

import shutil
from pathlib import Path

import pytest

from personal_agent.cli import main
from personal_agent.kernel.plugin import discover

DEMO = Path(__file__).resolve().parent.parent / "examples" / "demo"


def test_entry_points_discover_builtin_plugins() -> None:
    registry = discover()
    assert "events" in registry.content
    assert "markdown_digest" in registry.outputs
    assert registry.warnings == []


def test_demo_dry_run(capsys: pytest.CaptureFixture[str], tmp_path: Path) -> None:
    code = main(["--config-dir", str(DEMO), "--db", str(tmp_path / "a.db"), "run", "--dry-run"])
    out = capsys.readouterr().out
    assert code == 0
    assert "DRY RUN" in out and "## Plan" in out
    assert "jam session" in out and "LLM engineering meetup" in out
    for dropped in ("networking", "Morning AI workshop", "Porto", "gala"):
        assert dropped not in out  # dislike, time window, distance, price


def test_run_writes_digest_once(
    capsys: pytest.CaptureFixture[str], tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    config = tmp_path / "config"
    shutil.copytree(DEMO, config)
    monkeypatch.chdir(tmp_path)
    args = ["--config-dir", str(config), "--db", str(tmp_path / "a.db"), "run"]
    assert main(args) == 0
    assert "3 item(s) published" in capsys.readouterr().out
    digests = list((tmp_path / "out" / "digests").glob("*.md"))
    assert len(digests) == 1
    first = digests[0].read_text(encoding="utf-8")
    assert main(args) == 0
    assert "0 item(s) published" in capsys.readouterr().out
    assert digests[0].read_text(encoding="utf-8") == first


def test_plugins_and_sources_test(capsys: pytest.CaptureFixture[str]) -> None:
    assert main(["--config-dir", str(DEMO), "plugins"]) == 0
    assert "events" in capsys.readouterr().out
    assert main(["--config-dir", str(DEMO), "sources", "test", "demo-venues"]) == 0
    out = capsys.readouterr().out
    assert "demo-venues (ical):" in out and "before filters" in out
    assert main(["--config-dir", str(DEMO), "sources", "test", "nope"]) == 2


def test_auth_and_outputs_test_fail_clearly(
    capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("PERSONAL_AGENT_GOOGLE_CLIENT_SECRETS", raising=False)
    assert main(["--config-dir", str(DEMO), "auth", "google"]) == 2
    assert "OAuth client file" in capsys.readouterr().err
    assert main(["--config-dir", str(DEMO), "outputs", "test", "nope"]) == 2
