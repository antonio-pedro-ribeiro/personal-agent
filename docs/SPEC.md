# Personal Agent — Spec v1

A personal agent that finds **events** and **news** matching my interests, delivers a
digest, and suggests events into Google Calendar.

## 1. Architecture: microkernel

A small **kernel** that knows nothing about events or news, plus **plugins** that do.

```
            config/interests.md        config/sources.md
                     │                        │
                     ▼                        ▼
┌──────────────────────────── KERNEL ─────────────────────────────┐
│  Config (markdown checklists) · Plugin registry · Run loop      │
│  Services: Store (SQLite) · Ranker (LLM) · Calendar · Digest    │
│            HTTP · Clock · Log                                    │
└──────────▲───────────────────────────────▲──────────────────────┘
           │ Plugin API                    │ Plugin API
   ┌───────┴────────┐              ┌───────┴────────┐
   │ events plugin  │              │  news plugin   │   … future plugins
   │ adapters:      │              │ adapters:      │   (podcasts, papers,
   │  ical,         │              │  rss,          │    deals, …)
   │  ticketmaster  │              │  hackernews,   │
   │                │              │  gdelt         │
   └────────────────┘              └────────────────┘
```

### Kernel responsibilities
- Parse `interests.md` and `sources.md` into typed objects.
- Discover plugins (Python entry point group `personal_agent.plugins`), register them, and
  give each one only its own sources.
- Run the loop: `sync_feedback → collect → dedupe → filter → rank → deliver`.
- Provide shared services; plugins never open DB connections, call the LLM, or touch
  Google APIs directly.
- Isolate failures: an exception in a plugin or adapter is logged and skipped.

### Plugin API
```python
class Plugin(Protocol):
    name: str                                  # matches a "## <Name>" section in sources.md
    def collect(self, sources, profile, ctx) -> list[Item]: ...
    def filter(self, items, profile, ctx) -> list[Item]: ...       # deterministic, no LLM
    def rank_hints(self, profile) -> RankHints: ...                # rubric, threshold, max items
    def deliver(self, ranked, profile, ctx) -> DigestSection: ...  # side effects + digest text
    def sync_feedback(self, ctx) -> None: ...                      # optional
```
A plugin owns its **source adapters** (`type` field in `sources.md`) and its domain rules.
Adding a new kind of content = adding a plugin; the kernel does not change.

### Built-in plugins
| Plugin | Adapters | Filter | Deliver | Feedback |
|---|---|---|---|---|
| `events` | `ical`, `ticketmaster` | next 14 days, distance, price, time windows, Google `freeBusy` | tentative entries on **Suggestions** calendar + *Plan* section | kept = accepted, deleted = rejected |
| `news` | `rss`, `hackernews`, `gdelt` | published ≤ 48 h | *Read* section, duplicates collapsed | 👍/👎 boxes in digest, or `agent rate` |

**Not in v1:** watches (news → event tracking), embedding clustering, chat bot, web UI,
auto-editing interests.

### Running on Windows and Android
**Principle:** the agent is a headless scheduled job. Windows PCs and Android phones are
*thin clients*: they use apps you already have (Google Calendar, Gmail, a browser). No device
has to stay on, and there is no native app in v1.

**Deployment modes**
| Mode | Runs on | Scheduler | State | Status |
|---|---|---|---|---|
| **Cloud** (default) | GitHub Actions in this (private) repo | workflow `schedule:` + manual "Run workflow" | `agent.db` committed to an orphan `state` branch | v1 |
| **Windows local** | Python via `pipx` | Task Scheduler, via `agent schedule install` | `%LOCALAPPDATA%\personal-agent\` | v1 |
| Linux/macOS local | Python via `pipx` | cron / launchd, via `agent schedule install` | platform data dir | v1 (dev) |
| Android local (Termux) | — | — | — | **Unsupported.** Doze and app standby defer or kill background jobs, so scheduled runs are unreliable. |

Cloud-mode caveats: GitHub cron can start minutes to an hour late at busy times; a private
repo gets 2,000 free Actions minutes/month (a run should take ~2 min). A `concurrency:` group
prevents overlapping runs.

**What you do from each device**
| Task | Windows | Android |
|---|---|---|
| Edit `interests.md` / `sources.md` | any editor + git, or github.com | github.com in the browser, or a git-synced markdown editor |
| See event suggestions | Google Calendar (web) | Google Calendar app (*Suggestions* calendar) |
| Accept / reject an event | keep / delete it | keep / delete it |
| Read the digest | Gmail, or `digests/` on github.com | Gmail app, or github.com |
| Rate news | tick boxes in the digest file, or `agent rate` | tick boxes in the digest file on github.com |
| Run on demand | `agent run`, or "Run workflow" | "Run workflow" on github.com |

**Cross-platform requirements**
- Pure-Python or wheel-available dependencies only; no POSIX-only calls (`fcntl`, `os.fork`,
  shell scripts).
- Paths via `pathlib`; config/state/cache locations via `platformdirs`.
- All file I/O uses `encoding="utf-8"` explicitly (Windows otherwise defaults to a legacy code
  page). The markdown parser accepts CRLF; `.gitattributes` sets `*.md text eol=lf`.
- Timezones via `zoneinfo` + the `tzdata` package (Windows has no IANA database).
- Secrets: environment variables, or the OS keyring (`keyring` → Windows Credential Manager)
  locally; GitHub Actions secrets in cloud mode. Never in config files.
- Google OAuth: `agent auth google` runs the browser consent flow once on a desktop and
  stores the refresh token (keyring locally; paste into a repo secret for cloud mode).
- **Least-privilege scopes:** `calendar.app.created` (manage only calendars the agent created,
  i.e. *Suggestions*), `calendar.freebusy` (read busy slots, not event contents), and
  `gmail.send` if the digest is emailed. The rule "never write to the main calendar" is then
  enforced by Google, not just by our code.
- CI runs the test suite on `ubuntu-latest` and `windows-latest`.

## 2. User stories

### Interests
- **US-1** As a user, I edit `config/interests.md` — a checklist — to define topics; checking
  or unchecking a box turns a topic on or off without deleting it.
- **US-2** As a user, I set constraints (city, distance, price, time windows, max events per
  week) in the same document.
- **US-3** As a user, I keep a checklist of dislikes; checked ones exclude matching items.

### Sources
- **US-4** As a user, I manage `config/sources.md` — one section per plugin, one checkbox per
  source — independently of interests.
- **US-5** As a user, I run `agent sources test <id>` to see what a source returns.

### Plugins
- **US-6** As a user, I see which plugins are installed and enabled (`agent plugins`).
- **US-7** As a developer, I add a new content type by writing a plugin package with an entry
  point, without modifying the kernel.

### Events
- **US-8** As a user, I get up to N suggested events per week that fit my free time, each with
  a one-line reason it matches.
- **US-9** As a user, suggestions appear on a separate Suggestions calendar, never on my
  main calendar.
- **US-10** As a user, I accept a suggestion by keeping it and reject it by deleting it (from any
  device's Calendar app).

### News
- **US-11** As a user, I get a daily digest of the top ≤ 10 news items for my interests, each
  with a short summary and source link.
- **US-12** As a user, the same story from multiple outlets appears once.
- **US-13** As a user, I rate news by ticking 👍/👎 boxes in the digest file (works from a
  phone browser) or with `agent rate <id> up|down`.

### Learning & ops
- **US-14** As a user, my past ratings influence future selections.
- **US-15** As a user, the agent runs on a schedule (news daily, events weekly).
- **US-16** As a user, `agent run --dry-run` shows the digest without calendar writes or email.

### Platforms
- **US-17** As a user, the agent runs in the cloud on schedule without any of my devices on.
- **US-18** As an Android user, I can see, accept and reject suggestions, read the digest, rate
  news, and edit my interests and sources using only Google Calendar, Gmail and a browser.
- **US-19** As a Windows user, I install with `pipx install` and schedule runs with one
  command (`agent schedule install`).
- **US-20** As a user, I trust that the agent cannot modify my main calendar because it never
  holds a permission that would allow it.

## 3. Configuration documents

Both files are plain markdown, readable and editable in any editor or on GitHub.

**Format rules**
- `- [x]` = enabled, `- [ ]` = disabled.
- Item line: `- [x] **<id>** — <description or type>`.
- Indented `- key: value` bullets under an item are its attributes; comma-separated values
  become lists.
- `## Heading` sections give context: in `sources.md` the heading is the plugin name.
- Anything else (paragraphs, notes) is ignored, so the files can hold free-form comments.
- Parse errors name the file and line; an invalid item is skipped, not fatal.

### `config/interests.md`
```markdown
# Interests

## Profile
- city: Lisbon
- coordinates: 38.72, -9.14
- max_km: 15
- max_price: 60
- time_windows: weekday_evenings, weekends
- max_events_per_week: 3
- max_news_items: 10

## Topics
- [x] **ai** — Technical AI/ML; skip funding and hype
  - keywords: LLM, machine learning
  - weight: 0.8
  - plugins: events, news
- [x] **jazz** — Small-venue live jazz
  - keywords: jazz, improv
  - weight: 0.9
  - plugins: events
- [ ] **football** — Benfica matches   ← paused, kept for later

## Dislikes
- [x] networking mixers
- [x] tribute bands
- [ ] stand-up comedy
```
Defaults: `weight: 0.5`, `plugins:` all installed plugins.
`weekday_evenings` = Mon–Fri 18:00–24:00; `weekends` = Sat–Sun all day.

### `config/sources.md`
```markdown
# Sources

## Events
- [x] **ticketmaster** — ticketmaster
  - search: true
  - env: TICKETMASTER_API_KEY
- [x] **zdb-jazz** — ical
  - url: https://example.com/calendar.ics

## News
- [x] **hn** — hackernews
- [x] **arxiv-cl** — rss
  - url: http://export.arxiv.org/rss/cs.CL
- [ ] **gdelt** — gdelt
  - search: true
```
The text after `—` is the adapter `type`. `search: true` means the adapter receives the
keywords of enabled topics that list this plugin; otherwise it fetches its whole feed.
A section with no installed plugin is reported as a warning.

## 4. Data

### `Item` (kernel model, shared by all plugins)
| Field | Type | Notes |
|---|---|---|
| id | str | sha1(url) |
| plugin | str | `events`, `news`, … |
| title, url, source_id | str | |
| summary | str | raw text, truncated to 1,000 chars |
| published_at | datetime? | |
| start, end | datetime? | |
| extra | dict | plugin-specific (venue, lat, lon, price, …) |

### SQLite (kernel-owned)
- `items(id, plugin, json, first_seen)` — dedupe
- `selections(item_id, run_at, score, reason, external_ref)` — `external_ref` = calendar event id, etc.
- `feedback(item_id, signal ∈ {up, down, accepted, rejected}, at)`

## 5. Run loop (kernel)
1. Load and validate both documents.
2. For each enabled plugin with ≥ 1 enabled source:
   1. `sync_feedback(ctx)`
   2. `collect()` — per-adapter timeout 20 s; failures logged.
   3. Kernel drops items already in `items`.
   4. `filter()` — deterministic; also drops checked dislikes.
   5. Kernel `Ranker`: one LLM call per plugin (≤ 50 candidates), input = enabled topics for
      the plugin, dislikes, `rank_hints`, last 20 feedback examples. Output is JSON validated
      against a schema: `[{id, score 0–100, reason, duplicate_of?}]`. Without an API key, a
      keyword-overlap ranker is used.
   6. Keep top `max_items` with score ≥ `threshold`.
   7. `deliver()` → `DigestSection`.
3. Kernel writes the digest to `digests/YYYY-MM-DD.md` (committed in cloud mode; optional
   email) with sections in plugin order. Each item is rendered as:
   ```markdown
   - **Title** — reason/summary · [source](url) `id:ab12cd`
     - [ ] 👍
     - [ ] 👎
   ```
   At the start of the next run, the kernel reads ticked boxes in digests from the last 14 days
   and records them as `up` / `down` feedback (idempotent).

## 6. CLI
```
agent run [--plugin events|news] [--dry-run]
agent plugins
agent sources test <id>
agent rate <item-id> up|down
agent auth google                 # one-time OAuth consent (desktop browser)
agent schedule install|remove     # Task Scheduler on Windows, cron/launchd elsewhere
```

## 7. Code layout
```
personal_agent/
  kernel/
    config.py      # markdown checklist parser → Profile, Topic, Source
    models.py      # Item, DigestSection, RankHints
    plugin.py      # Plugin protocol, registry, entry-point discovery
    runner.py      # run loop
    services/      # store.py, ranker.py, calendar.py, digest.py, secrets.py
    platform.py    # data dirs, scheduler install (schtasks / cron / launchd)
  plugins/
    events/        # plugin.py, adapters/{ical,ticketmaster}.py
    news/          # plugin.py, adapters/{rss,hackernews,gdelt}.py
  cli.py
config/interests.md, config/sources.md
digests/                    # generated digests (feedback checkboxes)
.github/workflows/agent.yml # cloud mode: scheduled run, state branch
.github/workflows/ci.yml    # tests on ubuntu + windows
```

## 8. Stack
Python 3.12+ · Anthropic SDK (model configurable via env) · google-api-python-client +
google-auth-oauthlib (Calendar, Gmail) · feedparser · icalendar · httpx · pydantic · SQLite ·
platformdirs · tzdata · keyring · GitHub Actions.

## 9. Acceptance criteria (v1 done)
1. `agent run --dry-run` with 1 event source + 2 news sources produces a digest with both sections.
2. Unchecking a topic or source removes its effect on the next run; no other edit needed.
3. The kernel has no imports from `plugins/`; removing the `news` plugin package leaves
   `events` working.
4. No suggestion overlaps a busy slot or violates a constraint (unit-tested filters).
5. Nothing is ever written to the main calendar.
6. A plugin or source failure does not fail the run.
7. Deleting a suggestion is recorded as `rejected` on the next run and appears in the next
   ranking prompt.
8. Cost per daily run < $0.10 at ≤ 200 candidates.
9. The test suite passes on Windows and Linux in CI.
10. In cloud mode, the full loop works from an Android phone alone: deleting a suggestion in
    the Calendar app is recorded as `rejected`, and ticking 👍 in a digest on github.com is
    recorded as `up`, on the next run.
11. The Google token holds only `calendar.app.created`, `calendar.freebusy` and (optionally)
    `gmail.send`.

## 10. Open questions
- Digest channel: email, repo file, or both?
- Home city and initial interests.
- Is a private GitHub repo acceptable for storing interests, digests and state? (Required for
  cloud mode; otherwise use Windows local mode.)
