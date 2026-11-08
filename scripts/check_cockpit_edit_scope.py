#!/usr/bin/env python3
"""PreToolUse hook for the 'Personal Agent' VS Code cockpit agent.

Mechanically enforces what docs/ARCHITECTURE.md §6 and docs/SPEC.md §6 describe as a written
convention: the cockpit may edit ``config/*.md`` and ``docs/`` (its own job), but changes to
``personal_agent/kernel/`` or ``personal_agent/plugins/`` code should go through a reviewed
coding session, not a casual chat instruction. See AGENTS.md's hard rule: "kernel must never
import from plugins" — a cockpit edit that drifts from its scope is the easiest way to violate
invariants no one reviewed.

This only restricts this one cockpit agent (it is wired via this agent's own frontmatter
`hooks.PreToolUse`); it has no effect on the main coding session.

Best-effort only: it inspects `edit`/`create`-style tool calls for a target file path. It does
NOT parse shell commands run via `execute` for redirection-based writes (`> file`, `Set-Content`,
etc.) — that is a known gap, documented rather than silently assumed to be covered. Keep this
script small and auditable per the hooks reference's own guidance.

Reads one JSON object from stdin (the exact schema can vary by host, so this looks for a few
common key names); writes a PreToolUse decision to stdout and exits 0 (allow/ask) or 2 (deny).
"""

from __future__ import annotations

import json
import sys
from pathlib import Path, PurePosixPath

# Edits are allowed only under these repo-root-relative prefixes (POSIX-style, case-insensitive).
ALLOWED_PREFIXES = ("config/", "docs/")
ALLOWED_FILES = {"readme.md"}

# Tool name fragments (case-insensitive substring match) that create or modify file contents.
WRITE_TOOL_HINTS = ("edit", "create", "write", "apply_patch", "str_replace")

# Common key names different hosts may use for the tool name / its file-path argument.
TOOL_NAME_KEYS = ("tool_name", "toolName", "tool", "name")
PATH_KEYS = ("path", "file_path", "filePath", "target_file", "targetFile", "file")


def _get(d: dict, keys: tuple[str, ...]) -> str | None:
    for k in keys:
        v = d.get(k)
        if isinstance(v, str) and v:
            return v
    return None


def _find_path(payload: dict) -> str | None:
    if path := _get(payload, PATH_KEYS):
        return path
    for key in ("parameters", "input", "arguments", "args"):
        nested = payload.get(key)
        if isinstance(nested, dict) and (path := _get(nested, PATH_KEYS)):
            return path
    return None


def _is_write_tool(tool_name: str) -> bool:
    name = tool_name.lower()
    return any(hint in name for hint in WRITE_TOOL_HINTS)


def _relative_posix(raw_path: str) -> str:
    """Best-effort repo-root-relative POSIX path, so Windows backslashes and absolute
    paths from either the repo root or elsewhere all compare the same way."""
    p = Path(raw_path)
    try:
        repo_root = Path(__file__).resolve().parents[1]
        if p.is_absolute():
            p = p.relative_to(repo_root)
    except ValueError:
        pass  # outside the repo entirely; fall through and judge on the raw tail
    return PurePosixPath(p.as_posix().lstrip("./")).as_posix().lower()


def _decision(allow: bool, reason: str) -> dict:
    return {
        "hookSpecificOutput": {
            "hookEventName": "PreToolUse",
            "permissionDecision": "allow" if allow else "deny",
            "permissionDecisionReason": reason,
        }
    }


def main() -> int:
    raw = sys.stdin.read()
    try:
        payload = json.loads(raw) if raw.strip() else {}
    except json.JSONDecodeError:
        # Can't parse the call: fail open rather than breaking an unrelated tool's contract.
        print(json.dumps(_decision(True, "hook: unparsable payload, allowing")))
        return 0

    tool_name = _get(payload, TOOL_NAME_KEYS) or ""
    if not _is_write_tool(tool_name):
        print(json.dumps(_decision(True, "not a file-write tool")))
        return 0

    target = _find_path(payload)
    if target is None:
        # A write-capable tool with no discoverable path: ask rather than silently allow or
        # silently block something it can't evaluate.
        print(
            json.dumps(
                _decision(
                    False,
                    "Personal Agent cockpit: could not determine the target file for a "
                    f"'{tool_name}' call; scoped to config/ and docs/ only. Denying to be safe.",
                )
            )
        )
        return 2

    rel = _relative_posix(target)
    if rel in ALLOWED_FILES or any(rel.startswith(prefix) for prefix in ALLOWED_PREFIXES):
        print(json.dumps(_decision(True, f"'{rel}' is within the cockpit's scope")))
        return 0

    print(
        json.dumps(
            _decision(
                False,
                f"Personal Agent cockpit is scoped to editing config/ and docs/ only "
                f"(docs/ARCHITECTURE.md §6); '{rel}' is outside that scope. Make this change "
                "in a reviewed coding session instead.",
            )
        )
    )
    return 2


if __name__ == "__main__":
    sys.exit(main())
