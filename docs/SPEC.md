# Personal Agent — Spec v1

A personal agent that finds **events** and **news** matching my interests and publishes them
to the places I already use: Google Calendar, Google Tasks, an Obsidian vault, a markdown
digest, email.

## 1. Architecture: microkernel

A small **kernel** that knows nothing about events, news, calendars or notes, plus two kinds
of plugins:

- **Content plugins** find and judge items (`events`, `news`).
- **Output plugins** publish items and read back feedback (`google_calendar`, `google_tasks`,
  `obsidian`, `markdown_digest`, `email`).

```
  config/interests.md      config/sources.md          config/outputs.md
          │                        │                          │
          ▼                        ▼                          ▼
┌──────────────────────────────── KERNEL ─────────────────────────────────┐
│ Config (markdown checklists) · Plugin registry · Run loop · Router      │
│ Services: Store (SQLite) · Ranker (LLM) · Availability (free/busy)      │
│           Google auth · Secrets · HTTP · Clock · Log                    │
└──────▲───────────────────▲─────────────────────▲──────────────▲─────────┘
       │ Content API       │                     │ Output API   │
┌──────┴───────┐   ┌───────┴──────┐   ┌──────────┴───┐  ┌───────┴────────┐
│ events       │   │ news         │   │ google_      │  │ obsidian       │
│  ical,       │   │  rss, hn,    │   │ calendar     │  │ markdown_digest│
│  ticketmaster│   │  gdelt       │   │ google_tasks │  │ email  …       │
└──────────────┘   └──────────────┘   └──────────────┘  └────────────────┘
        items  ──►  dedupe · filter · rank  ──►  route  ──►  publish
                                                    ◄──  feedback
```

### Kernel responsibilities
- Parse the three config documents into typed objects.
- Discover plugins via entry point groups `personal_agent.content` and
  `personal_agent.outputs`; give each plugin only its own config.
- Run the loop (§5) and **route** ranked items to outputs according to `outputs.md`.
- Record deliveries and normalize feedback from every output into one `feedback` table.
- Provide shared services. Plugins never open DB connections or call the LLM directly; Google
  API clients come from the kernel's `Google auth` service with only the scopes they declared.
- Isolate failures: an exception in a plugin, adapter or output is logged and skipped.

### Content plugin API
```python
class ContentPlugin(Protocol):
    name: str                                            # "## <Name>" section in sources.md
    def collect(self, sources, profile, ctx) -> list[Item]: ...
    def filter(self, items, profile, ctx) -> list[Item]: ...   # deterministic, no LLM
    def rank_hints(self, profile) -> RankHints: ...            # rubric, threshold, max items
```
A content plugin owns its **source adapters** (`type` in `sources.md`) and domain rules.

### Output plugin API
```python
class OutputPlugin(Protocol):
    type: str                                  # e.g. "google_calendar"
    google_scopes: list[str]                   # [] if not a Google output
    def accepts(self, item: RankedItem) -> bool: ...           # e.g. calendar needs item.start
    def publish(self, items, cfg, ctx) -> list[Delivery]: ...  # idempotent per (item, output)
    def sync_feedback(self, deliveries, cfg, ctx) -> list[Feedback]: ...
    def preview(self, items, cfg) -> str: ...                  # for --dry-run
```
`Delivery = (item_id, output_id, external_ref)`; `Feedback = (item_id, signal, at, output_id)`.

**Namespace rule:** every output writes only into a container it owns (the *Suggestions*
calendar, an *Agent* task list, an `Agent/` vault folder, the `digests/` folder) and never
edits or deletes content the user created.

Adding a content type or an output = adding a plugin package; the kernel does not change.

### Built-in content plugins
| Plugin | Adapters | Filter |
|---|---|---|
| `events` | `ical`, `ticketmaster` | next 14 days, distance, price, time windows, free/busy (Availability service) |
| `news` | `rss`, `hackernews`, `gdelt` | published ≤ 48 h |

### Built-in output plugins
| Output type | Publishes | Accepts | Feedback | Google scopes | Runs in |
|---|---|---|---|---|---|
| `markdown_digest` | `digests/YYYY-MM-DD.md`, one section per content plugin | all | ticked 👍/👎 boxes | — | cloud, local |
| `google_calendar` | tentative event on the *Suggestions* calendar | items with `start` | kept = accepted, deleted = rejected | `calendar.app.created` | cloud, local |
| `google_tasks` | task in an *Agent* list; events get the event date as due date (date only, API ignores time); news become "Read: …" with link | all | completed = accepted/up, deleted = rejected/down | `tasks` | cloud, local |
| `obsidian` | daily note `<vault>/<folder>/YYYY-MM-DD.md` with frontmatter (`tags: [agent]`) and the digest checkbox format | all | ticked 👍/👎 boxes | — | local; cloud only if the vault is a git repo |
| `email` | the digest as an HTML/markdown email | all | — (v1) | `gmail.send` | cloud, local |

Feedback rules: an item open/untouched for 14 days gives no signal; if several outputs give
signals for one item, the latest wins. `agent rate <id> up|down` adds feedback directly.

**Not in v1:** watches (news → event tracking), embedding clustering, chat bot, web UI,
auto-editing interests, feedback by email reply.

### Running on Windows and Android
**Principle:** the agent is a headless scheduled job. Windows PCs and Android phones are
*thin clients*: you use the apps your outputs target (Google Calendar, Google Tasks, Obsidian,
Gmail, a browser). No device has to stay on, and there is no native app in v1.

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

**Obsidian and sync:** the `obsidian` output writes files.
- *Local mode:* write straight into the vault folder; any sync method (Obsidian Sync,
  OneDrive, Syncthing, git) carries notes to Android.
- *Cloud mode:* only works if the vault is a git repository (e.g. synced with the community
  Obsidian Git plugin on desktop and Android). The workflow clones it with a scoped token,
  writes the note, and pushes. Obsidian Sync has no public API, so a Sync-only vault requires
  local mode.

**What you do from each device**
| Task | Windows | Android |
|---|---|---|
| Edit `interests.md` / `sources.md` / `outputs.md` | any editor, Obsidian, or github.com | Obsidian (config in vault), or github.com in a browser |
| See event suggestions | Google Calendar (web) | Google Calendar app (*Suggestions* calendar) |
| Accept / reject an event | keep / delete it | keep / delete it |
| Work through items as to-dos | Google Tasks (web, Calendar side panel) | Google Tasks app: complete = 👍, delete = 👎 |
| Read and rate the digest | Obsidian daily note, `digests/` on github.com, or Gmail | Obsidian app, github.com, or Gmail app |
| Run on demand | `agent run`, or "Run workflow" | "Run workflow" on github.com |

**Config location:** `AGENT_CONFIG_DIR` (default `config/`) can point inside the Obsidian vault,
so interests, sources and outputs are edited as ordinary notes on any device.

**Cross-platform requirements**
- Pure-Python or wheel-available dependencies only; no POSIX-only calls (`fcntl`, `os.fork`,
  shell scripts).
- Paths via `pathlib`; config/state/cache locations via `platformdirs`.
- All file I/O uses `encoding="utf-8"` explicitly (Windows otherwise defaults to a legacy code
  page). The markdown parser accepts CRLF; `.gitattributes` sets `*.md text eol=lf`.
- Timezones via `zoneinfo` + the `tzdata` package (Windows has no IANA database).
- Secrets: environment variables, or the OS keyring (`keyring` → Windows Credential Manager)
  locally; GitHub Actions secrets in cloud mode. Never in config files.
- Google OAuth: `agent auth google` runs the browser consent flow once on a desktop for the
  **union of scopes declared by enabled outputs** (plus `calendar.freebusy` for the
  Availability service) and stores the refresh token (keyring locally; repo secret in cloud
  mode). Enabling a Google output that needs a new scope prompts a re-auth.
- **Least privilege:** `calendar.app.created` only reaches calendars the agent created, so it
  cannot modify the main calendar; `calendar.freebusy` reads busy slots, not event contents.
  Caveat: Google Tasks has no narrower write scope than `tasks` (all task lists), so the
  namespace rule is enforced by our code there, not by Google. `google_tasks` is off by default.
- CI runs the test suite on `ubuntu-latest` and `windows-latest`.

## 2. User stories

### Interests
- **US-1** As a user, I edit `interests.md` — a checklist — to define topics; checking or
  unchecking a box turns a topic on or off without deleting it.
- **US-2** As a user, I set constraints (city, distance, price, time windows, max events per
  week) in the same document.
- **US-3** As a user, I keep a checklist of dislikes; checked ones exclude matching items.

### Sources
- **US-4** As a user, I manage `sources.md` — one section per content plugin, one checkbox per
  source — independently of interests.
- **US-5** As a user, I run `agent sources test <id>` to see what a source returns.

### Outputs
- **US-21** As a user, I enable or disable outputs with a checklist in `outputs.md`.
- **US-22** As a user, I route each content plugin to one or more outputs (e.g. events →
  Calendar + Obsidian, news → Obsidian + Tasks).
- **US-23** As a user, I give feedback in whichever output I use — delete a calendar entry,
  complete or delete a task, tick a box in a note — and it all counts.
- **US-24** As an Obsidian user, I get a daily note in my vault, and I can keep the config
  documents in the vault to edit them in Obsidian on Android.
- **US-25** As a user, re-running the agent never creates duplicate calendar entries, tasks
  or notes.
- **US-26** As a user, outputs only write inside their own calendar, task list or folder, and
  never touch my own entries.

### Plugins
- **US-6** As a user, I see installed and enabled content and output plugins (`agent plugins`).
- **US-7** As a developer, I add a content type or an output by writing a plugin package with
  an entry point, without modifying the kernel.

### Events
- **US-8** As a user, I get up to N suggested events per week that fit my free time, each with
  a one-line reason it matches.
- **US-9** As a user, with the calendar output enabled, suggestions appear on a separate
  *Suggestions* calendar, never on my main calendar.
- **US-10** As a user, I accept a suggestion by keeping it and reject it by deleting it (from any
  device's Calendar app).

### News
- **US-11** As a user, I get the top ≤ 10 news items for my interests daily in my chosen
  outputs, each with a short summary and source link.
- **US-12** As a user, the same story from multiple outlets appears once.
- **US-13** As a user, I rate news in the output where I read it, or with
  `agent rate <id> up|down`.

### Learning & ops
- **US-14** As a user, my past ratings influence future selections.
- **US-15** As a user, the agent runs on a schedule (news daily, events weekly).
- **US-16** As a user, `agent run --dry-run` previews what each output would publish, without
  writing anything.

### Platforms
- **US-17** As a user, the agent runs in the cloud on schedule without any of my devices on.
- **US-18** As an Android user, I can see, accept and reject suggestions, read and rate items,
  and edit my config using only apps I already have (Calendar, Tasks, Obsidian, Gmail, browser).
- **US-19** As a Windows user, I install with `pipx install` and schedule runs with one
  command (`agent schedule install`).
- **US-20** As a user, I trust that the agent cannot modify my main calendar because it never
  holds a permission that would allow it.

## 3. Configuration documents

Three plain markdown files in `AGENT_CONFIG_DIR` (default `config/`), readable and editable in
any editor, in Obsidian, or on GitHub.

**Format rules**
- `- [x]` = enabled, `- [ ]` = disabled.
- Item line: `- [x] **<id>** — <description or type>`.
- Indented `- key: value` bullets under an item are its attributes; comma-separated values
  become lists.
- `## Heading` sections give context: in `sources.md` the heading is the content plugin name.
- Anything else (paragraphs, notes, Obsidian frontmatter) is ignored, so the files can hold
  free-form comments.
- Parse errors name the file and line; an invalid item is skipped, not fatal.

### `interests.md`
```markdown
# Interests

## Profile
- city: Lisbon
- coordinates: 38.72, -9.14
- timezone: Europe/Lisbon
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
Defaults: `weight: 0.5`, `plugins:` all installed content plugins.
`weekday_evenings` = Mon–Fri 18:00–24:00; `weekends` = Sat–Sun all day.

### `sources.md`
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
A section with no installed content plugin is reported as a warning.

### `outputs.md`
```markdown
# Outputs

- [x] **calendar** — google_calendar
  - plugins: events
  - calendar_name: Suggestions
- [x] **digest** — markdown_digest
  - plugins: events, news
  - path: digests/
- [x] **vault** — obsidian
  - plugins: events, news
  - vault_path: C:\Users\me\Obsidian\Main
  - folder: Agent
- [ ] **tasks** — google_tasks
  - plugins: news
  - list_name: Agent
  - min_score: 80
- [ ] **mail** — email
  - plugins: events, news
  - to: me@example.com
```
The text after `—` is the output `type`; several instances of one type are allowed (e.g. two
vault folders). Common attributes: `plugins` (routing; default all) and `min_score`
(default 0). Other attributes are passed to the output plugin as `cfg`. An output whose type
is not installed, or that cannot run in the current mode (e.g. `obsidian` in cloud mode
without a git vault), is reported as a warning and skipped.

## 4. Data

### `Item` (kernel model, shared by all plugins)
| Field | Type | Notes |
|---|---|---|
| id | str | sha1(url) |
| plugin | str | `events`, `news`, … |
| title, url, source_id | str | |
| summary | str | raw text, truncated to 1,000 chars |
| published_at | datetime? | |
| start, end | datetime? | outputs use these to decide `accepts()` |
| extra | dict | plugin-specific (venue, lat, lon, price, …) |

`RankedItem` = `Item` + `score`, `reason`, `also_at` (duplicate URLs).

### SQLite (kernel-owned)
- `items(id, plugin, json, first_seen)` — dedupe
- `rankings(item_id, run_at, score, reason)`
- `deliveries(item_id, output_id, external_ref, delivered_at)` — unique `(item_id, output_id)`
- `feedback(item_id, output_id, signal ∈ {up, down, accepted, rejected}, at)`

## 5. Run loop (kernel)
1. Load and validate `interests.md`, `sources.md`, `outputs.md`.
2. **Sync feedback:** for each enabled output, `sync_feedback(deliveries of last 14 days)`;
   store new signals (idempotent).
3. For each enabled content plugin with ≥ 1 enabled source:
   1. `collect()` — per-adapter timeout 20 s; failures logged.
   2. Kernel drops items already in `items`.
   3. `filter()` — deterministic; also drops checked dislikes.
   4. Kernel `Ranker`: one LLM call per plugin (≤ 50 candidates), input = enabled topics for
      the plugin, dislikes, `rank_hints`, last 20 feedback examples. Output is JSON validated
      against a schema: `[{id, score 0–100, reason, duplicate_of?}]`. Without an API key, a
      keyword-overlap ranker is used.
   5. Keep top `max_items` with score ≥ `threshold`.
4. **Route:** for each enabled output, select ranked items whose plugin is in `plugins`, with
   score ≥ `min_score`, that the output `accepts()`, and that have no delivery to that output yet.
5. **Publish:** `publish()` per output (or `preview()` with `--dry-run`); record deliveries.

Digest-style outputs (`markdown_digest`, `obsidian`) render each item as:
```markdown
- **Title** — reason/summary · [source](url) `id:ab12cd`
  - [ ] 👍
  - [ ] 👎
```
A shared renderer/parser for this format lives in the kernel (`services/checklist.py`) so the
two outputs and the config parser agree on syntax.

## 6. CLI
```
agent run [--plugin events|news] [--output <id>] [--dry-run]
agent plugins                     # content + output plugins, enabled state
agent sources test <id>
agent outputs test <id>           # publish one sample item to that output's namespace
agent rate <item-id> up|down
agent auth google                 # one-time OAuth consent for enabled outputs' scopes
agent schedule install|remove     # Task Scheduler on Windows, cron/launchd elsewhere
```

## 7. Code layout
```
personal_agent/
  kernel/
    config.py      # markdown checklist parser → Profile, Topic, Source, OutputConfig
    models.py      # Item, RankedItem, RankHints, Delivery, Feedback
    plugin.py      # ContentPlugin / OutputPlugin protocols, registry, entry points
    runner.py      # run loop + router
    services/      # store, ranker, availability, google_auth, secrets, checklist
    platform.py    # data dirs, scheduler install (schtasks / cron / launchd)
  plugins/
    content/
      events/      # plugin.py, adapters/{ical,ticketmaster}.py
      news/        # plugin.py, adapters/{rss,hackernews,gdelt}.py
    outputs/
      google_calendar.py  google_tasks.py  obsidian.py  markdown_digest.py  email.py
  cli.py
config/interests.md, config/sources.md, config/outputs.md
digests/                    # markdown_digest output
.github/workflows/agent.yml # cloud mode: scheduled run, state branch
.github/workflows/ci.yml    # tests on ubuntu + windows
```

## 8. Stack
Python 3.12+ · Anthropic SDK (model configurable via env) · google-api-python-client +
google-auth-oauthlib (Calendar, Tasks, Gmail) · feedparser · icalendar · httpx · pydantic ·
SQLite · platformdirs · tzdata · keyring · GitHub Actions.

## 9. Acceptance criteria (v1 done)
1. `agent run --dry-run` with 1 event source, 2 news sources and the `markdown_digest` +
   `google_calendar` outputs previews both outputs; events appear in both, news only in the
   digest.
2. Unchecking a topic, source or output removes its effect on the next run; no other edit needed.
3. The kernel has no imports from `plugins/`; removing any one plugin package (e.g. `news` or
   `obsidian`) leaves the rest working.
4. No suggestion overlaps a busy slot or violates a constraint (unit-tested filters).
5. Nothing is ever written to the main calendar, and no output writes outside its namespace.
6. A plugin, source or output failure does not fail the run.
7. Running twice in a row creates no duplicate calendar events, tasks or note entries.
8. Feedback from each output with feedback support (calendar delete, task complete/delete,
   ticked box in digest or Obsidian note) is recorded on the next run and appears in the next
   ranking prompt.
9. Cost per daily run < $0.10 at ≤ 200 candidates.
10. The test suite passes on Windows and Linux in CI.
11. In cloud mode, the full loop works from an Android phone alone using the Calendar app and
    a browser.
12. The Google token holds only the scopes required by enabled outputs plus
    `calendar.freebusy`.

## 10. Open questions
- Which outputs to enable by default: digest only, or digest + calendar?
- Home city and initial interests.
- Is a private GitHub repo acceptable for storing interests, digests and state? (Required for
  cloud mode; otherwise use Windows local mode.)
- Obsidian: is the vault git-synced (cloud mode works) or Obsidian Sync-only (local mode)?
