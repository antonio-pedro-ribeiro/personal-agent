# Implementation Plan

Builds [`SPEC.md`](SPEC.md) in **four milestones**: events end-to-end first (M1–M3), then news
and the remaining outputs (M4). Every milestone ends in something runnable, with tests passing
on Ubuntu and Windows.

## Overview

| # | Milestone | Result | Spec ACs |
|---|---|---|---|
| M1 | **Foundation**: scaffold, kernel, digest output, events from iCal | `agent run --dry-run` shows filtered, keyword-ranked events from an `.ics` source, offline | 2, 3, 4*, 6, 10 |
| M2 | **Smart events in Calendar**: LLM, web sources, Google Calendar | Events from web search, Safejourney and AllTera Tribe, LLM-ranked, on the *Suggestions* calendar | 5, 7, 9, 12, 13, 14 |
| M3 | **Events MVP**: feedback loop + unattended runs | Runs daily in the cloud (or Windows), learns from feedback, usable from Android alone | 8, 11 |
| M4 | **News & more outputs**, then v1 release | News in the digest; Obsidian, Google Tasks and email outputs; every AC tested; `v1.0.0` | 1, all |

\* AC 4 is complete in M2, once busy slots come from Google free/busy.

Sequence: M1 → M2 → M3 → M4. Inside M4, news and the extra outputs are
independent and can be built in either order.

## Before starting (owner: you)

| Needed by | Item |
|---|---|
| M2 | `ANTHROPIC_API_KEY` |
| M2 | AllTera Tribe official URL; confirm safejourney.pt is the right site |
| M2 | Home city, timezone, initial topics for `interests.md` |
| M2 | Google Cloud project with Calendar API enabled and an OAuth client of type **Desktop app**. Set the consent screen to **In production** (unverified is fine for personal use; you'll see a warning screen). In **Testing** status Google expires refresh tokens after 7 days, which would break scheduled runs. |
| M3 | Decide: cloud mode (private repo OK?) and/or Windows local mode |
| M4 | Obsidian vault path and how it syncs (git or Obsidian Sync) |

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
- `kernel/config.py`: load `interests.md`, `sources.md`, `outputs.md` from `AGENT_CONFIG_DIR`.
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

**Done when:** `pipx install .` works on Windows, CI is green on both OSes, and `agent run
--dry-run` prints a digest of filtered events from a fixture iCal source.

## M2 — Smart events in Calendar
Goal: real event discovery from the web, LLM ranking, and suggestions in Google Calendar.

**Spike first:** fetch safejourney.pt and the AllTera Tribe site and record fixtures;
check for feeds, JavaScript-only rendering, login walls. If events render only with
JavaScript, decide: a Playwright fetch mode (new dependency, needs a spec change) or drop the
source in favour of `web_search` with `allowed_domains`.

**LLM services**
- `kernel/services/llm.py`: Anthropic client wrapper; model from `AGENT_MODEL`; structured
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

## M3 — Events MVP: feedback + unattended runs
Goal: the agent runs by itself every day and learns from what you keep or delete.

**Feedback loop**
- `google_calendar.sync_feedback`: deleted → `rejected`; still present after its end → `accepted`.
- `markdown_digest.sync_feedback`: ticked 👍/👎 in digests from the last 14 days.
- `agent rate <id> up|down`; ranker prompt includes the latest 20 feedback examples.

**Deployment**
- `.github/workflows/agent.yml`: daily `schedule` + `workflow_dispatch`; `concurrency` group;
  `agent.db` on the orphan `state` branch; commit `digests/`; secrets `ANTHROPIC_API_KEY`,
  `GOOGLE_TOKEN_JSON`; run-log artifact (items, scores, cost).
- `kernel/platform.py` + `agent schedule install|remove`: Task Scheduler on Windows,
  cron/launchd elsewhere.
- README "Getting started" for cloud and Windows modes.

**Tests:** deleted fake calendar event → `rejected`; ticked fixture digest → `up`; feedback in
the ranking prompt (AC 8); actionlint in CI; scheduler command generation per OS.

**Done when:** a scheduled run (not a manual one) completes unattended and its results can
be seen and given feedback from Android alone (AC 11).

**Quality check (does not block M4):** after a few days of scheduled runs, review pick
quality, cost per run (AC 9 is likely tight with 5 web searches) and extraction errors, and
tune prompts, thresholds or `max_queries`.

## M4 — News, more outputs, v1 release
Goal: complete the spec.

**News plugin**
- `plugins/content/news/` + extraction spec; filter `published ≤ 48 h`.
- Adapters: `rss` (feedparser), `hackernews` (official Firebase API), `gdelt` (DOC 2.0 API);
  reuse generic `web_search` for news topics.
- Duplicate collapse: the Ranker's `duplicate_of` → canonical item with `also_at` links.
- Route news → `digest` in `outputs.md`.

**More outputs**
- `obsidian.py`: daily note with frontmatter and the shared checklist renderer; feedback from
  ticked boxes; cloud mode only with a git vault. Allow `AGENT_CONFIG_DIR` inside the vault.
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
`outputs.md`, and all 14 ACs pass.

## Risks

| Risk | Impact | Mitigation |
|---|---|---|
| Safejourney / AllTera pages are JS-rendered, login-only or lack dates | No events from them | M2 spike first; feed discovery; fall back to web search with `allowed_domains` |
| LLM extraction errors (wrong dates) | Bad suggestions | Schema + date validation; source URL required; feedback loop; quality check after M3 |
| Google OAuth token expiry | Scheduled runs fail silently | Consent screen "In production"; workflow fails loudly; digest notes auth errors |
| Web search cost creep | Budget overrun | `max_queries` cap; per-run cost log; quality check after M3 |
| M2 is the largest milestone | Slips | The spike and LLM services come first; Calendar work can start in parallel against fake clients |
| GitHub Actions cron delays | Late digests | Acceptable for daily cadence; manual dispatch available |
