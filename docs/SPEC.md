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
│ Services: Store (SQLite) · Ranker (LLM) · Extractor (LLM, web search)   │
│           Availability (free/busy) · Google auth · Secrets · HTTP · Log │
│ Generic adapters: web_search · web_page (usable by any content plugin)  │
└──────▲───────────────────▲─────────────────────▲──────────────▲─────────┘
       │ Content API       │                     │ Output API   │
┌──────┴───────┐   ┌───────┴──────┐   ┌──────────┴───┐  ┌───────┴────────┐
│ events       │   │ news         │   │ google_      │  │ obsidian       │
│  ical        │   │  rss, hn,    │   │ calendar     │  │ markdown_digest│
│              │   │  gdelt       │   │ google_tasks │  │ email  …       │
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
- Provide shared services. Plugins never open DB connections or call the LLM directly (only
  the kernel `Ranker` and `Extractor` do); Google
  API clients come from the kernel's `Google auth` service with only the scopes they declared.
- Isolate failures: an exception in a plugin, adapter or output is logged and skipped.

### Content plugin API
```python
class ContentPlugin(Protocol):
    name: str   # "## <Name>" section in sources.md
    title: str  # digest section heading, e.g. "Plan" (events), "Read" (news)

    def collect(self, sources, interests, ctx) -> list[Item]: ...
    def filter(self, items, interests, ctx) -> list[Item]: ...  # deterministic, no LLM
    def rank_hints(self, interests, ctx) -> RankHints: ...      # rubric, threshold, max items
```
`interests` contains only the enabled topics routed to the plugin, plus enabled dislikes.
The events plugin caps `max_items` at `max_events_per_week` minus events delivered in the
last 7 days (rolling), so daily runs don't exceed the weekly budget.
A content plugin owns its **source adapters** (`type` in `sources.md`) and domain rules.

### Generic adapters (kernel)
For sources without a feed or API, the kernel provides two adapters any content plugin can use
in `sources.md`. The plugin supplies an **extraction spec** (target `Item` fields plus domain
instructions, e.g. "events in Portugal with date, venue, price"); the kernel `Extractor`
returns schema-validated `Item`s.

| Adapter | What it does | Cost / limits |
|---|---|---|
| `web_search` | For each enabled topic routed to the plugin (highest weight first, at most `max_queries`, default 3), the Extractor runs one Claude request with the server-side web search tool (one search) and the web fetch tool (up to `fetch_pages` result pages, default 2), and extracts items. Snippets rarely contain dates, so without fetching few dated events are found. Optional `allowed_domains`, `query` template. Items must link to a site the search or fetch returned. | measured with Opus 5.5: ~$0.10 per query without fetching, ~$0.30–0.45 with 2 fetched pages (`anthropic_api`); included in the plan with `claude_code`; needs a working LLM provider |
| `web_page` | Fetches `url`. **Feed discovery first** (`feed: auto`; or `none`, or an explicit feed URL): an advertised `.ics` feed is parsed by the plugin's iCal adapter with no LLM; an RSS/Atom feed (preferring the feed under the page's own path, e.g. a WordPress category feed; comment feeds ignored) has its entries from the last 60 days extracted, because entries are articles whose event dates are in the text. Otherwise the HTML is reduced to text and extracted. Results are cached per document hash (including empty results), so unchanged entries and pages cost no LLM call. | one batched LLM call per run with new or changed documents; respects `robots.txt`; 1 request/s per host |

Rules: fetched pages and search results are untrusted data, passed to the LLM in delimited
blocks with an instruction to ignore embedded instructions; every extracted item must carry a
source URL; items whose date or URL fails validation are dropped. Without a working LLM provider, `web_*`
sources are skipped with a warning (feed-discovered sources still work).
Site-specific scraping code is avoided: if a generic `web_page` source proves unreliable, the
fix is a dedicated adapter in the plugin, written later.

### LLM providers (kernel)
The `Ranker` and `Extractor` call the LLM through one kernel interface (`LLM.parse`: system
prompt, untrusted content, pydantic schema, optional web search budget → validated object plus
the URLs the search/fetch tools returned). `PERSONAL_AGENT_LLM_PROVIDER` selects the
implementation; plugins never know which one runs. Two vendors, by API key or (Claude) by
subscription; `claude_code` is the default:

| Provider | Vendor | How | Credentials | Billing | Status |
|---|---|---|---|---|---|
| `anthropic_api` | Claude | Anthropic SDK: structured outputs, adaptive thinking, effort, server-side refusal fallbacks, server web search + web fetch tools | `PERSONAL_AGENT_ANTHROPIC_API_KEY` | pay per use; exact cost per run | M2 |
| `claude_code` (default) | Claude | Claude Code CLI, non-interactive (`claude -p --output-format stream-json --json-schema …`) subprocess in an empty temp folder, with `--safe-mode --strict-mcp-config --disable-slash-commands` and its own `--system-prompt` (user customizations off: a call is ~1k tokens instead of ~260k); only `WebSearch`/`WebFetch` allowed, `--max-turns` from the search budget; answer from `structured_output`, result URLs from the stream's tool results; default model `claude-sonnet-5-5` | local Claude Code login with a Claude Pro/Max subscription; cloud: `PERSONAL_AGENT_CLAUDE_CODE_OAUTH_TOKEN` (from `claude setup-token`) | included in the plan, within its usage limits; run shows the CLI's notional API cost | M3 |
| `gemini_api` | Gemini | Google Gen AI SDK (`google-genai`): JSON output with a response schema, Google Search grounding and URL context tools; source URLs from grounding metadata (Gemini 3+ allows schema output and grounding in one request); default model `gemini-3.8-flash` | `PERSONAL_AGENT_GEMINI_API_KEY` (Google AI Studio key) | pay per use (or the API's free tier, with lower limits); cost per run from token usage | M3 |
| `gemini_cli` | Gemini | Gemini CLI, non-interactive (`gemini -p "" --output-format stream-json --extensions "" --allowed-mcp-server-names ""`) subprocess in an empty temp folder; only `google_web_search`/`web_fetch` allowed; JSON validated against the schema with one retry; grounding redirect URLs resolved; default model `gemini-3.8-flash` | local Gemini CLI login (`gemini`), optional `PERSONAL_AGENT_GOOGLE_CLOUD_PROJECT` *(Gemini CLI 0.36/0.67 rejects personal Google accounts without a Cloud project with `IneligibleTierError`)* | included in plan / quota; run shows notional API cost | M3 |

Rules for all providers:
- Same prompts, untrusted-content delimiting, JSON validated against the pydantic schema (one
  retry with the validation error where the provider can't enforce the schema), and the same
  link/date validation.
- `PERSONAL_AGENT_MODEL` names a model of the selected vendor; each provider has its own
  default. `PERSONAL_AGENT_EFFORT` maps to the vendor's reasoning control where one exists.
- Credentials never cross providers: each provider reads only its own `PERSONAL_AGENT_*`
  variable, never the generic `ANTHROPIC_API_KEY`, `GEMINI_API_KEY` or `GOOGLE_API_KEY`; CLI
  providers get a clean subprocess environment without any API key, so a subscription run never
  falls back to paid API use.
- If the provider is unavailable (no key, CLI missing or logged out, quota or usage limit
  reached), the run continues with keyword ranking and skips web sources, with a warning.
- Link check with search: Gemini grounding may return redirect URLs
  (`vertexaisearch.cloud.google.com/...`) instead of the source page. The provider resolves
  them (one HEAD request each, same politeness rules) before the Extractor's same-site check;
  unresolvable links drop the item.
- Provider-specific features stay inside the provider (e.g. Claude's server-side refusal
  fallbacks, Gemini's safety settings); the kernel only sees `LLM.parse`.
- Subscriptions used this way are for the user's own personal agent only. Data use differs by
  tier (e.g. Google may use free-tier Gemini API and consumer-account data to improve its
  products); USAGE.md points this out.

### Output plugin API
```python
class OutputPlugin(Protocol):
    type: str  # e.g. "google_calendar"
    google_scopes: list[str]  # [] if not a Google output

    def accepts(self, item: RankedItem) -> bool: ...  # e.g. calendar needs item.start
    def publish(self, items, cfg, ctx) -> list[Delivery]: ...  # idempotent per (item, output)
    def sync_feedback(self, deliveries, cfg, ctx) -> list[Feedback]: ...
    def preview(self, items, cfg) -> str: ...  # for --dry-run
```
`Delivery = (item_id, output_id, external_ref)`; `Feedback = (item_id, signal, at, output_id)`.

**Namespace rule:** every output writes only into a container it owns (the *Suggestions*
calendar, an *Agent* task list, an `Agent/` vault folder, the `digests/` folder) and never
edits or deletes content the user created.

Adding a content type or an output = adding a plugin package; the kernel does not change.

### Built-in content plugins
| Plugin | Adapters | Filter |
|---|---|---|
| `events` | `ical` + generic `web_search`, `web_page` | next 14 days, distance, price, time windows, free/busy (Availability service) |
| `news` | `rss`, `hackernews`, `gdelt` + generic `web_search`, `web_page` | published ≤ 48 h |

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

**Config location:** `PERSONAL_AGENT_CONFIG_DIR` (default `config/`) can point inside the Obsidian vault,
so interests, sources and outputs are edited as ordinary notes on any device.

**Cross-platform requirements**
- Pure-Python or wheel-available dependencies only; no POSIX-only calls (`fcntl`, `os.fork`,
  shell scripts).
- Paths via `pathlib`; config/state/cache locations via `platformdirs`.
- All file I/O uses `encoding="utf-8"` explicitly (Windows otherwise defaults to a legacy code
  page). The markdown parser accepts CRLF; `.gitattributes` sets `*.md text eol=lf`.
- Timezones via `zoneinfo` + the `tzdata` package (Windows has no IANA database).
- Secret names are app-specific (`PERSONAL_AGENT_` prefix, e.g. `PERSONAL_AGENT_ANTHROPIC_API_KEY`) so a
  user-wide variable never leaks this agent's key to other apps, or another app's key into
  this agent. The kernel passes the key to the SDK explicitly and does **not** fall back to
  the generic `ANTHROPIC_API_KEY`.
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
- **US-27** As a user, I choose the LLM behind the agent: my Claude subscription (default), or
  Claude or Gemini with an API key, by changing one setting.
- **US-28** As a user, a subscription-based run never silently switches to paid API usage.

### Platforms
- **US-17** As a user, the agent runs in the cloud on schedule without any of my devices on.
- **US-18** As an Android user, I can see, accept and reject suggestions, read and rate items,
  and edit my config using only apps I already have (Calendar, Tasks, Obsidian, Gmail, browser).
- **US-19** As a Windows user, I install with `pipx install` and schedule runs with one
  command (`agent schedule install`).
- **US-20** As a user, I trust that the agent cannot modify my main calendar because it never
  holds a permission that would allow it.

## 3. Configuration documents

Three plain markdown files in `PERSONAL_AGENT_CONFIG_DIR` (default `config/`), readable and editable in
any editor, in Obsidian, or on GitHub.

**Format rules**
- `- [x]` = enabled, `- [ ]` = disabled.
- Item line: `- [x] **<id>** — <description or type>`.
- Indented `- key: value` bullets under an item are its attributes; comma-separated values
  become lists.
- `## Heading` sections give context: in `sources.md` the `##` heading is the content plugin
  name (items under the `#` title or outside a section are ignored with a warning).
- ` ← comment` at the end of an item line or attribute value is stripped.
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
- [x] **web** — web_search
  - max_queries: 3
- [x] **some-venue** — web_page
  - url: https://venue.example/agenda/
- [ ] **club-calendar** — ical
  - url: https://club.example/calendar.ics

## News
- [x] **some-feed** — rss
  - url: https://news.example/feed/
```
The spec names no particular sources: the actual list is user data in `config/sources.md`
(the example above only shows the format). The text after `—` is the adapter `type`. `search: true` means the adapter receives the
keywords of enabled topics that list this plugin; otherwise it fetches its whole feed.
A section with checked sources but no installed content plugin is reported as a warning. A source with a missing
or invalid `url` is skipped with a warning. A `url` without a scheme is a local file path,
relative to the config directory (useful for exported `.ics` files and fixtures).

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
(default 0). Other attributes are passed to the output plugin as `cfg`. Relative output paths
(e.g. `path: digests`) resolve against the working directory (the repo root in cloud mode). An output whose type
is not installed, or that cannot run in the current mode (e.g. `obsidian` in cloud mode
without a git vault), is reported as a warning and skipped.

## 4. Data

### `Item` (kernel model, shared by all plugins)
| Field | Type | Notes |
|---|---|---|
| id | str | sha1 of a plugin-chosen stable key: the URL, or for events source + UID + start (each recurring occurrence is its own item) |
| plugin | str | `events`, `news`, … |
| title, url, source_id | str | |
| summary | str | raw text, truncated to 1,000 chars |
| published_at | datetime? | |
| start, end | datetime? | timezone-aware; outputs use these to decide `accepts()` |
| location | str? | venue or place name, shown by outputs |
| extra | dict | plugin-specific (`all_day`, `lat`, `lon`, `price`, …) |

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
   2. Kernel merges duplicates by `id`, records `first_seen`, and drops items already
      delivered to **every** output that routes this plugin (no wasted ranking calls).
   3. `filter()` — deterministic; then the kernel drops items matching a checked dislike
      (whole-phrase, case-insensitive, on title, summary and location).
   4. Kernel `Ranker`: one LLM call per plugin (≤ 50 candidates), input = enabled topics for
      the plugin, dislikes, `rank_hints`, last 20 feedback examples. Output is JSON validated
      against a schema: `[{id, score 0–100, reason, duplicate_of?}]`. Without a working LLM
      provider, a keyword-overlap ranker is used.
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
agent run [--plugin events|news] [--output <id>] [--dry-run] [--explain] [--max-cost USD]
agent plugins                     # content + output plugins, enabled state
agent sources test <id>
agent outputs test <id>           # publish one sample item to that output's namespace
agent auth google                 # one-time OAuth consent for enabled outputs' scopes
agent rate <item-id> up|down      # planned (M4), not yet implemented
agent schedule install|remove     # planned (M4), not yet implemented
```
`--dry-run` skips publishing only; ranking/extraction LLM calls still happen and are still
billed, so it is a safe-to-publish preview, not a free one. `--explain` prints every ranked
item's score, reason and verdict (kept / capped by `max_items` / below threshold), so "why was
item X filtered?" has a direct, CLI-native answer instead of requiring database access.
`--max-cost` exits 3 with a warning if the run's actual cost exceeds the given USD amount; it
is checked after the run completes and does not prevent the LLM calls that already happened.

The CLI is the **only** integration boundary into the kernel. Every other client — the
scheduled cloud/local run, and the IDE cockpit below — invokes it rather than importing kernel
or plugin modules directly, so the microkernel invariants (§1) hold regardless of caller.

### IDE / chat cockpit (optional, non-authoritative)
A VS Code custom agent (`.github/agents/personal-agent.agent.md`, scoped `@Personal Agent`) may
wrap the CLI for interactive configuration editing, source/output testing, dry-run previews and
troubleshooting. It is tooling around the product, not part of it:
- **No special access.** It calls the same CLI commands listed above; it never imports
  `personal_agent.kernel` or `personal_agent.plugins`, opens the SQLite store, or calls an LLM
  provider directly. Anything it can do, a person typing the CLI by hand could do identically.
- **Not a scheduler.** It only acts during an interactive chat session. Unattended execution
  stays with GitHub Actions or the OS scheduler (§1, "Running on Windows and Android"); the
  cockpit is never the thing that wakes up and runs the agent.
- **Unrelated to the kernel's own LLM use.** The kernel's `Ranker`/`Extractor` calls (§1, "LLM
  providers") are a narrow, structured, cost-accounted part of the run loop. The cockpit's
  conversational model is a separate, human-facing concern; neither depends on the other.
- **Write access is scoped to `config/*.md` and `docs/`, and enforced by a hook**, not just
  the cockpit's instructions: `scripts/check_cockpit_edit_scope.py` runs as a `PreToolUse` hook
  and denies `edit`/`create` calls outside that scope. Changes to `kernel/` or `plugins/` code
  go through a reviewed coding session instead, so the "kernel never imports plugins" and
  idempotency invariants aren't put at risk by a casual chat instruction.

## 7. Code layout
```
personal_agent/
  kernel/
    config.py      # markdown checklist parser → Profile, Topic, Source, OutputConfig
    models.py      # Item, RankedItem, RankHints, Delivery, Feedback
    plugin.py      # ContentPlugin / OutputPlugin protocols, registry, entry points
    runner.py      # run loop + router
    services/      # store, ranker, extractor, llm (interface, selection, anthropic_api),
                   # llm_common, llm_claude_code, llm_gemini_api, llm_gemini_cli,
                   # availability, google, http, checklist
    adapters/      # generic: web_search.py, web_page.py (feed discovery + extraction)
    platform.py    # data dirs, scheduler install (schtasks / cron / launchd)
  plugins/
    content/
      events/      # plugin.py, extraction spec, adapters/ical.py
      news/        # plugin.py, adapters/{rss,hackernews,gdelt}.py
    outputs/
      google_calendar.py  google_tasks.py  obsidian.py  markdown_digest.py  email.py
  cli.py
config/interests.md, config/sources.md, config/outputs.md
digests/                    # markdown_digest output (generated; gitignored, not committed)
.github/workflows/agent.yml # cloud mode: scheduled run, state branch
.github/workflows/ci.yml    # tests on ubuntu + windows
.github/agents/personal-agent.agent.md  # IDE cockpit (§6); calls the CLI, not the kernel
.vscode/tasks.json           # CLI commands exposed as VS Code tasks
scripts/check_cockpit_edit_scope.py  # PreToolUse hook enforcing the cockpit's write scope
.pre-commit-config.yaml      # local mirror of ci.yml: ruff, mypy, lint-imports, pytest
```

## 8. Stack
Python 3.12+ · LLM: Anthropic SDK, Google Gen AI SDK (`google-genai`), optional Claude Code CLI
and Gemini CLI (subscription providers); model configurable via env · google-api-python-client +
google-auth-oauthlib (Calendar, Tasks, Gmail) · feedparser · icalendar · httpx · pydantic ·
SQLite · platformdirs · tzdata · keyring · GitHub Actions.

## 9. Acceptance criteria (v1 done)
1. `agent run --dry-run` with event sources of each type (`ical`, `web_page`, `web_search`),
   2 news sources and the `markdown_digest` + `google_calendar` outputs previews both outputs;
   events appear in both, news only in the digest.
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
9. Every run reports its LLM usage and estimated cost. *(Open, see §10: the original target of
   < $0.25 per run is met without `web_search`, but one web-search query with page fetching
   costs ~$0.30–0.45 on Opus 5.5.)*
10. The test suite passes on Windows and Linux in CI.
11. In cloud mode, the full loop works from an Android phone alone using the Calendar app and
    a browser.
12. The Google token holds only the scopes required by enabled outputs plus
    `calendar.freebusy`.
13. A `web_page` source whose page or feed entries are unchanged since the last run makes no
    LLM call, and a page that links an iCal feed is parsed without the LLM.
14. Text such as "ignore previous instructions" inside a fetched page or search result has no
    effect on extraction or ranking (fixture test).
15. With each of `claude_code`, `gemini_api` and `gemini_cli` selected and no other provider's
    credentials set, a run ranks and extracts events; CLI subprocesses receive no API key; a
    missing credential, logged-out CLI or reached limit degrades to keyword ranking with a
    warning.
16. The same recorded documents produce schema-valid items through every provider (shared
    contract tests with fake vendor responses), and search results from every provider pass
    the same link check (Gemini redirect URLs resolved first).

## 10. Open questions
- Which outputs to enable by default: digest only, or digest + calendar?
- Home city and initial interests.
- Sources that publish only on social media (Instagram, Facebook) are not fetchable without
  login and are out of scope; is a manual import (e.g. an exported `.ics`) enough for those?
- Cost of `web_search`: resolved for now by making `claude_code` (subscription) the default;
  open: do daily scheduled runs fit within a Pro plan's usage limits (M4 quality check)?
- Measured cost per run on `gemini_api` (needs a key; list prices are in the cost estimate).
- `gemini_cli`: implemented and contract-tested; personal Google accounts without a Cloud project
  are rejected by Gemini CLI (`IneligibleTierError`), so live use requires an eligible account or
  `PERSONAL_AGENT_GOOGLE_CLOUD_PROJECT`.
- Is a private GitHub repo acceptable for storing interests, digests and state? (Required for
  cloud mode; otherwise use Windows local mode.)
- Obsidian: is the vault git-synced (cloud mode works) or Obsidian Sync-only (local mode)?
