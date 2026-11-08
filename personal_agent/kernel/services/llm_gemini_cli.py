"""``gemini_cli`` provider: the Gemini CLI (``gemini -p``) with a Google sign-in.

Each call is one non-interactive CLI run in an empty temp folder with a clean environment (no
API key, so a subscription or free-tier CLI run never falls back to paid API use). Extensions
and MCP servers are switched off, and the only tools allowed are ``google_web_search`` (and
``web_fetch`` when pages may be opened) when a search budget is given. Output is validated
against the pydantic schema with one retry on invalid JSON, and Gemini grounding redirect URLs
are resolved before the Extractor's link check.
"""

from __future__ import annotations

import json
import re
from collections.abc import Callable
from pathlib import Path
from typing import Any, TypeVar

from pydantic import BaseModel

from personal_agent.kernel.services.llm import (
    DEFAULT_EFFORT,
    LLMError,
    ParseResult,
    Usage,
    WebSearch,
)
from personal_agent.kernel.services.llm_common import (
    DEFAULT_TIMEOUT_S,
    Runner,
    clean_env,
    cli_failure,
    finish_search,
    json_events,
    json_schema,
    parse_json,
    run_subprocess,
    search_instructions,
    with_retry,
)

T = TypeVar("T", bound=BaseModel)

NAME = "Gemini CLI"
PROJECT_VAR = "GOOGLE_CLOUD_PROJECT"
SEARCH_TOOL = "google_web_search"
FETCH_TOOL = "web_fetch"
BASE_FLAGS = [
    "-p",
    "",
    "--output-format",
    "stream-json",
    "--extensions",
    "",
    "--allowed-mcp-server-names",
    "",
]
BLOCKED = {"SAFETY", "PROHIBITED_CONTENT", "BLOCKLIST", "SPII", "RECITATION", "IMAGE_SAFETY"}
_URL_RE = re.compile(r"https?://[^\s)>\]\"',]+")


class GeminiCliLLM:
    provider = "gemini_cli"
    billing = "plan"

    def __init__(
        self,
        command: list[str],
        model: str,
        effort: str = DEFAULT_EFFORT,
        project: str | None = None,
        resolve: Callable[[str], str | None] | None = None,
        runner: Runner = run_subprocess,
        timeout: float = DEFAULT_TIMEOUT_S,
    ) -> None:
        self.command = command
        self.model = model
        self.effort = effort
        self.project = project
        self.resolve = resolve
        self.runner = runner
        self.timeout = timeout
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
        argv = self._argv(web_search)
        full_prompt = self._prompt(system, prompt, schema, web_search)
        urls: set[str] = set()

        def call(text: str) -> str:
            reply, found = self._run(argv, text)
            urls.update(found)
            return reply

        _, output = with_retry(call, lambda r: parse_json(r, schema), full_prompt, NAME)
        return finish_search(output, schema, urls, web_search, self.resolve)

    # --- helpers ------------------------------------------------------------------------

    def _argv(self, ws: WebSearch | None) -> list[str]:
        argv = [*self.command, *BASE_FLAGS, "--model", self.model]
        if ws:
            tools = f"{SEARCH_TOOL},{FETCH_TOOL}" if ws.fetch_uses else SEARCH_TOOL
            argv += ["--allowed-tools", tools]
        else:
            argv += ["--allowed-tools", ""]
        return argv

    @staticmethod
    def _prompt(system: str, prompt: str, schema: type[BaseModel], ws: WebSearch | None) -> str:
        body = f"{system.strip()}\n\n{prompt.strip()}"
        if ws:
            body += search_instructions(ws)
        else:
            body += "\n\nDo not call any tools."
        body += (
            "\n\nReply with only a JSON object matching this JSON Schema "
            f"(no markdown fences, no commentary):\n{json.dumps(json_schema(schema))}"
        )
        return body

    def _run(self, argv: list[str], prompt: str) -> tuple[str, set[str]]:
        extra = {"GEMINI_CLI_TRUST_WORKSPACE": "true", "NO_BROWSER": "1"}
        if self.project:
            extra[PROJECT_VAR] = self.project
        cli = self.runner(argv, prompt, clean_env(extra), self.timeout)
        events = json_events(cli.stdout)
        urls, searches = _tool_results(events)
        result = next(
            (
                e
                for e in reversed(events)
                if e.get("type") == "result" or "response" in e or "error" in e
            ),
            None,
        )
        if result is None:
            raise cli_failure(NAME, cli)
        self._record(result, searches)
        if error := _error_message(result):
            if _is_blocked(result, error):
                raise LLMError(f"{NAME}: response blocked ({error})")
            raise cli_failure(NAME, cli, error)
        finish = str(result.get("finish_reason") or result.get("stop_reason") or "").upper()
        if finish in BLOCKED or finish == "REFUSAL":
            raise LLMError(f"{NAME}: response blocked ({finish.lower()})")
        return _assistant_text(events, result), urls

    def _record(self, result: dict[str, Any], searches: int) -> None:
        stats = result.get("stats") or result.get("usage") or {}
        u = self.usage
        u.calls += 1
        cached = int(stats.get("cached") or stats.get("cache_read_input_tokens") or 0)
        prompt_tokens = int(stats.get("input_tokens") or 0)
        inp = int(stats.get("input") or max(0, prompt_tokens - cached))
        out = int(stats.get("output_tokens") or 0)
        if not (inp or out) and isinstance(stats.get("models"), dict):
            for m in stats["models"].values():
                tokens = (m.get("tokens") if isinstance(m, dict) else None) or m or {}
                if isinstance(tokens, dict):
                    c = int(tokens.get("cached") or 0)
                    p = int(tokens.get("prompt") or tokens.get("input_tokens") or 0)
                    inp += int(tokens.get("input") or max(0, p - c))
                    cached += c
                    out += int(tokens.get("candidates") or tokens.get("output_tokens") or 0)
        u.input_tokens += inp
        u.cache_read_tokens += cached
        u.output_tokens += out
        u.web_searches += searches
        u.seconds += float(stats.get("duration_ms") or result.get("duration_ms") or 0) / 1000
        if (cost := u.cost_usd(self.model)) is not None:
            u.notional_usd = cost


def _assistant_text(events: list[dict[str, Any]], result: dict[str, Any]) -> str:
    chunks = [
        str(e.get("content") or "")
        for e in events
        if e.get("type") == "message" and e.get("role") == "assistant"
    ]
    if chunks:
        return "".join(chunks)
    return str(result.get("response") or result.get("result") or "")


def _error_message(result: dict[str, Any]) -> str:
    err = result.get("error")
    if isinstance(err, dict):
        return str(err.get("message") or err.get("type") or "error")
    if err:
        return str(err)
    if result.get("status") == "error" or result.get("is_error"):
        return str(result.get("message") or result.get("result") or "error")
    return ""


def _is_blocked(result: dict[str, Any], error: str) -> bool:
    finish = str(result.get("finish_reason") or result.get("stop_reason") or "").upper()
    if finish in BLOCKED or finish == "REFUSAL":
        return True
    low = error.lower()
    return any(b.lower() in low for b in BLOCKED) or "blocked" in low or "declined" in low


def _urls_in_text(text: str) -> set[str]:
    return {m.group(0).rstrip(".,;:!)") for m in _URL_RE.finditer(text)}


def _tool_results(events: list[dict[str, Any]]) -> tuple[set[str], int]:
    """URLs the ``google_web_search`` / ``web_fetch`` tools returned, and search count."""
    urls: set[str] = set()
    searches = 0
    fetch_calls: dict[str, set[str]] = {}
    session_id = ""
    for event in events:
        kind = event.get("type")
        if kind == "init" and isinstance(event.get("session_id"), str):
            session_id = event["session_id"]
        elif kind == "tool_use":
            name = event.get("tool_name") or event.get("name")
            if name == SEARCH_TOOL:
                searches += 1
            elif name == FETCH_TOOL:
                params = event.get("parameters") or event.get("input") or {}
                call_id = str(event.get("tool_id") or event.get("id") or "")
                if isinstance(params, dict) and call_id:
                    text = f"{params.get('url') or ''} {params.get('prompt') or ''}"
                    fetch_calls[call_id] = _urls_in_text(text)
        elif kind == "tool_result":
            if event.get("status") == "error" or event.get("error"):
                continue
            output = str(event.get("output") or "")
            failed = output.startswith(("Error:", "Failed to fetch"))
            call_id = str(event.get("tool_id") or "")
            if not failed and call_id in fetch_calls:
                urls.update(fetch_calls[call_id])
            if not failed and output:
                urls.update(_urls_in_text(output))
            for src in event.get("sources") or []:
                if isinstance(src, str):
                    urls.add(src)
                elif isinstance(src, dict):
                    web = src.get("web") if isinstance(src.get("web"), dict) else src
                    if isinstance(web, dict) and isinstance(web.get("uri") or web.get("url"), str):
                        urls.add(str(web.get("uri") or web.get("url")))
            for u in event.get("urls") or []:
                if isinstance(u, str):
                    urls.add(u)
    if searches and not urls and len(session_id) >= 8:
        urls.update(_session_urls(session_id[:8]))
    return urls, searches


def _session_urls(short_session_id: str) -> set[str]:
    """Fallback for Gemini CLI builds where ``stream-json`` omits search URLs from ``tool_result``
    and writes them only to ``~/.gemini/tmp/*/chats/session-*-<id>.json``."""
    tmp = Path.home() / ".gemini" / "tmp"
    try:
        matches = list(tmp.glob(f"*/chats/session-*-{short_session_id}.json"))
    except OSError:
        return set()
    urls: set[str] = set()
    for path in matches:
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        for msg in (data.get("messages") if isinstance(data, dict) else None) or []:
            for call in (msg.get("toolCalls") if isinstance(msg, dict) else None) or []:
                if isinstance(call, dict) and call.get("status") == "success":
                    urls.update(_urls_in_text(json.dumps(call.get("result") or "")))
    return urls
