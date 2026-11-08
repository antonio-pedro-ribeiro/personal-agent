"""Ranking service. M1 ships the keyword ranker; the LLM ranker arrives in M2."""

from __future__ import annotations

import re
from collections.abc import Sequence
from typing import Protocol

from personal_agent.kernel.models import Interests, Item, RankedItem, RankHints

EXTRA_MATCH_BONUS = 5.0


class Ranker(Protocol):
    def rank(
        self,
        items: Sequence[Item],
        interests: Interests,
        hints: RankHints,
        feedback: Sequence[tuple[Item, str]],
    ) -> list[RankedItem]:
        """Score items 0-100, highest first. ``interests`` holds only the plugin's topics."""
        ...


def _pattern(term: str) -> re.Pattern[str]:
    return re.compile(rf"(?<!\w){re.escape(term.lower())}(?!\w)")


class KeywordRanker:
    """Deterministic fallback: score = best topic weight x 100 when a keyword matches,
    +5 per extra matched keyword, capped at 100."""

    def rank(
        self,
        items: Sequence[Item],
        interests: Interests,
        hints: RankHints,
        feedback: Sequence[tuple[Item, str]],
    ) -> list[RankedItem]:
        compiled = [
            (topic, [(kw, _pattern(kw)) for kw in _terms(topic.id, topic.keywords)])
            for topic in interests.topics
        ]
        ranked: list[RankedItem] = []
        for item in items:
            text = item.search_text()
            best_score, best_reason = 0.0, "No topic keywords matched"
            for topic, patterns in compiled:
                matched = [kw for kw, pat in patterns if pat.search(text)]
                if not matched:
                    continue
                score = min(100.0, topic.weight * 100 + EXTRA_MATCH_BONUS * (len(matched) - 1))
                if score > best_score:
                    best_score = score
                    best_reason = f"Matches topic '{topic.id}' ({', '.join(matched)})"
            ranked.append(
                RankedItem(**item.model_dump(), score=round(best_score, 1), reason=best_reason)
            )
        ranked.sort(key=_order)
        return ranked


def _terms(topic_id: str, keywords: Sequence[str]) -> list[str]:
    """Topic id plus keywords, de-duplicated case-insensitively, order kept."""
    unique: dict[str, str] = {}
    for term in [topic_id, *keywords]:
        unique.setdefault(term.lower(), term)
    return list(unique.values())


def _order(item: RankedItem) -> tuple[float, float, str]:
    """Highest score first; ties broken by earliest date, then title."""
    when = item.start or item.published_at
    return (-item.score, when.timestamp() if when else float("inf"), item.title)
