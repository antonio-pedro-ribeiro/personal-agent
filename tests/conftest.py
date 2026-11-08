from __future__ import annotations

from collections.abc import Callable, Sequence
from datetime import UTC, datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest

from personal_agent.kernel.context import RunContext
from personal_agent.kernel.models import (
    Delivery,
    Feedback,
    Interests,
    Item,
    OutputConfig,
    RankedItem,
    RankHints,
    Source,
)
from personal_agent.kernel.services.availability import Availability, NoAvailability
from personal_agent.kernel.services.http import HttpService
from personal_agent.kernel.services.ranker import KeywordRanker
from personal_agent.kernel.services.store import Store

NOW = datetime(2026, 11, 8, 12, 0, tzinfo=UTC)  # a Sunday


@pytest.fixture(autouse=True)
def _no_real_credentials(monkeypatch: pytest.MonkeyPatch) -> None:
    """Tests never use real keys, the OS keyring or Google (AGENTS.md: no network)."""
    from personal_agent.kernel.services import google

    for name in ("PERSONAL_AGENT_ANTHROPIC_API_KEY", "ANTHROPIC_API_KEY", google.TOKEN_ENV):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setattr(google, "load_token", lambda: (None, "test"))


LISBON = ZoneInfo("Europe/Lisbon")


@pytest.fixture
def make_ctx(tmp_path: Path) -> Callable[..., RunContext]:
    def factory(
        now: datetime = NOW,
        dry_run: bool = False,
        availability: Availability | None = None,
        tz: ZoneInfo = LISBON,
    ) -> RunContext:
        return RunContext(
            store=Store(tmp_path / "agent.db"),
            ranker=KeywordRanker(),
            availability=availability or NoAvailability(),
            http=HttpService(base_dir=tmp_path),
            now=now,
            tz=tz,
            config_dir=tmp_path,
            dry_run=dry_run,
        )

    return factory


def make_item(item_id: str, title: str, plugin: str = "fake", **kwargs: object) -> Item:
    return Item(
        id=item_id.ljust(40, "0"),
        plugin=plugin,
        title=title,
        url=f"https://example.com/{item_id}",
        source_id="src",
        **kwargs,  # type: ignore[arg-type]
    )


class FakeContent:
    """Content plugin returning fixed items; optionally raising."""

    def __init__(self, items: Sequence[Item], name: str = "fake", fail: bool = False) -> None:
        self.name = name
        self.title = name.title()
        self.items = list(items)
        self.fail = fail
        self.collect_calls = 0

    def collect(
        self, sources: Sequence[Source], interests: Interests, ctx: RunContext
    ) -> list[Item]:
        self.collect_calls += 1
        if self.fail:
            raise RuntimeError("boom")
        return list(self.items)

    def filter(self, items: Sequence[Item], interests: Interests, ctx: RunContext) -> list[Item]:
        return list(items)

    def rank_hints(self, interests: Interests, ctx: RunContext) -> RankHints:
        return RankHints(threshold=50, max_items=10)


class FakeOutput:
    """Output that records what it published; optionally raising or filtering."""

    google_scopes: list[str] = []  # noqa: RUF012

    def __init__(
        self,
        type_: str = "fake_out",
        fail: bool = False,
        accept: Callable[[RankedItem], bool] | None = None,
        feedback: list[Feedback] | None = None,
    ) -> None:
        self.type = type_
        self.fail = fail
        self.accept = accept or (lambda item: True)
        self.published: list[RankedItem] = []
        self.previews: list[str] = []
        self.feedback = feedback or []
        self.synced: list[Delivery] = []

    def accepts(self, item: RankedItem) -> bool:
        return self.accept(item)

    def publish(
        self, items: Sequence[RankedItem], cfg: OutputConfig, ctx: RunContext
    ) -> list[Delivery]:
        if self.fail:
            raise RuntimeError("output down")
        self.published.extend(items)
        return [Delivery(item_id=i.id, output_id=cfg.id, external_ref=i.id) for i in items]

    def sync_feedback(
        self, deliveries: Sequence[Delivery], cfg: OutputConfig, ctx: RunContext
    ) -> list[Feedback]:
        self.synced.extend(deliveries)
        return list(self.feedback)

    def preview(self, items: Sequence[RankedItem], cfg: OutputConfig, ctx: RunContext) -> str:
        text = ", ".join(i.title for i in items)
        self.previews.append(text)
        return text
