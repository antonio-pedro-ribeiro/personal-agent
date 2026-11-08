"""``gemini_api`` provider: Gemini through the Google Gen AI SDK (``google-genai``).

The key comes only from ``PERSONAL_AGENT_GEMINI_API_KEY`` and is passed to the client
explicitly. JSON output uses the pydantic schema; web search uses Google Search grounding (and
URL context when pages may be fetched) in the same request, which Gemini 3+ models support.
Grounding links point at a Google redirect service; they are resolved to the source pages
before the Extractor's same-site check, and redirect links in the answer are rewritten.
"""

from __future__ import annotations

import time
from collections.abc import Callable
from typing import Any, TypeVar

from pydantic import BaseModel

from personal_agent.kernel.services.llm import (
    DEFAULT_EFFORT,
    LLMError,
    LLMUnavailable,
    ParseResult,
    Usage,
    WebSearch,
)
from personal_agent.kernel.services.llm_common import (
    finish_search,
    json_schema,
    parse_json,
    search_instructions,
    with_retry,
)

T = TypeVar("T", bound=BaseModel)

NAME = "Gemini API"
THINKING_LEVEL = {"minimal": "minimal", "low": "low", "medium": "medium"}  # anything else: high
BLOCKED = {"SAFETY", "PROHIBITED_CONTENT", "BLOCKLIST", "SPII", "RECITATION", "IMAGE_SAFETY"}


class GeminiApiLLM:
    provider = "gemini_api"
    billing = "api"

    def __init__(
        self,
        api_key: str,
        model: str,
        effort: str = DEFAULT_EFFORT,
        client: Any = None,
        resolve: Callable[[str], str | None] | None = None,
    ) -> None:
        if client is None:
            from google import genai  # imported lazily: tests and other providers never need it

            client = genai.Client(api_key=api_key)
        self.client = client
        self.model = model
        self.effort = effort
        self.resolve = resolve
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
        config = self._config(system, schema, web_search, max_tokens)
        if web_search:
            prompt += search_instructions(web_search)
        urls: set[str] = set()

        def call(text: str) -> str:
            reply, found = self._call(text, config)
            urls.update(found)
            return reply

        _, output = with_retry(call, lambda r: parse_json(r, schema), prompt, NAME)
        return finish_search(output, schema, urls, web_search, self.resolve)

    # --- helpers ------------------------------------------------------------------------

    def _config(
        self, system: str, schema: type[BaseModel], ws: WebSearch | None, max_tokens: int
    ) -> Any:
        from google.genai import types

        tools: list[Any] = []
        if ws:
            tools.append(types.Tool(google_search=types.GoogleSearch()))
            if ws.fetch_uses:
                tools.append(types.Tool(url_context=types.UrlContext()))
        return types.GenerateContentConfig(
            system_instruction=system,
            response_mime_type="application/json",
            response_json_schema=json_schema(schema),
            max_output_tokens=max_tokens,
            thinking_config=types.ThinkingConfig(
                thinking_level=THINKING_LEVEL.get(self.effort, "high")  # type: ignore[arg-type]
            ),
            tools=tools or None,
        )

    def _call(self, prompt: str, config: Any) -> tuple[str, set[str]]:
        started = time.monotonic()
        try:
            response = self.client.models.generate_content(
                model=self.model, contents=prompt, config=config
            )
        except Exception as exc:
            code = getattr(exc, "code", None)
            if code in (401, 403) or "api key" in str(exc).lower():
                raise LLMUnavailable(f"{NAME}: key rejected ({exc})") from exc
            if code == 429:
                raise LLMUnavailable(f"{NAME}: quota or rate limit reached ({exc})") from exc
            raise
        finally:
            self.usage.seconds += time.monotonic() - started
        self._record(response)

        feedback = getattr(response, "prompt_feedback", None)
        if feedback is not None and getattr(feedback, "block_reason", None):
            raise LLMError(f"{NAME}: prompt blocked ({_name(feedback.block_reason)})")
        candidates = getattr(response, "candidates", None) or []
        if not candidates:
            raise LLMError(f"{NAME}: empty response (no candidates)")
        candidate = candidates[0]
        finish = _name(getattr(candidate, "finish_reason", None))
        if finish in BLOCKED:
            raise LLMError(f"{NAME}: response blocked ({finish})")
        if finish == "MAX_TOKENS":
            raise LLMError(f"{NAME}: response truncated (max_tokens)")
        return _text(candidate), _grounding_urls(candidate)

    def _record(self, response: Any) -> None:
        meta = getattr(response, "usage_metadata", None)
        u = self.usage
        u.calls += 1
        if meta is not None:
            cached = getattr(meta, "cached_content_token_count", 0) or 0
            prompt = getattr(meta, "prompt_token_count", 0) or 0
            tool = getattr(meta, "tool_use_prompt_token_count", 0) or 0
            u.input_tokens += max(0, prompt - cached) + tool
            u.cache_read_tokens += cached
            u.output_tokens += (getattr(meta, "candidates_token_count", 0) or 0) + (
                getattr(meta, "thoughts_token_count", 0) or 0
            )
        for candidate in (getattr(response, "candidates", None) or [])[:1]:
            grounding = getattr(candidate, "grounding_metadata", None)
            u.web_searches += len(getattr(grounding, "web_search_queries", None) or [])


def _name(value: Any) -> str:
    return str(getattr(value, "name", value) or "")


def _text(candidate: Any) -> str:
    content = getattr(candidate, "content", None)
    parts = getattr(content, "parts", None) or []
    return "".join(
        p.text for p in parts if getattr(p, "text", None) and not getattr(p, "thought", False)
    )


def _grounding_urls(candidate: Any) -> set[str]:
    urls: set[str] = set()
    grounding = getattr(candidate, "grounding_metadata", None)
    for chunk in getattr(grounding, "grounding_chunks", None) or []:
        web = getattr(chunk, "web", None)
        if uri := getattr(web, "uri", None):
            urls.add(str(uri))
    url_context = getattr(candidate, "url_context_metadata", None)
    for meta in getattr(url_context, "url_metadata", None) or []:
        status = _name(getattr(meta, "url_retrieval_status", None))
        if (url := getattr(meta, "retrieved_url", None)) and "SUCCESS" in status:
            urls.add(str(url))
    return urls
