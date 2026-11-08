# Implementation Plan

Builds [`SPEC.md`](SPEC.md) in **five milestones**: events end-to-end first (M1–M4, with M3 adding
Claude-subscription and Gemini providers), then news and the remaining outputs (M5). Every milestone ends in something runnable, with tests passing on Ubuntu and Windows.

## Overview

| #     | Milestone                                                                                 | Result                                                                                                                                                 | Spec ACs            |
| ----- | ----------------------------------------------------------------------------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------ | ------------------- |
| M1 ✅ | **Foundation**: scaffold, kernel, digest output, events from iCal                   | `agent run --dry-run` shows filtered, keyword-ranked events from an `.ics` source, offline                                                         | 2, 3, 4*, 6, 10     |
| M2 ✅ | **Smart events in Calendar**: LLM, web sources, Google Calendar                     | Events from web search, web pages and iCal feeds, LLM-ranked, on the *Suggestions* calendar                                                          | 5, 7, 9, 12, 13, 14 |
| M3 ✅ | **Alternative LLM providers**: Claude subscription (now the default), Gemini API & CLI | `PERSONAL_AGENT_LLM_PROVIDER` = `claude_code`, `gemini_api` or `gemini_cli` runs ranking, extraction and web search without the Claude API key | 15, 16              |
| M4 ✅ | **Events MVP**: feedback loop + unattended runs                                     | Runs daily in the cloud (or Windows), learns from feedback, usable from Android alone                                                                  | 8, 11               |
| M5    | **News & more outputs**, then v1 release                                            | News in the digest; Obsidian, Google Tasks and email outputs; every AC tested; `v1.0.0`                                                              | 1, all              |

\* AC 4 is complete in M2, once busy slots come from Google free/busy.

✅ M2: done and verified live on 2026-11-08 (LLM ranking, `web_search`, a `web_page` source with
feed discovery, a Meetup iCal source, Google sign-in, free/busy, and a test event on the
*Suggestions* calendar). AllTera Tribe was dropped (no website found).

✅ M3: built on 2026-11-08. `claude_code` verified live (`agent run --dry-run` with no API key:
5 calls, 3 web searches, ~48 s, included in plan, ~$0.14 at API prices) and made the **default
provider**. `gemini_api` and `gemini_cli` built and contract-tested against SDK/CLI fakes;
`gemini_api` live check waits for a `PERSONAL_AGENT_GEMINI_API_KEY`, and `gemini_cli` live use
requires an eligible Google account or `PERSONAL_AGENT_GOOGLE_CLOUD_PROJECT` (Gemini CLI 0.36/0.67
rejects personal Google accounts without a Cloud project). Spike findings (recorded in
`tests/fixtures/llm/`):
- Claude Code 2.1: `-p --output-format stream-json --verbose --json-schema` gives the answer as
  `structured_output`; WebSearch results arrive as `tool_use_result.results[].content[].url`,
  WebFetch as `tool_use_result.{url, code}`. Without `--safe-mode --strict-mcp-config
  --disable-slash-commands --system-prompt …` one tiny call loaded ~260k tokens of user setup
  (plugins, MCP, skills); with them, ~1.3k. Logged out → `is_error: true`, `result: "Not logged
  in · Please run /login"`, exit code 1.
- Gemini API: default `gemini-3.8-flash` ($0.75 / $3.75 per M tokens until 2026-12-31, free
  tier); Gemini 3+ supports schema output with Google Search grounding and URL context in one
  request; grounding search $0.014 per query after 5,000 free a month.
- Gemini CLI 0.36 and 0.67: `IneligibleTierError` ("no longer supported for Gemini Code Assist
  for individuals… migrate to Antigravity").

✅ M4: built on 2026-11-08: calendar and digest feedback read-back, `agent rate`, feedback as
a delimited block in the ranking prompt (AC 8 tested end to end), `agent schedule
install|remove` (Task Scheduler XML / launchd / crontab, per-OS command generation tested),
`agent run --fail-on-error` and `--log-file`, the cloud workflow `.github/workflows/agent.yml`
(state branch with `agent.db` and `digests/`, run-log artifact) and actionlint in CI. **Open
before "done when":** publish the Google app, add the repo secrets, and confirm one scheduled
cloud run end to end from Android (AC 11), then the quality check below.

Sequence: M1 → M2 → M3 → M4 → M5. Inside M5, news and the extra outputs are
independent and can be built in either order.

## Before starting (owner: you)

| Needed by | Item                                                                                                                                                                                                                               |
| --------- | ---------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| M2        | `PERSONAL_AGENT_ANTHROPIC_API_KEY`                                                                                                                                                                                               |
| M2 ✅     | Initial event sources (now in `config/sources.md`)                                                                                                                                                                               |
| M2        | Home city, timezone, initial topics for `interests.md`                                                                                                                                                                           |
| M2 ✅     | Google Cloud project `personal-agent` with Calendar API enabled and a **Desktop app** OAuth client (done; app in **Testing**, so the sign-in expires every 7 days)                                                   |
| M4        | Publish the OAuth app (**In production**) so scheduled runs don't lose the sign-in weekly; needs a public home page and privacy-policy link on the Branding page                                                             |
| M3 ✅     | Claude Code signed in with your subscription (done; for cloud, `claude setup-token` in M4); optional: a Gemini API key from Google AI Studio for the `gemini_api` live check |
| M4        | Decide: cloud mode (private repo OK?) and/or Windows local mode                                                                                                                                                                    |
| M5        | Obsidian vault path and how it syncs (git or Obsidian Sync)                                                                                                                                                                        |

---

## M1 — Foundation

Goal: the whole pipeline works offline with a real content plugin and output, without any
LLM or Google dependency.

**Scaffold & CI**

- `pyproject.toml` (hatchling), package `personal_agent`, console script `agent`.
- Entry point groups `personal_agent.content` and `personal_agent.outputs`.
- Dev tools: pytest, ruff (lint + format), mypy (strict on `kernel/`), import-linter rule
  blocking `kernel → plugins` imports.
- `.gitattributes` (`*.md text eol=lf`), `.gitignore` (`*.db`, `.env`, `out/`, tokens).
- `.github/workflows/ci.yml`: `ubuntu-latest` × `windows-latest`, Python 3.12 + 3.13.

**Kernel core**

- `kernel/models.py`: pydantic `Item`, `RankedItem`, `RankHints`, `Delivery`, `Feedback`,
  `Profile`, `Topic`, `Source`, `OutputConfig`.
- `kernel/services/checklist.py`: parse checklist items, `key: value` attributes, sections;
  tolerate CRLF, frontmatter and free text; file:line warnings. Also renders the digest item format.
- `kernel/config.py`: load `interests.md`, `sources.md`, `outputs.md` from `PERSONAL_AGENT_CONFIG_DIR`.
- `kernel/plugin.py`: `ContentPlugin` / `OutputPlugin` protocols, entry-point registry.
- `kernel/services/store.py`: SQLite schema + migrations; location via `platformdirs`.
- `kernel/runner.py`: run loop with per-plugin/adapter/output isolation, router (`plugins`,
  `min_score`, `accepts`, not-yet-delivered), `--dry-run` → `preview()`.
- `kernel/services/ranker.py`: keyword-overlap ranker (the LLM comes in M2).
- CLI: `agent run`, `agent plugins`, `agent sources test <id>`.

**First plugins**

- `plugins/outputs/markdown_digest.py`: writes `digests/YYYY-MM-DD.md`; idempotent.
- `plugins/content/events/`: `collect`, `filter`, `rank_hints`; `adapters/ical.py`
  (icalendar + `zoneinfo`/`tzdata`, recurring events expanded for the 14-day window).
- Filters: next 14 days, distance (haversine; "unknown" when no coordinates), price, time
  windows in the profile timezone, checked dislikes. `Availability` is stubbed (no busy slots).
- `config/*.md` starter files (from spec §3).

**Tests:** parser fixtures (CRLF, frontmatter, bad and unchecked items); fake content/output
plugins for routing, idempotency and failure isolation; `.ics` fixtures (timezones, all-day,
recurring, DST boundary); a table-driven test per filter.

**Done when:** `pipx install .` works on Windows, CI is green on both OSes, and `agent run --dry-run` prints a digest of filtered events from a fixture iCal source.

## M2 — Smart events in Calendar

Goal: real event discovery from the web, LLM ranking, and suggestions in Google Calendar.

**Spike first:** fetch safejourney.pt and the AllTera Tribe site and record fixtures;
check for feeds, JavaScript-only rendering, login walls. If events render only with
JavaScript, decide: a Playwright fetch mode (new dependency, needs a spec change) or drop the
source in favour of `web_search` with `allowed_domains`.

**LLM services**

- `kernel/services/llm.py`: Anthropic client wrapper; model from `PERSONAL_AGENT_MODEL`; structured
  outputs via pydantic; refusal handling; token/cost log per run.
- `Ranker`: batched JSON scoring `[{id, score, reason, duplicate_of}]`, keyword fallback.
- `Extractor`: plugin extraction spec + untrusted content in delimited blocks → validated `Item`s.

**Generic web adapters**

- `kernel/adapters/web_page.py`: robots.txt, 1 req/s/host, 20 s timeout; RSS/Atom/iCal feed
  discovery; HTML → text; content-hash cache.
- `kernel/adapters/web_search.py`: Claude web search tool, `max_queries`, `allowed_domains`.
- Enable `web`, `safejourney`, `alltera-tribe` in `config/sources.md`.

**Google**

- `kernel/services/google_auth.py`: `agent auth google`; scopes = enabled outputs +
  `calendar.freebusy`; token in `keyring` locally, env/secret in cloud; re-auth on scope change.
- `kernel/services/availability.py`: free/busy on the primary calendar.
- `plugins/outputs/google_calendar.py`: create *Suggestions* calendar if missing; tentative
  events with reason, URL, `agentItemId` extended property; idempotent; `preview()`.
- CLI: `agent outputs test <id>`.

**Tests:** recorded HTML/search fixtures with a fake LLM client; feed discovery and
unchanged page → no LLM call (AC 13); prompt-injection fixture (AC 14); cost accounting (AC 9);
fake Google client for idempotency, namespace and scopes (ACs 5, 7, 12); busy-slot filter
(completes AC 4). One documented manual run against a real Google account.

**Done when:** a real run shows LLM-ranked events from all three sources on the *Suggestions*
calendar on your phone, with no overlap with busy slots.

## M3 — Alternative LLM providers: Claude subscription and Gemini ✅

> Built as planned, with `claude_code` made the default provider (see the M3 note at the top).

Goal: besides the pay-per-use Claude API (default, done in M2), run the agent's LLM work on

- a **Claude Pro/Max subscription** through Claude Code (`claude_code`),
- **Gemini by API key** through the Google Gen AI SDK (`gemini_api`),
- a **Google subscription or free quota** through Gemini CLI (`gemini_cli`).

**Spike first** (recorded fixtures for every finding):

- Claude Code and Gemini CLI: flags for non-interactive JSON output, tool allowlists (search and
  fetch only), model selection; how logged-out and usage-limit states are reported; whether
  `gemini_cli` can authenticate in GitHub Actions with a subscription.
- Gemini API: default model; whether JSON schema output and Google Search grounding work in the
  same request (else two calls: grounded search, then structured extraction); format of
  grounding source URLs; measured cost of one ranking call and one search query.

**Provider setting**

- `PERSONAL_AGENT_LLM_PROVIDER`: `anthropic_api` (default), `claude_code`, `gemini_api` or
  `gemini_cli`. `llm.from_env()` picks the implementation; Ranker, Extractor and plugins are
  unchanged (same `LLM` interface).
- `PERSONAL_AGENT_MODEL` per vendor (each provider has a default); `PERSONAL_AGENT_EFFORT` mapped
  to the vendor's reasoning control where available.

**Shared pieces** (`kernel/services/llm_common.py`)

- JSON-from-text parsing, pydantic validation and one retry with the validation error, for
  providers that can't enforce a schema.
- A subprocess runner for CLI providers: timeout, clean environment (no API keys), output
  capture, mapping of "not logged in" / "limit reached" to clear errors.
- Search-URL normalisation: resolve redirect URLs (e.g. Gemini grounding links) with HEAD
  requests before the Extractor's same-site check.
- Usage reporting: dollars for API providers, "calls + duration, included in plan" for CLIs.

**`ClaudeCodeLLM`** (`llm_claude_code.py`)

- `claude -p` with only web search and web fetch allowed, capped by `max_queries` /
  `fetch_pages`; result URLs read from the CLI's JSON/stream output.
- Credentials: local Claude Code login; cloud `PERSONAL_AGENT_CLAUDE_CODE_OAUTH_TOKEN` (from
  `claude setup-token`), passed to the subprocess as Claude Code's own token variable.

**`GeminiApiLLM`** (`llm_gemini_api.py`)

- `google-genai` client with `PERSONAL_AGENT_GEMINI_API_KEY` passed explicitly; JSON output with
  the pydantic schema; Google Search grounding (and URL context, for `fetch_pages`) for web
  search; source URLs from grounding metadata; token usage and cost per run.
- Safety blocks and empty candidates reported as clear errors (like Claude refusals).

**`GeminiCliLLM`** (`llm_gemini_cli.py`)

- `gemini -p` with only its Google Search and web fetch tools; JSON validated as above.
- Credentials: local Gemini CLI login with a Google account. Cloud mode only if the spike finds a
  supported way; otherwise documented as local-only (use `gemini_api` in the cloud).

**Failure handling:** missing credentials, logged-out CLI, quota or usage limit → a clear warning;
ranking falls back to keywords and web sources are skipped for that run.

**Docs:** USAGE.md "Choosing an LLM provider" (setup per provider, comparison, limits, data-use
and terms notes).

**Tests:** provider-contract tests run the same documents through each provider with faked
vendor responses (SDK fakes for `gemini_api`, a fake executable for the CLIs): valid output,
invalid then valid (retry), safety block / refusal, not logged in, limit reached, timeout;
provider selection from env; no API key in any CLI subprocess environment; Gemini redirect
URLs resolved before the link check.

**Done when:** for each of `claude_code`, `gemini_api` and `gemini_cli`, with no other provider's
credentials set, `agent run --dry-run` on the real config ranks events and the `web` source
finds dated events (ACs 15, 16).

## M4 — Events MVP: feedback + unattended runs ✅

> Built as planned (see the M4 note at the top); the live cloud check is pending.

Goal: the agent runs by itself every day and learns from what you keep or delete.

**Feedback loop**

- `google_calendar.sync_feedback`: deleted → `rejected`; still present after its end → `accepted`.
- `markdown_digest.sync_feedback`: ticked 👍/👎 in digests from the last 14 days.
- `agent rate <id> up|down`; ranker prompt includes the latest 20 feedback examples.

**Deployment**

- `.github/workflows/agent.yml`: daily `schedule` + `workflow_dispatch`; `concurrency` group;
  `agent.db` on the orphan `state` branch; commit `digests/`; secrets
  `PERSONAL_AGENT_CLAUDE_CODE_OAUTH_TOKEN` (default provider; the workflow installs Claude Code) or an API key (per provider),
  `PERSONAL_AGENT_GOOGLE_TOKEN_JSON`; run-log artifact (items, scores, cost).
- `kernel/platform.py` + `agent schedule install|remove`: Task Scheduler on Windows,
  cron/launchd elsewhere.
- README "Getting started" for cloud and Windows modes.

**Tests:** deleted fake calendar event → `rejected`; ticked fixture digest → `up`; feedback in
the ranking prompt (AC 8); actionlint in CI; scheduler command generation per OS.

**Done when:** a scheduled run (not a manual one) completes unattended and its results can
be seen and given feedback from Android alone (AC 11).

**Quality check (does not block M5):** after a few days of scheduled runs, review pick
quality, cost per run (AC 9 is likely tight with 5 web searches) and extraction errors, and
tune prompts, thresholds or `max_queries`.

## M5 — News, more outputs, v1 release

Goal: complete the spec.

**News plugin**

- `plugins/content/news/` + extraction spec; filter `published ≤ 48 h`.
- Adapters: `rss` (feedparser), `hackernews` (official Firebase API), `gdelt` (DOC 2.0 API);
  reuse generic `web_search` for news topics.
- Duplicate collapse: the Ranker's `duplicate_of` → canonical item with `also_at` links.
- Route news → `digest` in `outputs.md`.

**More outputs**

- `obsidian.py`: daily note with frontmatter and the shared checklist renderer; feedback from
  ticked boxes; cloud mode only with a git vault. Allow `PERSONAL_AGENT_CONFIG_DIR` inside the vault.
- `google_tasks.py`: *Agent* list, due date = event date, "Read: …" for news; completed/deleted
  → feedback. Off by default (`tasks` scope is broad).
- `email.py`: `gmail.send`, the digest rendered as HTML.

**Release**

- Map every acceptance criterion to a named test; fill the gaps.
- Security pass: secrets, untrusted-content handling, scopes.
- Docs: config reference, troubleshooting (OAuth, blocked sites, Actions delays). Tag `v1.0.0`.

**Tests:** feed/API fixtures; duplicate collapse; 48 h filter; temp-dir vault; fake Tasks and
Gmail clients; idempotency and namespace per output.

**Done when:** the digest has *Plan* and *Read* sections (AC 1), each output can be toggled in
`outputs.md`, and all 15 ACs pass.

## Risks

| Risk                                                                                 | Impact                          | Mitigation                                                                                                   |
| ------------------------------------------------------------------------------------ | ------------------------------- | ------------------------------------------------------------------------------------------------------------ |
| Safejourney / AllTera pages are JS-rendered, login-only or lack dates                | No events from them             | M2 spike first; feed discovery; fall back to web search with `allowed_domains`                             |
| LLM extraction errors (wrong dates)                                                  | Bad suggestions                 | Schema + date validation; source URL required; feedback loop; quality check after M4                         |
| Google OAuth token expiry (7 days while the app is in Testing)                       | Scheduled runs fail silently    | Publish the app before M4 scheduling; workflow fails loudly; digest notes auth errors                        |
| Web search cost creep                                                                | Budget overrun                  | `max_queries` cap; per-run cost log; quality check after M4                                                |
| M2 is the largest milestone                                                          | Slips                           | The spike and LLM services come first; Calendar work can start in parallel against fake clients              |
| GitHub Actions cron delays                                                           | Late digests                    | Acceptable for daily cadence; manual dispatch available                                                      |
| Subscription usage limits hit by a run, or shared with your own Claude/Gemini use    | Run degrades to keyword ranking | Small `max_queries`; clear warning; API providers remain available                                         |
| Claude Code or Gemini CLI output format changes between versions                     | Provider breaks after an update | Spike + recorded fixtures; validate JSON; fail with a clear message                                          |
| Providers differ in extraction quality or link behaviour (e.g. Gemini redirect URLs) | Uneven results per provider     | Shared contract tests; same validation for all; URL resolution; compare providers on the same recorded pages |
