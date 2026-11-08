"""OS-specific scheduling for local mode: the only module with per-OS scheduler code.

``agent schedule install`` registers one daily job that runs ``agent run`` with absolute paths:

- Windows: a Task Scheduler task (imported from XML, so it gets a working directory and
  "run as soon as possible after a missed start" when the PC was off).
- macOS: a launchd agent (``~/Library/LaunchAgents``, where launchd looks for user agents).
- Linux and other POSIX: a line in the user's crontab, tagged so it can be removed again.

Building the commands is pure (and unit-tested per OS); only ``install``/``remove`` run them.
No shell is involved: the job calls the Python interpreter directly, and output goes to a log
file through ``agent --log-file`` instead of shell redirection.
"""

from __future__ import annotations

import os
import plistlib
import shlex
import subprocess
import sys
import tempfile
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from datetime import time
from pathlib import Path, PurePath
from xml.sax.saxutils import escape

from platformdirs import user_log_dir

TASK_NAME = "personal-agent"
LAUNCHD_LABEL = "com.personal-agent.run"
CRON_TAG = "# personal-agent (managed by `agent schedule`)"

Runner = Callable[[Sequence[str], str | None], subprocess.CompletedProcess[str]]


def default_log_file() -> Path:
    return Path(user_log_dir("personal-agent", appauthor=False)) / "agent.log"


def os_kind(platform: str | None = None) -> str:
    """``windows``, ``macos`` or ``cron`` (Linux and other POSIX systems)."""
    p = platform or sys.platform
    if p.startswith("win"):
        return "windows"
    if p == "darwin":
        return "macos"
    return "cron"


def parse_time(value: str) -> time:
    try:
        hh, mm = value.split(":")
        return time(int(hh), int(mm))
    except ValueError as exc:
        raise ValueError(f"expected HH:MM (24 h), got {value!r}") from exc


@dataclass(frozen=True)
class Job:
    """What to run: ``argv`` in ``workdir`` every day at ``at`` (local time)."""

    argv: list[str]
    workdir: PurePath
    at: time


def agent_argv(
    config_dir: Path, db: Path | None, log_file: Path, python: str | None = None
) -> list[str]:
    """``python -m personal_agent.cli ... run`` with absolute paths (no PATH lookup)."""
    exe = python or _quiet_python()
    argv = [exe, "-m", "personal_agent.cli", "--config-dir", str(config_dir.resolve())]
    if db is not None:
        argv += ["--db", str(Path(db).resolve())]
    return [*argv, "--log-file", str(log_file), "run"]


def _quiet_python() -> str:
    """On Windows prefer ``pythonw.exe`` so a scheduled run doesn't flash a console window."""
    exe = Path(sys.executable)
    if os_kind() == "windows":
        quiet = exe.with_name("pythonw.exe")
        if quiet.is_file():
            return str(quiet)
    return str(exe)


# --- Windows: Task Scheduler --------------------------------------------------------------


def windows_task_xml(job: Job) -> str:
    exe, *args = job.argv
    arguments = subprocess.list2cmdline(args)
    return f"""<?xml version="1.0" encoding="UTF-16"?>
<Task version="1.2" xmlns="http://schemas.microsoft.com/windows/2004/02/mit/task">
  <RegistrationInfo>
    <Description>personal-agent daily run (installed by `agent schedule install`)</Description>
  </RegistrationInfo>
  <Triggers>
    <CalendarTrigger>
      <StartBoundary>2026-01-01T{job.at:%H:%M}:00</StartBoundary>
      <Enabled>true</Enabled>
      <ScheduleByDay>
        <DaysInterval>1</DaysInterval>
      </ScheduleByDay>
    </CalendarTrigger>
  </Triggers>
  <Principals>
    <Principal id="Author">
      <LogonType>InteractiveToken</LogonType>
      <RunLevel>LeastPrivilege</RunLevel>
    </Principal>
  </Principals>
  <Settings>
    <MultipleInstancesPolicy>IgnoreNew</MultipleInstancesPolicy>
    <DisallowStartIfOnBatteries>false</DisallowStartIfOnBatteries>
    <StopIfGoingOnBatteries>false</StopIfGoingOnBatteries>
    <StartWhenAvailable>true</StartWhenAvailable>
    <RunOnlyIfNetworkAvailable>true</RunOnlyIfNetworkAvailable>
    <ExecutionTimeLimit>PT1H</ExecutionTimeLimit>
    <Enabled>true</Enabled>
  </Settings>
  <Actions Context="Author">
    <Exec>
      <Command>{escape(exe)}</Command>
      <Arguments>{escape(arguments)}</Arguments>
      <WorkingDirectory>{escape(str(job.workdir))}</WorkingDirectory>
    </Exec>
  </Actions>
</Task>
"""


def windows_install_commands(xml_path: Path) -> list[list[str]]:
    return [["schtasks", "/Create", "/TN", TASK_NAME, "/XML", str(xml_path), "/F"]]


def windows_remove_commands() -> list[list[str]]:
    return [["schtasks", "/Delete", "/TN", TASK_NAME, "/F"]]


# --- macOS: launchd -----------------------------------------------------------------------


def launchd_plist_path() -> Path:
    return Path.home() / "Library" / "LaunchAgents" / f"{LAUNCHD_LABEL}.plist"


def launchd_plist(job: Job) -> bytes:
    return plistlib.dumps(
        {
            "Label": LAUNCHD_LABEL,
            "ProgramArguments": job.argv,
            "WorkingDirectory": str(job.workdir),
            "StartCalendarInterval": {"Hour": job.at.hour, "Minute": job.at.minute},
            "RunAtLoad": False,
        }
    )


def launchd_install_commands(plist: Path) -> list[list[str]]:
    return [["launchctl", "unload", str(plist)], ["launchctl", "load", "-w", str(plist)]]


def launchd_remove_commands(plist: Path) -> list[list[str]]:
    return [["launchctl", "unload", "-w", str(plist)]]


# --- Linux / POSIX: crontab ---------------------------------------------------------------


def cron_line(job: Job) -> str:
    command = shlex.join(job.argv)
    return f"{job.at.minute} {job.at.hour} * * * cd {shlex.quote(str(job.workdir))} && {command}"


def crontab_with(current: str, job: Job | None) -> str:
    """``current`` crontab without our entry, plus the new entry if ``job`` is given."""
    lines = current.splitlines()
    kept: list[str] = []
    skip_next = False
    for line in lines:
        if skip_next:
            skip_next = False
            continue
        if line.strip() == CRON_TAG:
            skip_next = True  # our tag is always followed by our job line
            continue
        kept.append(line)
    while kept and not kept[-1].strip():
        kept.pop()
    if job is not None:
        kept += [CRON_TAG, cron_line(job)]
    return "\n".join(kept) + "\n" if kept else ""


# --- install / remove ---------------------------------------------------------------------


def _run(argv: Sequence[str], stdin: str | None = None) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        list(argv), input=stdin, capture_output=True, text=True, encoding="utf-8", check=False
    )


def install(job: Job, run: Runner = _run, platform: str | None = None) -> str:
    """Register (or replace) the daily job. Returns a one-line description."""
    kind = os_kind(platform)
    if kind == "windows":
        fd, name = tempfile.mkstemp(suffix=".xml")
        os.close(fd)
        xml_path = Path(name)
        try:
            xml_path.write_text(windows_task_xml(job), encoding="utf-16")
            for argv in windows_install_commands(xml_path):
                _check(run(argv, None), argv)
        finally:
            xml_path.unlink(missing_ok=True)
        return f"Task Scheduler task '{TASK_NAME}', daily at {job.at:%H:%M}"
    if kind == "macos":
        plist = launchd_plist_path()
        plist.parent.mkdir(parents=True, exist_ok=True)
        plist.write_bytes(launchd_plist(job))
        unload, load = launchd_install_commands(plist)
        run(unload, None)  # fine if it wasn't loaded yet
        _check(run(load, None), load)
        return f"launchd agent {plist}, daily at {job.at:%H:%M}"
    current = run(["crontab", "-l"], None)
    existing = current.stdout if current.returncode == 0 else ""
    _check(run(["crontab", "-"], crontab_with(existing, job)), ["crontab", "-"])
    return f"crontab entry, daily at {job.at:%H:%M}"


def remove(run: Runner = _run, platform: str | None = None) -> str:
    kind = os_kind(platform)
    if kind == "windows":
        for argv in windows_remove_commands():
            _check(run(argv, None), argv)
        return f"Removed Task Scheduler task '{TASK_NAME}'"
    if kind == "macos":
        plist = launchd_plist_path()
        if plist.is_file():
            for argv in launchd_remove_commands(plist):
                run(argv, None)
            plist.unlink()
        return f"Removed launchd agent {plist}"
    current = run(["crontab", "-l"], None)
    if current.returncode == 0:
        _check(run(["crontab", "-"], crontab_with(current.stdout, None)), ["crontab", "-"])
    return "Removed the crontab entry"


def _check(result: subprocess.CompletedProcess[str], argv: Sequence[str]) -> None:
    if result.returncode != 0:
        detail = (result.stderr or result.stdout or "").strip()
        raise RuntimeError(f"`{' '.join(argv)}` failed ({result.returncode}): {detail}")
