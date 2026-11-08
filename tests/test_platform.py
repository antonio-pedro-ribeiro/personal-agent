"""Scheduler command generation per OS (local mode, `agent schedule install|remove`)."""

from __future__ import annotations

import plistlib
import subprocess
from collections.abc import Sequence
from datetime import time
from pathlib import Path, PurePosixPath, PureWindowsPath

import pytest

from personal_agent.kernel import platform
from personal_agent.kernel.platform import CRON_TAG, Job

JOB = Job(
    argv=["/opt/py/bin/python", "-m", "personal_agent.cli", "--config-dir", "/my cfg", "run"],
    workdir=PurePosixPath("/home/me/agent"),
    at=time(7, 5),
)


class FakeRunner:
    def __init__(self, crontab: str | None = None) -> None:
        self.calls: list[tuple[list[str], str | None]] = []
        self.crontab = crontab

    def __call__(self, argv: Sequence[str], stdin: str | None) -> subprocess.CompletedProcess[str]:
        self.calls.append((list(argv), stdin))
        if list(argv) == ["crontab", "-l"]:
            if self.crontab is None:
                return subprocess.CompletedProcess(argv, 1, "", "no crontab for me")
            return subprocess.CompletedProcess(argv, 0, self.crontab, "")
        if list(argv) == ["crontab", "-"]:
            self.crontab = stdin
        return subprocess.CompletedProcess(argv, 0, "", "")


@pytest.mark.parametrize(
    ("name", "kind"), [("win32", "windows"), ("darwin", "macos"), ("linux", "cron")]
)
def test_os_kind(name: str, kind: str) -> None:
    assert platform.os_kind(name) == kind


def test_parse_time() -> None:
    assert platform.parse_time("07:30") == time(7, 30)
    with pytest.raises(ValueError, match="HH:MM"):
        platform.parse_time("7am")


def test_agent_argv_is_absolute_and_logs_to_file(tmp_path: Path) -> None:
    argv = platform.agent_argv(Path("config"), None, tmp_path / "a.log", python="py")
    assert argv[:3] == ["py", "-m", "personal_agent.cli"]
    assert Path(argv[argv.index("--config-dir") + 1]).is_absolute()
    assert argv[-1] == "run" and "--db" not in argv
    assert argv[argv.index("--log-file") + 1] == str(tmp_path / "a.log")
    with_db = platform.agent_argv(Path("c"), Path("x.db"), tmp_path / "a.log", python="py")
    assert Path(with_db[with_db.index("--db") + 1]).is_absolute()


def test_windows_task_xml_and_commands(tmp_path: Path) -> None:
    job = Job(
        argv=[
            r"C:\Py\pythonw.exe",
            "-m",
            "personal_agent.cli",
            "--config-dir",
            r"C:\My Cfg",
            "run",
        ],
        workdir=PureWindowsPath(r"C:\Agent & Co"),
        at=time(6, 0),
    )
    xml = platform.windows_task_xml(job)
    assert "<StartBoundary>2026-01-01T06:00:00</StartBoundary>" in xml
    assert r"<Command>C:\Py\pythonw.exe</Command>" in xml
    assert '"C:\\My Cfg"' in xml  # argument with a space is quoted
    assert "C:\\Agent &amp; Co</WorkingDirectory>" in xml  # XML-escaped
    assert "<StartWhenAvailable>true</StartWhenAvailable>" in xml
    assert platform.windows_install_commands(Path("t.xml")) == [
        ["schtasks", "/Create", "/TN", "personal-agent", "/XML", "t.xml", "/F"]
    ]
    assert platform.windows_remove_commands() == [
        ["schtasks", "/Delete", "/TN", "personal-agent", "/F"]
    ]


def test_windows_install_writes_utf16_xml_then_deletes_it() -> None:
    seen: dict[str, str] = {}

    def run(argv: Sequence[str], stdin: str | None) -> subprocess.CompletedProcess[str]:
        xml_path = Path(argv[argv.index("/XML") + 1])
        seen["xml"] = xml_path.read_text(encoding="utf-16")
        seen["path"] = str(xml_path)
        return subprocess.CompletedProcess(argv, 0, "", "")

    assert "daily at 07:05" in platform.install(JOB, run=run, platform="win32")
    assert "<Exec>" in seen["xml"] and not Path(seen["path"]).exists()


def test_launchd_plist(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    plist = plistlib.loads(platform.launchd_plist(JOB))
    assert plist["ProgramArguments"] == JOB.argv
    assert plist["StartCalendarInterval"] == {"Hour": 7, "Minute": 5}
    assert plist["WorkingDirectory"] == str(JOB.workdir)

    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    runner = FakeRunner()
    platform.install(JOB, run=runner, platform="darwin")
    path = tmp_path / "Library" / "LaunchAgents" / "com.personal-agent.run.plist"
    assert path.is_file()
    assert runner.calls[-1][0] == ["launchctl", "load", "-w", str(path)]
    platform.remove(run=runner, platform="darwin")
    assert not path.exists()


def test_cron_line_quotes_paths() -> None:
    line = platform.cron_line(JOB)
    assert line.startswith("5 7 * * * cd /home/me/agent && /opt/py/bin/python -m")
    assert "'/my cfg'" in line


def test_crontab_install_replaces_and_remove_keeps_user_lines() -> None:
    runner = FakeRunner(crontab="MAILTO=me\n0 1 * * * backup\n")
    platform.install(JOB, run=runner, platform="linux")
    platform.install(JOB, run=runner, platform="linux")  # re-install: still one entry
    assert runner.crontab is not None
    assert runner.crontab.count(CRON_TAG) == 1
    assert runner.crontab.startswith("MAILTO=me\n0 1 * * * backup\n")
    platform.remove(run=runner, platform="linux")
    assert runner.crontab == "MAILTO=me\n0 1 * * * backup\n"


def test_crontab_install_without_existing_crontab() -> None:
    runner = FakeRunner(crontab=None)
    platform.install(JOB, run=runner, platform="linux")
    assert runner.crontab == f"{CRON_TAG}\n{platform.cron_line(JOB)}\n"


def test_failed_command_raises_clear_error() -> None:
    def run(argv: Sequence[str], stdin: str | None) -> subprocess.CompletedProcess[str]:
        return subprocess.CompletedProcess(argv, 1, "", "ERROR: Access is denied.")

    with pytest.raises(RuntimeError, match="Access is denied"):
        platform.remove(run=run, platform="win32")
