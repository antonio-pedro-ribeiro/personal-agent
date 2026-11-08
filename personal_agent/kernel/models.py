"""Data models shared by the kernel and all plugins."""

from __future__ import annotations

import hashlib
from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

Signal = Literal["up", "down", "accepted", "rejected"]

SHORT_ID_LEN = 10


def make_item_id(*parts: str) -> str:
    """Stable item id: sha1 of the plugin-chosen key parts (e.g. URL, or UID + start)."""
    return hashlib.sha1("|".join(parts).encode("utf-8")).hexdigest()


class Item(BaseModel):
    """A candidate found by a content plugin."""

    model_config = ConfigDict(frozen=True)

    id: str
    plugin: str
    title: str
    url: str
    source_id: str
    summary: str = ""
    published_at: datetime | None = None
    start: datetime | None = None
    end: datetime | None = None
    location: str | None = None
    extra: dict[str, Any] = Field(default_factory=dict)

    @property
    def short_id(self) -> str:
        return self.id[:SHORT_ID_LEN]

    def search_text(self) -> str:
        """Lower-cased text used by keyword matching and dislike filtering."""
        return " ".join(filter(None, [self.title, self.summary, self.location or ""])).lower()


class RankedItem(Item):
    score: float
    reason: str
    also_at: list[str] = Field(default_factory=list)


class RankHints(BaseModel):
    """Provided by a content plugin to steer ranking."""

    rubric: str = ""
    threshold: float = 50.0
    max_items: int = 10


class Delivery(BaseModel):
    item_id: str
    output_id: str
    external_ref: str | None = None


class OwnedEntry(BaseModel):
    """Something an output created in its own namespace (a calendar event, a task...)."""

    item_id: str
    external_ref: str
    title: str = ""
    when: str = ""  # display only, as the output stores it


class Feedback(BaseModel):
    item_id: str
    output_id: str
    signal: Signal
    at: datetime


# --- Configuration -----------------------------------------------------------------------


class Profile(BaseModel):
    city: str | None = None
    coordinates: tuple[float, float] | None = None
    timezone: str = "UTC"
    max_km: float | None = None
    max_price: float | None = None
    time_windows: list[str] = Field(default_factory=list)
    max_events_per_week: int = 3
    max_news_items: int = 10
    extra: dict[str, str] = Field(default_factory=dict)


class Topic(BaseModel):
    id: str
    description: str = ""
    enabled: bool = True
    keywords: list[str] = Field(default_factory=list)
    weight: float = 0.5
    plugins: list[str] | None = None  # None = all content plugins

    def applies_to(self, plugin: str) -> bool:
        return self.plugins is None or plugin in self.plugins


class Dislike(BaseModel):
    text: str
    enabled: bool = True


class Interests(BaseModel):
    profile: Profile = Field(default_factory=Profile)
    topics: list[Topic] = Field(default_factory=list)
    dislikes: list[Dislike] = Field(default_factory=list)

    def for_plugin(self, plugin: str) -> Interests:
        """Only enabled topics routed to ``plugin`` and enabled dislikes."""
        return Interests(
            profile=self.profile,
            topics=[t for t in self.topics if t.enabled and t.applies_to(plugin)],
            dislikes=[d for d in self.dislikes if d.enabled],
        )


class Source(BaseModel):
    id: str
    plugin: str
    type: str
    enabled: bool = True
    attrs: dict[str, str] = Field(default_factory=dict)
    origin: str = ""  # "file:line" for messages


class OutputConfig(BaseModel):
    id: str
    type: str
    enabled: bool = True
    plugins: list[str] | None = None  # None = all content plugins
    min_score: float = 0.0
    attrs: dict[str, str] = Field(default_factory=dict)
    origin: str = ""

    def routes(self, plugin: str) -> bool:
        return self.plugins is None or plugin in self.plugins


class Config(BaseModel):
    interests: Interests = Field(default_factory=Interests)
    sources: list[Source] = Field(default_factory=list)
    outputs: list[OutputConfig] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)
