"""Pieces shared by the LLM providers: JSON-from-text parsing with one retry, a clean-environment
subprocess runner for CLI providers, error classification, and search-URL normalisation.

Nothing outside ``kernel/services/llm*.py`` imports this module.
"""

from __future__ import annotations

import json
import os
import re
import subprocess
import tempfile
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from typing import Any
from urllib.parse import urlparse

from pydantic import BaseModel, ValidationError

from personal_agent.kernel.services.llm import LLMError, LLMUnavailable, WebSearch

# Environment variables a CLI subprocess may inherit: what the OS and the CLI need to find
# their home, config and temp folders, and nothing else. API keys and tokens never pass.
_ENV_ALLOW = {
    "PATH", "PATHEXT", "SYSTEMROOT", "SYSTEMDRIVE", "WINDIR", "COMSPEC", "TEMP", "TMP", "TMPDIR",
    "USERPROFILE", "HOMEDRIVE", "HOMEPATH", "HOME", "APPDATA", "LOCALAPPDATA", "PROGRAMDATA",
    "PROGRAMFILES", "PROGRAMFILES(X86)", "PROGRAMW6432", "USERNAME", "USER", "LOGNAME", "SHELL",
    "LANG", "LC_ALL", "LC_CTYPE", "TZ", "OS", "PROCESSOR_ARCHITECTURE", "NUMBER_OF_PROCESSORS",
    "COMPUTERNAME", "XDG_CONFIG_HOME", "XDG_DATA_HOME", "XDG_CACHE_HOME", "XDG_STATE_HOME",
    "XDG_RUNTIME_DIR", "SSL_CERT_FILE", "SSL_CERT_DIR", "HTTPS_PROXY", "HTTP_PROXY", "NO_PROXY",
}  # fmt: skip
_SECRET_HINTS = ("API_KEY", "APIKEY", "TOKEN", "SECRET", "PASSWORD", "CREDENTIAL")

# Text that means "this provider can't be used for the rest of the run" (not a one-off failure).
_UNAVAILABLE_PATTERNS = (
    r"not logged in",
    r"please run /login",
    r"invalid api key",
    r"authentication",
    r"unauthori[sz]ed",
    r"oauth token",
    r"usage limit",
    r"limit reached",
    r"rate limit",
    r"quota",
    r"credit balance",
    r"ineligible ?tier",
    r"no longer supported",
)

REDIRECT_HOSTS = {"vertexaisearch.cloud.google.com"}  # Gemini grounding links
DEFAULT_TIMEOUT_S = 300.0

RETRY_NOTE = (
    "\n\nYour previous reply was not valid JSON for the required schema ({error}). "
    "Reply again with only the JSON object."
)


def unavailable_reason(text: str) -> str | None:
    """A short reason when ``text`` reports a logged-out CLI, a bad key or a reached limit."""
    low = text.lower()
    for pattern in _UNAVAILABLE_PATTERNS:
        if re.search(pattern, low):
            line = next((ln.strip() for ln in text.splitlines() if ln.strip()), text.strip())
            return line[:200]
    return None


# --- JSON -------------------------------------------------------------------------------


def json_schema(schema: type[BaseModel]) -> dict[str, Any]:
    return schema.model_json_schema()


def parse_json[M: BaseModel](text: str, schema: type[M]) -> M:
    """The first JSON object in ``text`` (code fences and chatter allowed), validated."""
    body = text.strip()
    fence = re.search(r"```(?:json)?\s*(.*?)```", body, re.DOTALL)
    if fence:
        body = fence.group(1).strip()
    start = body.find("{")
    if start < 0:
        raise ValueError("no JSON object in the reply")
    obj, _ = json.JSONDecoder().raw_decode(body[start:])
    return schema.model_validate(obj)


def short_error(exc: Exception) -> str:
    return " ".join(str(exc).split())[:300]


def with_retry[R](
    call: Callable[[str], R], check: Callable[[R], Any], prompt: str, name: str
) -> tuple[R, Any]:
    """``call(prompt)`` then ``check(reply)``; if the reply isn't valid JSON for the schema
    (``ValueError``/``ValidationError``), one more call with the error appended."""
    error = ""
    for attempt in range(2):
        reply = call(prompt + (RETRY_NOTE.format(error=error) if attempt else ""))
        try:
            return reply, check(reply)
        except (ValueError, ValidationError) as exc:
            error = short_error(exc)
    raise LLMError(f"{name}: invalid JSON after one retry ({error})")


# --- CLI subprocesses -------------------------------------------------------------------


@dataclass
class CliResult:
    returncode: int
    stdout: str
    stderr: str


Runner = Callable[[list[str], str, dict[str, str], float], CliResult]


def clean_env(extra: dict[str, str] | None = None) -> dict[str, str]:
    """A minimal environment: the OS basics plus ``extra``; never an API key or token."""
    env = {
        k: v
        for k, v in os.environ.items()
        if k.upper() in _ENV_ALLOW and not any(h in k.upper() for h in _SECRET_HINTS)
    }
    env.update(extra or {})
    return env


def run_subprocess(argv: list[str], stdin: str, env: dict[str, str], timeout: float) -> CliResult:
    """Run a CLI in an empty temp folder (so it never picks up project files)."""
    name = os.path.basename(argv[0])
    with tempfile.TemporaryDirectory(prefix="personal-agent-") as cwd:
        try:
            proc = subprocess.run(
                argv,
                input=stdin,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                env=env,
                cwd=cwd,
                timeout=timeout,
                check=False,
            )
        except FileNotFoundError as exc:
            raise LLMUnavailable(f"{name} not found") from exc
        except subprocess.TimeoutExpired as exc:
            raise LLMError(f"{name} timed out after {timeout:.0f}s") from exc
    return CliResult(proc.returncode, proc.stdout or "", proc.stderr or "")


def cli_failure(name: str, result: CliResult, message: str = "") -> LLMError:
    """The error for a failed CLI call: ``LLMUnavailable`` when logged out or over a limit."""
    text = "\n".join(t for t in (message, result.stderr, result.stdout) if t.strip())
    if reason := unavailable_reason(text):
        return LLMUnavailable(f"{name}: {reason}")
    tail = " ".join(text.split())[-300:] or f"exit code {result.returncode}"
    return LLMError(f"{name} failed: {tail}")


# --- web search -------------------------------------------------------------------------


def search_instructions(ws: WebSearch) -> str:
    """Budget and scope for providers whose search tools can't be capped by parameters."""
    lines = [f"\n\nUse at most {ws.max_uses} web search(es)."]
    if ws.fetch_uses:
        lines.append(f"Open at most {ws.fetch_uses} result page(s).")
    else:
        lines.append("Do not open result pages.")
    if ws.allowed_domains:
        lines.append(f"Only use results from: {', '.join(ws.allowed_domains)}.")
    if ws.city or ws.timezone:
        where = ", ".join(x for x in (ws.city, ws.timezone) if x)
        lines.append(f"The person is near: {where}.")
    lines.append("For each item, give the URL of the original page, not a redirect link.")
    return " ".join(lines)


def in_domains(url: str, domains: Iterable[str]) -> bool:
    host = urlparse(url).netloc.lower().split(":")[0]
    wanted = [d.lower().lstrip(".") for d in domains]
    return not wanted or any(host == d or host.endswith("." + d) for d in wanted)


def resolve_redirects(
    urls: Iterable[str], resolve: Callable[[str], str | None] | None
) -> dict[str, str | None]:
    """Map each redirect-service URL to its target (None when it can't be resolved)."""
    mapping: dict[str, str | None] = {}
    for url in urls:
        if urlparse(url).netloc.lower() not in REDIRECT_HOSTS:
            continue
        target: str | None = None
        if resolve is not None:
            try:
                target = resolve(url)
            except Exception:
                target = None
        if target and urlparse(target).netloc.lower() in REDIRECT_HOSTS:
            target = None
        mapping[url] = target
    return mapping


def rewrite_strings(obj: Any, mapping: dict[str, str | None]) -> Any:
    """Replace redirect URLs inside model output with their targets."""
    if isinstance(obj, dict):
        return {k: rewrite_strings(v, mapping) for k, v in obj.items()}
    if isinstance(obj, list):
        return [rewrite_strings(v, mapping) for v in obj]
    if isinstance(obj, str) and obj in mapping:
        return mapping[obj] or obj
    return obj
