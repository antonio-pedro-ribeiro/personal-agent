"""``claude_code`` provider: the Claude Code CLI (``claude -p``) on a Claude Pro/Max plan.

Each call is one non-interactive CLI run in an empty temp folder with a clean environment (no
API key, so a subscription run never falls back to paid API use). User customizations are
switched off (``--safe-mode``, no MCP servers, no skills) so the context stays small, and the
only tools are web search and web fetch, when a search budget is given. The answer comes back
as ``structured_output`` (validated against the pydantic schema via ``--json-schema``) and the
search/fetch result URLs are read from the ``stream-json`` events.
"""

from __future__ import annotations

import json
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
    in_domains,
    json_events,
    json_schema,
    parse_json,
    run_subprocess,
    search_instructions,
    with_retry,
)

T = TypeVar("T", bound=BaseModel)

NAME = "Claude Code"
TOKEN_VAR = "CLAUDE_CODE_OAUTH_TOKEN"  # Claude Code's own variable for `claude setup-token`
BASE_FLAGS = [
    "-p",
    "--output-format",
    "stream-json",
    "--verbose",
    "--no-session-persistence",
    "--safe-mode",
    "--strict-mcp-config",
    "--disable-slash-commands",
]


class ClaudeCodeLLM:
    provider = "claude_code"
    billing = "plan"

    def __init__(
        self,
        command: list[str],
        model: str,
        effort: str = DEFAULT_EFFORT,
        oauth_token: str | None = None,
        runner: Runner = run_subprocess,
        timeout: float = DEFAULT_TIMEOUT_S,
    ) -> None:
        self.command = command
        self.model = model
        self.effort = effort
        self.oauth_token = oauth_token
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
        argv = self._argv(system, schema, web_search)
        if web_search:
            prompt += search_instructions(web_search)
        urls: set[str] = set()

        def call(text: str) -> dict[str, Any]:
            result, found = self._run(argv, text)
            urls.update(found)
            return result

        _, output = with_retry(call, lambda r: self._output(r, schema), prompt, NAME)
        if web_search and web_search.allowed_domains:
            urls = {u for u in urls if in_domains(u, web_search.allowed_domains)}
        return ParseResult(output=output, result_urls=urls)

    # --- helpers ------------------------------------------------------------------------

    def _argv(self, system: str, schema: type[BaseModel], ws: WebSearch | None) -> list[str]:
        argv = [*self.command, *BASE_FLAGS, "--json-schema", json.dumps(json_schema(schema))]
        argv += ["--system-prompt", system, "--model", self.model, "--effort", self.effort]
        if ws:
            tools = "WebSearch,WebFetch" if ws.fetch_uses else "WebSearch"
            turns = ws.max_uses + ws.fetch_uses + 2
            argv += ["--tools", tools, "--allowed-tools", tools, "--max-turns", str(turns)]
        else:
            argv += ["--tools", "", "--max-turns", "3"]
        return argv

    def _run(self, argv: list[str], prompt: str) -> tuple[dict[str, Any], set[str]]:
        extra = {TOKEN_VAR: self.oauth_token} if self.oauth_token else None
        cli = self.runner(argv, prompt, clean_env(extra), self.timeout)
        events = json_events(cli.stdout)
        urls, searches = _tool_results(events)
        result = next((e for e in reversed(events) if e.get("type") == "result"), None)
        if result is None:
            raise cli_failure(NAME, cli)
        self._record(result, searches)
        if result.get("is_error") or result.get("api_error_status"):
            raise cli_failure(NAME, cli, str(result.get("result") or ""))
        if result.get("stop_reason") == "refusal":
            raise LLMError(f"{NAME}: the model declined the request")
        if str(result.get("subtype", "")).startswith("error"):
            raise LLMError(f"{NAME}: {result.get('subtype')}")
        return result, urls

    def _record(self, result: dict[str, Any], searches: int) -> None:
        u = self.usage
        usage = result.get("usage") or {}
        u.calls += 1
        u.input_tokens += int(usage.get("input_tokens") or 0)
        u.output_tokens += int(usage.get("output_tokens") or 0)
        u.cache_read_tokens += int(usage.get("cache_read_input_tokens") or 0)
        u.cache_write_tokens += int(usage.get("cache_creation_input_tokens") or 0)
        u.web_searches += searches
        u.seconds += float(result.get("duration_ms") or 0) / 1000
        u.notional_usd += float(result.get("total_cost_usd") or 0)

    @staticmethod
    def _output(result: dict[str, Any], schema: type[T]) -> T:
        structured = result.get("structured_output")
        if structured is not None:
            return schema.model_validate(structured)
        return parse_json(str(result.get("result") or ""), schema)


def _tool_results(events: list[dict[str, Any]]) -> tuple[set[str], int]:
    """URLs the WebSearch/WebFetch tools returned, and the number of searches made."""
    urls: set[str] = set()
    searches = 0
    for event in events:
        if event.get("type") == "assistant":
            for block in (event.get("message") or {}).get("content") or []:
                if isinstance(block, dict) and block.get("type") == "tool_use":
                    searches += block.get("name") == "WebSearch"
        result = event.get("tool_use_result") if event.get("type") == "user" else None
        if not isinstance(result, dict):
            continue
        for r in result.get("results") or []:  # WebSearch
            for hit in (r.get("content") if isinstance(r, dict) else None) or []:
                if isinstance(hit, dict) and isinstance(hit.get("url"), str):
                    urls.add(hit["url"])
        if isinstance(result.get("url"), str) and result.get("code") == 200:  # WebFetch
            urls.add(result["url"])
    return urls, searches
