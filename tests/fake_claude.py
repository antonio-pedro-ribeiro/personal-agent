"""A fake ``claude`` executable for subprocess tests.

Usage: ``python fake_claude.py REPLY_JSON ENV_DUMP``. Writes the names (and the Claude Code token
value, if any) of the environment variables it received to ENV_DUMP, then prints a
``stream-json`` result whose ``structured_output`` is REPLY_JSON's content.
"""

import json
import os
import sys
from pathlib import Path

reply, env_dump = Path(sys.argv[1]), Path(sys.argv[2])
sys.stdin.read()
raw = reply.read_text(encoding="utf-8")
env_dump.write_text(
    json.dumps(
        {
            "names": sorted(os.environ),
            "token": os.environ.get("CLAUDE_CODE_OAUTH_TOKEN"),
            "project": os.environ.get("GOOGLE_CLOUD_PROJECT"),
        }
    ),
    encoding="utf-8",
)
result = {
    "type": "result",
    "subtype": "success",
    "status": "success",
    "is_error": False,
    "duration_ms": 1200,
    "total_cost_usd": 0.01,
    "usage": {"input_tokens": 10, "output_tokens": 5},
    "response": raw,
    "structured_output": json.loads(raw),
}
print(json.dumps({"type": "system", "subtype": "init"}))
print(json.dumps(result))
