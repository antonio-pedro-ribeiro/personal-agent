"""Run context: the only way plugins reach kernel services."""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import date, datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from personal_agent.kernel.services.availability import Availability
from personal_agent.kernel.services.http import Http
from personal_agent.kernel.services.ranker import Ranker
from personal_agent.kernel.services.store import Store


@dataclass
class RunContext:
    store: Store
    ranker: Ranker
    availability: Availability
    http: Http
    now: datetime  # timezone-aware
    tz: ZoneInfo
    config_dir: Path
    dry_run: bool = False
    plugin_titles: dict[str, str] = field(default_factory=dict)
    warnings: list[str] = field(default_factory=list)
    log: logging.Logger = field(default_factory=lambda: logging.getLogger("personal_agent"))

    @property
    def today(self) -> date:
        return self.now.astimezone(self.tz).date()

    def warn(self, message: str) -> None:
        """Record a non-fatal problem (shown at the end of the run)."""
        self.warnings.append(message)
        self.log.warning(message)
