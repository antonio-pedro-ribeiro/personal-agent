"""LLM access for the kernel ``Ranker`` and ``Extractor``. Plugins never call this directly.

``PERSONAL_AGENT_LLM_PROVIDER`` picks the implementation (``claude_code`` by default, and
``gemini_api``, in their own modules; ``anthropic_api`` here). The model comes from
``PERSONAL_AGENT_MODEL`` (each provider has a default) and each provider reads only its own
``PERSONAL_AGENT_*`` credential, passed explicitly, so the generic ``ANTHROPIC_API_KEY`` /
``GEMINI_API_KEY`` / ``GOOGLE_API_KEY`` are never used.
"""

from __future__ import annotations

import os
import shutil
import time
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from typing import Any, Protocol, TypeVar

from pydantic import BaseModel

PROVIDER_ENV = "PERSONAL_AGENT_LLM_PROVIDER"
PROVIDERS = ("anthropic_api", "claude_code", "gemini_api", "gemini_cli")
DEFAULT_PROVIDER = "claude_code"  # Claude Pro/Max subscription: no per-run cost
API_KEY_ENV = "PERSONAL_AGENT_ANTHROPIC_API_KEY"
CLAUDE_CODE_TOKEN_ENV = "PERSONAL_AGENT_CLAUDE_CODE_OAUTH_TOKEN"
GEMINI_API_KEY_ENV = "PERSONAL_AGENT_GEMINI_API_KEY"
MODEL_ENV = "PERSONAL_AGENT_MODEL"
EFFORT_ENV = "PERSONAL_AGENT_EFFORT"
DEFAULT_MODEL = "claude-opus-5-5"
DEFAULT_CLAUDE_CODE_MODEL = "claude-sonnet-5-5"  # subscription limits are tighter on Opus
DEFAULT_GEMINI_MODEL = "gemini-3.8-flash"
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
    # Gemini paid tier (Nov 2026; Flash prices double on 2027-01-01, Argon's on 2026-12-01)
    "gemini-3.8-flash": (0.75, 3.75),
    "gemini-3.6-flash": (0.75, 3.75),
    "gemini-3.5-flash-lite": (0.30, 2.50),
    "gemini-3.1-flash-lite": (0.25, 1.50),
    "gemini-4-argon": (2.0, 10.0),
}
WEB_SEARCH_USD = 0.01  # per search
GEMINI_SEARCH_USD = 0.014  # per grounded search query, after 5,000 free per month

T = TypeVar("T", bound=BaseModel)


class LLMError(RuntimeError):
    pass


class LLMUnavailable(LLMError):
    """No key, CLI missing or logged out, quota or usage limit: skip the LLM for this run."""


@dataclass
class Usage:
    calls: int = 0
    input_tokens: int = 0
    output_tokens: int = 0
    cache_read_tokens: int = 0
    cache_write_tokens: int = 0
    web_searches: int = 0
    seconds: float = 0.0  # wall time spent in LLM calls
    notional_usd: float = 0.0  # subscription providers: what the calls would cost on the API

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
        search = GEMINI_SEARCH_USD if model.startswith("gemini") else WEB_SEARCH_USD
        return tokens / 1_000_000 + self.web_searches * search


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
    @property
    def provider(self) -> str: ...  # one of PROVIDERS

    @property
    def billing(self) -> str: ...  # "api" (pay per use) or "plan" (included in a subscription)

    @property
    def model(self) -> str: ...

    @property
    def usage(self) -> Usage: ...

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

    provider = "anthropic_api"
    billing = "api"

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
            started = time.monotonic()
            try:
                response = self.client.beta.messages.parse(messages=messages, **kwargs)
            except Exception as exc:
                status = getattr(exc, "status_code", None)
                if status in (401, 403) or "credit balance" in str(exc).lower():
                    raise LLMUnavailable(f"Claude API: {exc}") from exc
                raise
            finally:
                self.usage.seconds += time.monotonic() - started
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


class Guarded:
    """Wraps a provider: after one ``LLMUnavailable`` every later call fails fast."""

    def __init__(self, inner: LLM) -> None:
        self.inner = inner
        self.unavailable: str | None = None

    @property
    def provider(self) -> str:
        return self.inner.provider

    @property
    def billing(self) -> str:
        return self.inner.billing

    @property
    def model(self) -> str:
        return self.inner.model

    @property
    def usage(self) -> Usage:
        return self.inner.usage

    def parse(
        self,
        *,
        system: str,
        prompt: str,
        schema: type[T],
        web_search: WebSearch | None = None,
        max_tokens: int = 16000,
    ) -> ParseResult[T]:
        if self.unavailable:
            raise LLMUnavailable(self.unavailable)
        try:
            return self.inner.parse(
                system=system,
                prompt=prompt,
                schema=schema,
                web_search=web_search,
                max_tokens=max_tokens,
            )
        except LLMUnavailable as exc:
            self.unavailable = str(exc)
            raise


def _model(
    env: Mapping[str, str], prefixes: tuple[str, ...], default: str
) -> tuple[str, str | None]:
    """``PERSONAL_AGENT_MODEL`` when it names a model of this vendor, else the default."""
    value = env.get(MODEL_ENV, "").strip()
    if not value:
        return default, None
    if value.lower().startswith(prefixes):
        return value, None
    return default, f"{MODEL_ENV}={value} is not a model of this provider; using {default}"


def select(
    env: Mapping[str, str] | None = None,
    resolve: Callable[[str], str | None] | None = None,
) -> tuple[Guarded | None, list[str]]:
    """The configured provider (or None) plus warnings; None means keyword ranking and no
    web sources. ``resolve`` follows a redirect URL to its target (Gemini grounding links).
    """
    env = os.environ if env is None else env
    name = env.get(PROVIDER_ENV, "").strip().lower() or DEFAULT_PROVIDER
    effort = env.get(EFFORT_ENV, "").strip() or DEFAULT_EFFORT
    impl: LLM
    if name == "anthropic_api":
        key = env.get(API_KEY_ENV, "").strip()
        if not key:
            return None, [f"{API_KEY_ENV} not set"]
        model, note = _model(env, ("claude",), DEFAULT_MODEL)
        impl = AnthropicLLM(api_key=key, model=model, effort=effort)
    elif name == "claude_code":
        from personal_agent.kernel.services.llm_claude_code import ClaudeCodeLLM

        exe = shutil.which("claude")
        if exe is None:
            return None, ["Claude Code CLI (`claude`) not found on PATH"]
        prefixes = ("claude", "opus", "sonnet", "haiku", "fable")
        model, note = _model(env, prefixes, DEFAULT_CLAUDE_CODE_MODEL)
        token = env.get(CLAUDE_CODE_TOKEN_ENV, "").strip() or None
        impl = ClaudeCodeLLM(command=[exe], model=model, effort=effort, oauth_token=token)
    elif name == "gemini_api":
        key = env.get(GEMINI_API_KEY_ENV, "").strip()
        if not key:
            return None, [f"{GEMINI_API_KEY_ENV} not set"]
        from personal_agent.kernel.services.llm_gemini_api import GeminiApiLLM

        model, note = _model(env, ("gemini",), DEFAULT_GEMINI_MODEL)
        impl = GeminiApiLLM(api_key=key, model=model, effort=effort, resolve=resolve)
    elif name == "gemini_cli":
        return None, [
            "gemini_cli is deferred: Google no longer accepts Gemini CLI sign-ins from personal "
            "accounts; use gemini_api"
        ]
    else:
        return None, [f"unknown {PROVIDER_ENV} '{name}' (expected one of {', '.join(PROVIDERS)})"]
    return Guarded(impl), [note] if note else []


def from_env() -> Guarded | None:
    """The configured LLM, or None when its credentials or CLI are missing."""
    return select()[0]
