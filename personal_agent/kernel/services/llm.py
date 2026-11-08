"""LLM access for the kernel ``Ranker`` and ``Extractor``. Plugins never call this directly.

The model comes from ``PERSONAL_AGENT_MODEL`` and the key from
``PERSONAL_AGENT_ANTHROPIC_API_KEY``; the key is passed to the SDK explicitly, so the generic
``ANTHROPIC_API_KEY`` is never used.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from typing import Any, Protocol, TypeVar

from pydantic import BaseModel

API_KEY_ENV = "PERSONAL_AGENT_ANTHROPIC_API_KEY"
MODEL_ENV = "PERSONAL_AGENT_MODEL"
EFFORT_ENV = "PERSONAL_AGENT_EFFORT"
DEFAULT_MODEL = "claude-opus-5-5"
DEFAULT_EFFORT = "low"
FALLBACK_BETA = "server-side-fallback-2026-07-01"
WEB_SEARCH_TOOL = "web_search_20260209"
WEB_FETCH_TOOL = "web_fetch_20260209"
MAX_PAUSE_CONTINUATIONS = 3

# USD per million tokens (input, output). Cache reads ~0.1x input, cache writes ~1.25x input.
PRICES: dict[str, tuple[float, float]] = {
    "claude-opus-5-5": (4.0, 20.0),
    "claude-sonnet-5-5": (2.0, 10.0),
    "claude-haiku-5-5": (0.10, 0.50),
    "claude-opus-5": (5.0, 25.0),
    "claude-sonnet-5": (2.0, 10.0),
    "claude-fable-5-1": (10.0, 50.0),
}
WEB_SEARCH_USD = 0.01  # per search

T = TypeVar("T", bound=BaseModel)


class LLMError(RuntimeError):
    pass


@dataclass
class Usage:
    calls: int = 0
    input_tokens: int = 0
    output_tokens: int = 0
    cache_read_tokens: int = 0
    cache_write_tokens: int = 0
    web_searches: int = 0

    def add(self, usage: Any) -> None:
        self.calls += 1
        self.input_tokens += getattr(usage, "input_tokens", 0) or 0
        self.output_tokens += getattr(usage, "output_tokens", 0) or 0
        self.cache_read_tokens += getattr(usage, "cache_read_input_tokens", 0) or 0
        self.cache_write_tokens += getattr(usage, "cache_creation_input_tokens", 0) or 0
        server = getattr(usage, "server_tool_use", None)
        self.web_searches += getattr(server, "web_search_requests", 0) or 0

    def cost_usd(self, model: str) -> float | None:
        if model not in PRICES:
            return None
        inp, out = PRICES[model]
        tokens = (
            self.input_tokens * inp
            + self.output_tokens * out
            + self.cache_read_tokens * inp * 0.1
            + self.cache_write_tokens * inp * 1.25
        )
        return tokens / 1_000_000 + self.web_searches * WEB_SEARCH_USD


@dataclass
class WebSearch:
    max_uses: int = 2
    fetch_uses: int = 0  # pages the model may open from the results (web fetch tool)
    allowed_domains: list[str] = field(default_factory=list)
    city: str | None = None
    timezone: str | None = None


@dataclass
class ParseResult[M: BaseModel]:
    output: M
    result_urls: set[str] = field(default_factory=set)  # URLs returned by web search


class LLM(Protocol):
    model: str
    usage: Usage

    def parse(
        self,
        *,
        system: str,
        prompt: str,
        schema: type[T],
        web_search: WebSearch | None = None,
        max_tokens: int = 16000,
    ) -> ParseResult[T]: ...


def _search_tool(ws: WebSearch) -> dict[str, Any]:
    tool: dict[str, Any] = {"type": WEB_SEARCH_TOOL, "name": "web_search", "max_uses": ws.max_uses}
    if ws.allowed_domains:
        tool["allowed_domains"] = ws.allowed_domains
    if ws.city or ws.timezone:
        location: dict[str, str] = {"type": "approximate"}
        if ws.city:
            location["city"] = ws.city
        if ws.timezone:
            location["timezone"] = ws.timezone
        tool["user_location"] = location
    return tool


def _result_urls(content: Any) -> set[str]:
    urls: set[str] = set()
    for block in content or []:
        kind = getattr(block, "type", None)
        results = getattr(block, "content", None)
        if kind == "web_search_tool_result" and isinstance(results, list):
            # an error result is a single object, not a list
            urls.update(r.url for r in results if getattr(r, "url", None))
        elif kind == "web_fetch_tool_result" and (url := getattr(results, "url", None)):
            urls.add(str(url))
    return urls


class AnthropicLLM:
    """Structured-output calls with adaptive thinking, low effort and server-side fallbacks."""

    def __init__(self, api_key: str, model: str, effort: str = DEFAULT_EFFORT) -> None:
        import anthropic  # imported lazily: tests and keyless runs never need it

        self.client = anthropic.Anthropic(api_key=api_key)
        self.model = model
        self.effort = effort
        self.usage = Usage()

    def parse(
        self,
        *,
        system: str,
        prompt: str,
        schema: type[T],
        web_search: WebSearch | None = None,
        max_tokens: int = 16000,
    ) -> ParseResult[T]:
        messages: list[Any] = [{"role": "user", "content": prompt}]
        kwargs: dict[str, Any] = {
            "model": self.model,
            "max_tokens": max_tokens,
            "system": system,
            "output_format": schema,
            "output_config": {"effort": self.effort},
            "betas": [FALLBACK_BETA],
            "fallbacks": "default",
        }
        if web_search:
            kwargs["tools"] = [_search_tool(web_search)]
            if web_search.fetch_uses:
                kwargs["tools"].append(
                    {"type": WEB_FETCH_TOOL, "name": "web_fetch", "max_uses": web_search.fetch_uses}
                )

        urls: set[str] = set()
        for _ in range(MAX_PAUSE_CONTINUATIONS + 1):
            response = self.client.beta.messages.parse(messages=messages, **kwargs)
            self.usage.add(response.usage)
            urls |= _result_urls(response.content)
            if response.stop_reason != "pause_turn":
                break
            messages.append({"role": "assistant", "content": response.content})
        if response.stop_reason == "refusal":
            raise LLMError("the model declined the request")
        if response.stop_reason == "max_tokens":
            raise LLMError("response truncated (max_tokens)")
        parsed = response.parsed_output
        if parsed is None:
            raise LLMError("no structured output in response")
        return ParseResult(output=parsed, result_urls=urls)


def from_env() -> AnthropicLLM | None:
    """The configured LLM, or None when no key is set (keyword ranking, no web sources)."""
    key = os.environ.get(API_KEY_ENV, "").strip()
    if not key:
        return None
    model = os.environ.get(MODEL_ENV, "").strip() or DEFAULT_MODEL
    effort = os.environ.get(EFFORT_ENV, "").strip() or DEFAULT_EFFORT
    return AnthropicLLM(api_key=key, model=model, effort=effort)
