"""Live progress for ``agent run``: a step log on stderr (spec §5).

``Progress`` writes one line per step and per task, with item counts and timings, so a long run
is not silent. On a terminal a task's label is shown as soon as it starts and its result is
appended when it ends; elsewhere (CI logs, pipes) a task without nested lines is written once,
complete. A ``Progress`` without a stream (the default, and ``--quiet``) writes nothing.
"""

from __future__ import annotations

import time
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from typing import TextIO

INDENT = "      "
LABEL_WIDTH = 18
KIND_WIDTH = 15


def _fit(text: str, width: int) -> str:
    return text if len(text) <= width else text[: width - 1] + "…"


def format_seconds(seconds: float) -> str:
    if seconds < 60:
        return f"{seconds:.1f}s"
    minutes, secs = divmod(round(seconds), 60)
    return f"{minutes}m {secs:02d}s"


def items(n: int) -> str:
    return f"{n} item" if n == 1 else f"{n} items"


class Task:
    """Handle for a running task: set ``result`` (for example "12 items") before it ends."""

    def __init__(self) -> None:
        self.result = ""


@dataclass
class _Open:
    head: str  # indent + label (+ kind)
    blank: str  # same width, spaces only
    shown: bool = False  # head already written
    pending: bool = False  # head written without a newline (live), waiting for its result


class Progress:
    def __init__(
        self,
        stream: TextIO | None = None,
        live: bool | None = None,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self.stream = stream
        if live is None:
            live = bool(getattr(stream, "isatty", lambda: False)())
        self.live = stream is not None and live
        self.clock = clock
        self.total = 0
        self._step = 0
        self._open: list[_Open] = []
        self._started = clock()

    @property
    def enabled(self) -> bool:
        return self.stream is not None

    def _write(self, text: str) -> None:
        if self.stream is None:
            return
        self.stream.write(text)
        self.stream.flush()

    def _before_line(self) -> None:
        """Make room for a new line: end a live pending head, show unshown parent heads."""
        for task in self._open:
            if task.pending:
                self._write("\n")
                task.pending = False
            elif not task.shown:
                self._write(task.head.rstrip() + "\n")
            task.shown = True

    def header(self, text: str) -> None:
        self._before_line()
        self._write(f"{text}\n\n")

    def step(self, title: str, detail: str = "") -> None:
        """A numbered top-level step: ``[2/4] Collecting events (8 sources)``."""
        self._before_line()
        self._step += 1
        number = f"[{self._step}/{self.total}]" if self.total else f"[{self._step}]"
        line = f"{number} {title}"
        if detail:
            line = f"{line} {'.' * max(3, 36 - len(line))} {detail}"
        self._write(line + "\n")

    def line(self, text: str) -> None:
        """A free-form line under the current step or task, for example ``→ 6 kept``."""
        self._before_line()
        self._write(f"{INDENT}{'  ' * len(self._open)}{text}\n")

    @contextmanager
    def task(self, label: str, kind: str = "") -> Iterator[Task]:
        """One unit of work under a step, timed: ``viral-agenda  web_page  41 items  18.3s``.

        Tasks nest (a web search's topics under its source). A failing task prints ``failed``
        and re-raises, so the caller's error handling is unchanged.
        """
        self._before_line()
        nesting = "  " * len(self._open)
        width = max(8, LABEL_WIDTH - len(nesting))  # nested labels keep the columns aligned
        head = (
            f"{INDENT}{nesting}{_fit(label, width):<{width}} {_fit(kind, KIND_WIDTH):<{KIND_WIDTH}}"
        )
        current = _Open(head=head, blank=" " * len(head))
        if self.live:
            self._write(head)
            current.shown = current.pending = True
        self._open.append(current)
        handle = Task()
        start = self.clock()
        outcome = "failed"
        try:
            yield handle
            outcome = handle.result
        finally:
            self._open.pop()
            elapsed = format_seconds(self.clock() - start)
            tail = f" {outcome:>12}  {elapsed:>7}"
            if current.pending:  # nothing printed in between: finish the same line
                self._write(tail + "\n")
            elif current.shown:  # nested lines came in between
                self._write(current.blank + tail + "\n")
            else:
                self._write(head + tail + "\n")

    def done(self) -> None:
        self._before_line()
        self._write(f"Done in {format_seconds(self.clock() - self._started)}\n\n")
