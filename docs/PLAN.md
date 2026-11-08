# Implementation Plan

Builds [`SPEC.md`](SPEC.md) in milestones: **events end-to-end first, then news, then the
remaining outputs**. Every milestone ends in something runnable, with tests passing on
Ubuntu and Windows.

Size: **S** ≈ ½–1 day · **M** ≈ 2–3 days · **L** ≈ 4–5 days (one developer + coding agent).

## Overview

| # | Milestone | Size | Result | Spec ACs |
|---|---|---|---|---|
| M0 | Scaffold & CI | S | `agent plugins` runs; CI green on Ubuntu + Windows | 10 |
| M1 | Kernel core + digest output | L | `agent run --dry-run` prints an (empty) digest from real config files | 2, 3, 6 |
| M2 | Events plugin, iCal, filters | M | Events from an `.ics` source, filtered, ranked by keywords, in the digest | 4 |
| M3 | LLM services + web sources | L | `web`, `safejourney`, `alltera-tribe` produce events; LLM ranking | 1*, 9, 13, 14 |
| M4 | Google auth, free/busy, Calendar output | L | Suggestions appear in Google Calendar, never overlapping busy slots | 5, 7, 12 |
| M5 | Feedback loop | M | Deleted suggestions / ticked boxes change the next ranking | 8 |
| M6 | Deployment (cloud + Windows) | M | **Events MVP:** runs daily unattended; usable from Android | 11 |
| M7 | News plugin | M | News section in the digest from RSS, HN, GDELT, web search | 1 |
| M8 | More outputs: Obsidian, Tasks, email | L | All five outputs working and routable | 3, 7, 8 |
| M9 | Hardening & release | S | All 14 ACs have tests; docs complete; v1 tag | all |

\* AC 1 is fully met only after M7 (needs news sources).

Critical path: M0 → M1 → M2 → M3 → M4 → M5 → M6. M7 and M8 can run in parallel after M6.

## Before starting (owner: you)

| Needed by | Item |
|---|---|
| M3 | `ANTHROPIC_API_KEY` |
| M3 | AllTera Tribe official URL; confirm safejourney.pt is the right site |
| M3 | Home city, timezone, initial topics for `interests.md` |
| M4 | Google Cloud project with Calendar API enabled and an OAuth client of type **Desktop app**. Set the consent screen to **In production** (unverified is fine for personal use; you'll see a warning screen). In **Testing** status Google expires refresh tokens after 7 days, which would break scheduled runs. |
| M6 | Decide: cloud mode (private repo OK?) and/or Windows local mode |
| M8 | Obsidian vault path and how it syncs (git or Obsidian Sync) |

---

## M0 — Scaffold & CI (S)
**Tasks**
- `pyproject.toml` (hatchling), package `personal_agent`, console script `agent`.
- Entry point groups `personal_agent.content` and `personal_agent.outputs` (empty for now).
- Dev tools: pytest, ruff (lint + format), mypy (strict on `kernel/`).
- `.gitattributes` (`*.md text eol=lf`), `.gitignore` (`*.db`, `.env`, `out/`, tokens).
- `.github/workflows/ci.yml`: matrix `ubuntu-latest` × `windows-latest`, Python 3.12 + 3.13.
- CLI skeleton (`argparse`): `agent plugins` lists discovered plugins.

**Done when:** CI is green on both OSes, and `pipx install .` then `agent plugins` works on Windows.

## M1 — Kernel core + `markdown_digest` output (L)
**Tasks**
- `kernel/models.py`: pydantic `Item`, `RankedItem`, `RankHints`, `Delivery`, `Feedback`,
  `Profile`, `Topic`, `Source`, `OutputConfig`.
- `kernel/services/checklist.py`: parse checklist items, `key: value` attributes, sections;
  tolerate CRLF, frontmatter and free text; file:line warnings. Also renders the digest item format.
- `kernel/config.py`: load `interests.md`, `sources.md`, `outputs.md` from `AGENT_CONFIG_DIR`.
- `kernel/plugin.py`: `ContentPlugin` / `OutputPlugin` protocols, entry-point registry.
- `kernel/services/store.py`: SQLite schema + migrations (`items`, `rankings`, `deliveries`,
  `feedback`); location via `platformdirs`.
- `kernel/runner.py`: loop skeleton with per-plugin/adapter/output try-except isolation,
  router (`plugins`, `min_score`, `accepts`, not-yet-delivered), `--dry-run` → `preview()`.
- `kernel/services/ranker.py`: **keyword-overlap ranker only** (LLM comes in M3).
- `plugins/outputs/markdown_digest.py`: writes `digests/YYYY-MM-DD.md`; idempotent.
- `config/*.md` starter files (from spec §3).

**Tests:** parser fixtures (CRLF, frontmatter, bad items, unchecked items); a fake content
plugin + fake output prove routing, idempotency and failure isolation; an import-linter rule
blocks `kernel → plugins` imports.

**Done when:** `agent run --dry-run` prints a digest preview using the fake plugin; ACs 2, 3, 6
have passing tests.

## M2 — Events plugin, iCal adapter, filters (M)
**Tasks**
- `plugins/content/events/plugin.py`: `collect`, `filter`, `rank_hints`, extraction spec
  (used in M3).
- `adapters/ical.py` (icalendar + `zoneinfo`/`tzdata`), with recurring events expanded for the
  14-day window.
- Filters: next 14 days; distance (haversine, skip if no coordinates and mark "unknown");
  price; time windows in the profile timezone; checked dislikes. Busy slots are stubbed
  (`Availability` returns none) until M4.
- `agent sources test <id>`.

**Tests:** recorded `.ics` fixtures (timezones, all-day, recurring, DST boundary); a table-driven
test per filter.

**Done when:** a fixture iCal source yields filtered, keyword-ranked events in the digest (AC 4,
except busy slots).

## M3 — LLM services + web sources (L)
**Tasks**
- `kernel/services/llm.py`: Anthropic client wrapper. Model from `AGENT_MODEL` env; structured
  outputs with pydantic schemas; refusal handling; token/cost logging per run.
- `Ranker`: batched JSON scoring `[{id, score, reason, duplicate_of}]`, last 20 feedback
  examples, keyword ranker as fallback.
- `Extractor`: plugin extraction spec + untrusted content in delimited blocks → validated
  `Item`s (URL required, valid dates).
- `kernel/adapters/web_page.py`: polite fetch (robots.txt, 1 req/s/host, timeout 20 s),
  RSS/Atom/iCal **feed discovery**, HTML → text, content-hash cache in `store`.
- `kernel/adapters/web_search.py`: Claude web search server tool, `max_queries`,
  `allowed_domains`, one query per topic.
- Enable `web`, `safejourney`, `alltera-tribe` in `config/sources.md`.
- **Spike first (½ day):** fetch both sites and record fixtures; check for feeds, JS-only
  rendering, and login walls. If a site renders events only with JavaScript, log it as a risk
  and decide: a Playwright fetch mode (adds a dependency, needs a spec change) or drop the source.

**Tests:** recorded HTML/search fixtures with a fake LLM client; feed-discovery fixture (no LLM
call); unchanged page → no LLM call (AC 13); prompt-injection fixture (AC 14); a cost-accounting
test against AC 9's budget.

**Done when:** a dry run with real network and API key shows events from all three sources with
LLM reasons.

## M4 — Google auth, free/busy, `google_calendar` output (L)
**Tasks**
- `kernel/services/google_auth.py`: `agent auth google` (installed-app flow), scopes = union of
  enabled outputs + `calendar.freebusy`; refresh token in `keyring` locally, env/secret in
  cloud; re-auth prompt when scopes change.
- `kernel/services/availability.py`: free/busy query on the primary calendar, with a cache per run.
- `plugins/outputs/google_calendar.py`: create the *Suggestions* calendar if missing (store its
  id); insert tentative events with the reason, URL and `agent-id` in the description;
  idempotent via `deliveries` + the private extended property `agentItemId`; `preview()`.
- `agent outputs test <id>`.

**Tests:** fake Google client for idempotency, namespace and scopes (ACs 5, 7, 12); busy-slot
filter test (completes AC 4). One manual end-to-end run against a real account (documented
checklist).

**Done when:** suggestions appear in Google Calendar on the phone and never overlap busy slots.

## M5 — Feedback loop (M)
**Tasks**
- `google_calendar.sync_feedback`: suggestion deleted → `rejected`; still present after its
  end time → `accepted`.
- `markdown_digest.sync_feedback`: ticked 👍/👎 in digests from the last 14 days.
- `agent rate <id> up|down`.
- Ranker prompt includes the latest 20 feedback examples; latest signal wins.

**Tests:** fake calendar deletion → `rejected`; ticked fixture digest → `up`; feedback appears
in the ranking prompt (AC 8).

**Done when:** deleting a suggestion changes the next run's prompt and scores.

## M6 — Deployment: cloud + Windows (M) → **Events MVP**
**Tasks**
- `.github/workflows/agent.yml`: daily `schedule` + `workflow_dispatch`; `concurrency` group;
  restore and push `agent.db` on the orphan `state` branch; commit `digests/`; secrets
  `ANTHROPIC_API_KEY`, `GOOGLE_TOKEN_JSON`.
- `kernel/platform.py` + `agent schedule install|remove`: Task Scheduler (`schtasks`) on
  Windows, cron/launchd elsewhere.
- Run log artifact per workflow run (items collected, scores, cost).
- README "Getting started" for both modes.

**Tests:** a workflow lint check (actionlint) in CI; scheduler command generation unit-tested
per OS.

**Done when:** one week of unattended daily runs, used from Android only (AC 11). **Pause here
for a review** of pick quality, cost and false positives before building news.

---

## M7 — News plugin (M)
**Tasks**
- `plugins/content/news/plugin.py` + extraction spec; filter `published ≤ 48 h`.
- Adapters: `rss` (feedparser), `hackernews` (official Firebase API, top + best stories),
  `gdelt` (DOC 2.0 API, keyword search).
- Reuse the generic `web_search` for news topics.
- Duplicate collapse: the Ranker's `duplicate_of` → canonical item with `also_at` links.
- Route news → `digest` in `outputs.md`.

**Tests:** recorded feed/API fixtures; duplicate-collapse fixture; the 48 h filter.

**Done when:** the digest has *Plan* and *Read* sections (AC 1 fully met).

## M8 — More outputs: Obsidian, Google Tasks, email (L)
**Tasks**
- `obsidian.py`: daily note in `<vault>/<folder>/` with frontmatter, shared checklist renderer,
  feedback via ticked boxes; cloud mode only with a git vault (clone with a scoped token, push).
- `google_tasks.py`: *Agent* list, due date = event date, "Read: …" for news; completed/deleted
  → feedback. Off by default (`tasks` scope is broad).
- `email.py`: `gmail.send`, the digest rendered as HTML.
- Allow `AGENT_CONFIG_DIR` inside the vault.

**Tests:** fake clients for Tasks/Gmail; temp-dir vault for Obsidian; idempotency and namespace
tests per output.

**Done when:** each output can be enabled or disabled independently via `outputs.md` and passes
ACs 3, 7, 8.

## M9 — Hardening & release (S)
- Map every acceptance criterion to a named test; add the missing ones.
- Run the cost check over a week of logs (AC 9).
- Security pass: secrets handling, untrusted-content handling, scopes.
- Docs: README, config reference, troubleshooting (OAuth, blocked sites, Actions delays).
- Tag `v1.0.0`.

## Risks

| Risk | Impact | Mitigation |
|---|---|---|
| Safejourney / AllTera pages are JS-rendered, login-only or lack dates | No events from them | M3 spike first; feed discovery; fall back to web search with `allowed_domains` |
| LLM extraction errors (wrong dates) | Bad suggestions | Schema + date validation; source URL required; feedback loop; manual review at M6 |
| Google OAuth token expiry | Scheduled runs fail silently | Consent screen "In production"; workflow fails loudly and the digest notes auth errors |
| Web search cost creep | Budget overrun | `max_queries` cap; per-run cost log; AC 9 test |
| GitHub Actions cron delays | Late digests | Acceptable for daily cadence; manual dispatch available |
