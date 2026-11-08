# Usage

How to install, configure and run personal-agent. For recurring upkeep (re-signing in to Google, spend
checks, key rotation, backups), see [`OPERATIONS.md`](OPERATIONS.md). For the design, see [`SPEC.md`](SPEC.md);
for what is built and what is next, see [`PLAN.md`](PLAN.md).

> **What works today (M1–M4):** events from iCal, web pages and web search; LLM ranking on
> your Claude Pro/Max plan (default), a Claude API key, a Gemini API key, or Gemini CLI; a
> markdown digest; suggestions in Google Calendar; feedback (kept/deleted suggestions, ticked
> boxes, `agent rate`) that steers the ranking; daily runs in GitHub Actions or the OS
> scheduler. **Not yet:** news, Obsidian, Google Tasks and email (M5).

---

## Quickstart

**1. Install** (Python 3.12+, Windows, Linux or macOS):

```bash
git clone https://github.com/antonio-pedro-ribeiro/personal-agent
cd personal-agent
python -m pip install -e .
```

**2. Try the offline demo.** No keys or network needed, and it writes nothing:

```bash
agent --config-dir examples/demo run --dry-run
```

It reads a local calendar file, drops events that fail your filters (a disliked mixer, a
weekday-morning workshop, an event in Porto, a €150 gala) and previews the digest.

**3. Sign in to Claude Code** so the agent can search the web and rank with an LLM. By
default it uses your Claude Pro/Max plan through [Claude Code](https://claude.com/claude-code):
install it, run `claude` once and `/login` with your Pro/Max account. Nothing else to set.

Prefer pay-per-use with a Claude or Gemini API key? See
[Choosing an LLM provider](#choosing-an-llm-provider).

**4. Describe what you like.** Copy the three files from [`examples/demo/`](../examples/demo/) into
`config/` (git-ignored, so your interests are never committed), then set your city, budget and
topics in `config/interests.md` and check which sources are on in `config/sources.md`.

**5. Preview a real run:**

```bash
agent run --dry-run
```

**6. Run it for real.** This writes `digests/YYYY-MM-DD.md`; run it again and nothing is
duplicated:

```bash
agent run
```

To also get suggestions in Google Calendar, follow [Google Calendar setup](#google-calendar-setup).
To run it every day by itself, see [Running every day](#running-every-day).

---

## How a run works

```
interests.md ─┐
sources.md ───┼─► collect ─► dedupe ─► filter ─► rank ─► route ─► outputs
outputs.md ───┘
```

1. **Feedback:** what you did with earlier suggestions is read back (see
   [Feedback](#feedback)). Skipped in `--dry-run`.
2. **Collect:** every checked source is fetched.
3. **Dedupe:** items already delivered to every output are skipped, so they cost nothing.
4. **Filter:** deterministic rules (dates, distance, price, time windows, busy slots,
   dislikes). No LLM.
5. **Rank:** the LLM scores what is left from 0 to 100 against your topics and your
   latest 20 feedback signals, and writes a one-line reason. Without a key, a keyword matcher is used instead.
6. **Route and publish:** each checked output gets the items routed to it, at most once.

A failing source or output is reported as a warning; the rest of the run continues.

---

## Configuration

Three markdown files in `config/` (or the folder in `PERSONAL_AGENT_CONFIG_DIR`, for example
inside an Obsidian vault). Edit them in any editor, in Obsidian, or on github.com.

### Format rules (all three files)

| Syntax | Meaning |
|---|---|
| `- [x] **id** — text` | an enabled item (`[X]` also works) |
| `- [ ] **id** — text` | a disabled item: kept, but ignored |
| `  - key: value` (indented) | a setting of the item above |
| `a, b, c` | a list |
| `← comment` at the end of a line | ignored |
| Paragraphs, notes, Obsidian frontmatter | ignored |

An invalid item is skipped with a warning that names the file and line.

### `interests.md`

```markdown
## Profile
- city: Lisbon
- coordinates: 38.72, -9.14
- timezone: Europe/Lisbon
- max_km: 15
- max_price: 60
- time_windows: weekday_evenings, weekends
- max_events_per_week: 3

## Topics
- [x] **jazz** — Small-venue live jazz
  - keywords: jazz, improv
  - weight: 0.9
  - plugins: events

## Dislikes
- [x] networking mixers
```

**Profile**

| Setting | Effect |
|---|---|
| `city` | used in web searches |
| `coordinates` | `lat, lon` of home, for the distance filter |
| `timezone` | IANA name, e.g. `Europe/Lisbon`; dates and time windows use it (default UTC, with a warning) |
| `max_km` | drop events farther than this; events with unknown or online location are kept |
| `max_price` | drop events above this price (euros); events with unknown price are kept |
| `time_windows` | `weekday_evenings` (Mon–Fri 18:00–24:00) and/or `weekends`; empty = any time |
| `max_events_per_week` | at most this many suggestions in any rolling 7 days |

**Topics:** the description is what the LLM ranks against, so write it the way you'd
explain it to a friend ("skip funding news and hype"). `keywords` are used by the keyword
ranker and in web searches. `weight` (0–1, default 0.5) sets priority. `plugins` limits a
topic to `events` and/or `news` (default: all).

**Dislikes:** a checked phrase removes any item whose title, summary or location contains
it (whole words, any case).

### `sources.md`

One `## <Plugin>` section per content plugin (today: `## Events`), one checkbox per source.
The text after `—` is the source type.

```markdown
## Events
- [x] **web** — web_search
  - max_queries: 3
  - fetch_pages: 2
- [x] **safejourney** — web_page
  - url: https://www.safejourney.pt/eventos/
- [ ] **my-club** — ical
  - url: https://example.com/calendar.ics
```

| Type | What it does | Settings | LLM cost |
|---|---|---|---|
| `ical` | reads a calendar feed (`.ics`); expands recurring events | `url` (web, `file://`, or a path relative to the config folder); `page` (link shown when an event has none) | none |
| `web_page` | fetches a page. If it advertises a calendar feed, that feed is read without the LLM; if it has an RSS/Atom feed (prefers the one for the page's own section), each entry is extracted; otherwise the page text is extracted | `url`; `feed`: `auto` (default), `none` (always read the page itself), or a feed URL | only for new or changed entries/pages |
| `web_search` | one Claude web search per topic, highest weight first; the model may open a few result pages to find dates | `max_queries` (default 3); `fetch_pages` (default 2); `allowed_domains` (comma list); `query` (template with `{topic}`, `{keywords}`, `{city}`, `{start}`, `{end}`) | every run |

Every extracted event must have a date and a link that really appears in the fetched content
or search results; anything else is dropped. Fetched text is treated as untrusted, so
instructions hidden in web pages are ignored. Sites that block robots in `robots.txt` are
not fetched, and each site gets at most one request per second.

### `outputs.md`

```markdown
- [x] **digest** — markdown_digest
  - plugins: events, news
  - path: digests
- [x] **calendar** — google_calendar
  - plugins: events
  - calendar_name: Suggestions
```

Settings for every output: `plugins` (which content goes there; default all) and
`min_score` (only items scored at least this; default 0).

| Type | Publishes | Settings | Feedback |
|---|---|---|---|
| `markdown_digest` | `<path>/YYYY-MM-DD.md`, one section per plugin (*Plan* for events), each item with 👍/👎 boxes | `path` (default `digests`, relative to where you run `agent`) | tick a box |
| `google_calendar` | tentative, *free*-showing events on a calendar the agent creates and owns | `calendar_name` (default `Suggestions`) | still there after it ends = accepted, deleted = rejected |

You can have several outputs of the same type, for example two digests with different
`min_score`.

---

## Google Calendar setup

The agent can only write to a calendar it created itself, and can only see *when* you are
busy on your main calendar, not what the events are. One-time setup:

1. In [Google Cloud Console](https://console.cloud.google.com/), create a project.
2. **APIs & Services → Library:** enable the **Google Calendar API**.
3. **Google Auth Platform (OAuth consent screen):** app name, your email, user type
   *External*, accept the API Services User Data Policy, and add yourself under
   **Audience → Test users**. The app starts in **Testing**:
   - *Testing* works right away, but Google expires the sign-in after **7 days**; run
     `agent auth google` again when it does. Fine for manual runs.
   - For unattended scheduled runs, **publish** it (*Audience → Publish app*). Google only
     allows that once the *Branding* page has public links: at least an app home page and a
     privacy policy. You don't need Google's verification for personal use; you'll see an
     "unverified app" warning when signing in.
4. **Credentials → Create credentials → OAuth client ID → Desktop app.** Download the JSON.
5. Enable the output in `config/outputs.md` (`- [x] **calendar** — google_calendar`), then:

   ```bash
   agent auth google --client-secrets path/to/client_secret.json
   ```

   A browser opens; approve the two permissions (manage calendars the app creates; see
   free/busy). The token is stored in your OS keyring (Windows Credential Manager).
6. Check it works:

   ```bash
   agent outputs test calendar
   ```

   A test event appears tomorrow at 20:00 in the new *Suggestions* calendar, on every device
   where you use Google Calendar. Delete it whenever you like.

   To remove every suggestion the agent has added (events you put in that calendar
   yourself are kept): `agent outputs clear calendar`. Deleting them this way doesn't count
   as rejecting them, and the next run may add back the ones still upcoming and relevant.

Free/busy is used automatically once connected: suggestions that overlap your busy time are
dropped. If you later enable another Google output, run `agent auth google` again; the
agent warns you when a permission is missing.

---

## Choosing an LLM provider

The agent needs an LLM to rank events, read web pages and search the web. You can use your
Claude subscription (the default) or pay per use with a Claude or Gemini API key:

| Provider | Vendor | You need | Billing | Default model |
|---|---|---|---|---|
| `claude_code` (default) | Claude | Claude Code, signed in with a Claude Pro or Max plan | included in your plan | `claude-sonnet-5-5` |
| `anthropic_api` | Claude | an Anthropic API key | pay per use | `claude-opus-5-5` |
| `gemini_api` | Gemini | a Gemini API key from Google AI Studio | pay per use, or the free tier | `gemini-3.8-flash` |
| `gemini_cli` | Gemini | Gemini CLI (`gemini`), signed in with an eligible Google account (plus `PERSONAL_AGENT_GOOGLE_CLOUD_PROJECT` if required) | included in your plan / quota | `gemini-3.8-flash` |

Pick one with `PERSONAL_AGENT_LLM_PROVIDER`; the rest of your setup doesn't change.

**Claude subscription (`claude_code`, default)**
1. Install Claude Code and sign in: run `claude`, then `/login` with your Pro/Max account.
2. That's it. The agent runs `claude -p` with your customizations switched off (no plugins,
   MCP servers or skills, so each call stays small) and only web search and web fetch allowed.
3. Cloud mode: create a token with `claude setup-token` and store it as the repo secret
   `PERSONAL_AGENT_CLAUDE_CODE_OAUTH_TOKEN`.

**Claude API key (`anthropic_api`)**
1. Create a dedicated key, ideally in its own Console workspace with a monthly spend limit.
2. `setx PERSONAL_AGENT_ANTHROPIC_API_KEY "sk-ant-..."` and
   `setx PERSONAL_AGENT_LLM_PROVIDER anthropic_api` (Windows; then open a new terminal; on
   Linux/macOS use `export`).

**Gemini API key (`gemini_api`)**
1. Create a key in [Google AI Studio](https://aistudio.google.com/), preferably in its own
   project with a budget alert in Google Cloud billing.
2. `setx PERSONAL_AGENT_GEMINI_API_KEY "..."` and `setx PERSONAL_AGENT_LLM_PROVIDER gemini_api`.
3. Cloud mode: store the key as the repo secret `PERSONAL_AGENT_GEMINI_API_KEY`.

**Gemini CLI (`gemini_cli`, local only)**
1. Install Gemini CLI (`npm install -g @google/gemini-cli`), run `gemini` once to sign in, and set
   `setx PERSONAL_AGENT_LLM_PROVIDER gemini_cli`.
2. Note: Gemini CLI (0.36 / 0.67) rejects personal Google accounts on the free tier
   (`IneligibleTierError: no longer supported for Gemini Code Assist for individuals`); if your
   account requires a Google Cloud project for Code Assist, set
   `PERSONAL_AGENT_GOOGLE_CLOUD_PROJECT`, or use `gemini_api` instead. Use `gemini_api` in cloud
   mode.

**How they compare**

| | API key providers | Claude subscription (`claude_code`) |
|---|---|---|
| Cost | pay per use, shown after every run | included, within your plan's limits (shared with your own use); the run shows what it would have cost on the API |
| Speed | faster | slower (starts a CLI process per call; ~10 s per call, a full run takes about a minute) |
| Control | exact model and effort, guaranteed JSON shape | model and effort via flags; JSON checked against the schema |
| Cloud mode | yes | yes (token) |

Things to know:
- Each provider reads only its own variable. The agent never uses the generic
  `ANTHROPIC_API_KEY`, `GEMINI_API_KEY` or `GOOGLE_API_KEY`, and subscription providers never
  receive an API key, so a run never quietly turns into paid API use.
- If the chosen provider is unavailable (missing key, CLI logged out, limit reached), the run
  still completes with keyword ranking and without web sources, and tells you why.
- `PERSONAL_AGENT_MODEL` must name a model of the chosen vendor (a Claude model for Claude
  providers, a Gemini model for Gemini providers); leave it unset to use each provider's default.
- Results can differ between vendors; all of them go through the same date and link checks.
- **Data use:** Google may use data from the Gemini API free tier and from consumer Google
  accounts to improve its products; paid API use is treated differently. Check each vendor's
  current terms, and remember that subscriptions used this way are for your own personal agent.

---

## Feedback

The agent learns from what you do with its suggestions. At the start of each run it reads
back, for items delivered in the last 14 days:

| Where | Signal |
|---|---|
| *Suggestions* calendar | event deleted → `rejected`; event still there after it ended → `accepted` |
| Digest | `- [x] 👍` under an item → `up`; `- [x] 👎` → `down` (both ticked = ignored) |
| Command line | `agent rate <id> up` or `down` (the `id:` shown in the digest or event description; the first 6+ characters are enough) |

If you change your mind (untick 👍, tick 👎), the newest signal wins. The latest 20 signals go
into the ranking prompt, so similar items score higher or lower next time. Untouched items
give no signal.

---

## Running every day

**Cloud (GitHub Actions):** the workflow [`.github/workflows/agent.yml`](../.github/workflows/agent.yml)
runs daily at 06:00 UTC and on demand (*Actions → Agent → Run workflow*, optionally as a dry
run). Setup is in the [README](../README.md#getting-started). Each run:

- installs the agent (and Claude Code for the default provider);
- checks out the `state` branch into `state/` and runs there, so `state/agent.db` and
  `state/digests/` are saved back to that branch (config is read from `config/` on main, which
  is git-ignored: commit it in your private copy with `git add -f config/`);
- runs `agent run --explain --fail-on-error` and uploads the output as the `run-log-<n>`
  artifact (items, scores, reasons, cost);
- fails (red, and GitHub emails you) if a plugin, output or feedback sync failed, or the LLM
  provider wasn't available, after saving what did succeed.

From a phone: see suggestions in Google Calendar; on github.com switch to the `state` branch,
open `digests/`, edit the day's file and tick boxes; edit `config/*.md` on main; start a run
from the Actions tab.

**Local (Windows, Linux, macOS):**

```powershell
cd C:\Users\me\personal-agent-data          # the run starts here (digests/ resolve here)
agent --config-dir C:\Git\personal-agent\config schedule install --time 07:00
agent schedule remove
```

`install` registers one daily job: a Task Scheduler task on Windows (starts late if the PC was
off, needs you to be logged in, no console window), a launchd agent on macOS, or a crontab
line elsewhere. Running it again replaces the job. The job runs `agent run` with absolute paths
and `--log-file` (default: the platform log folder, e.g.
`%LOCALAPPDATA%\personal-agent\Logs\agent.log`; printed on install). Options: `--time HH:MM`,
`--workdir DIR` (instead of the current folder); the global `--config-dir`, `--db` and
`--log-file` are passed on to the job.

---

## Commands

Global options go before the command:
`agent [--config-dir DIR] [--db FILE] [--log-file FILE] [-v] <command>`. `--log-file` appends
all output to that file instead of the terminal.

| Command | What it does |
|---|---|
| `agent run` | full run: collect, filter, rank, publish |
| `agent run --dry-run` | same, but writes nothing (no digest, no calendar, no state); prints a preview of each output |
| `agent run --plugin events` | only this content plugin |
| `agent run --output calendar` | only this output |
| `agent run --explain` | also list every ranked item with its score, reason and verdict |
| `agent run --fail-on-error` | exit with code 4 if a plugin, output or feedback sync failed, or no LLM was available (for scheduled runs) |
| `agent run --quiet` | no live step log on stderr while it runs (the summary is still printed) |
| `agent run --max-cost USD` | exit with code 3 if the run's estimated cost was above USD (checked afterwards) |
| `agent plugins` | installed content and output plugins, and whether each is enabled |
| `agent sources test <id>` | fetch one source and list what it returns, before filters |
| `agent outputs test <id>` | publish one sample item to that output |
| `agent outputs clear <id>` | delete everything the agent created there (e.g. all events in *Suggestions*; yours stay); asks first, `--yes` to skip, `--dry-run` to only list |
| `agent auth google [--client-secrets FILE] [--print-token]` | connect Google; `--print-token` prints the token for cloud mode (keep it private) |
| `agent rate <id> up\|down` | give feedback on an item by its id |
| `agent schedule install [--time HH:MM] [--workdir DIR]` | daily run with the OS scheduler ([details](#running-every-day)) |
| `agent schedule remove` | remove it |

Each run prints what was found, filtered and kept, what each output published, the LLM
calls and their estimated cost, and any warnings.

---

## Environment variables

| Variable | Purpose | Default |
|---|---|---|
| `PERSONAL_AGENT_LLM_PROVIDER` | `claude_code`, `anthropic_api`, `gemini_api` or `gemini_cli`; without a working provider, keyword ranking only and web sources are skipped | `claude_code` |
| `PERSONAL_AGENT_ANTHROPIC_API_KEY` | Anthropic key for `anthropic_api` | – |
| `PERSONAL_AGENT_CLAUDE_CODE_OAUTH_TOKEN` | subscription token for `claude_code` in cloud mode; locally the Claude Code login is used | – |
| `PERSONAL_AGENT_GEMINI_API_KEY` | Google AI Studio key for `gemini_api` | – |
| `PERSONAL_AGENT_GOOGLE_CLOUD_PROJECT` | Google Cloud project ID for `gemini_cli` when required by the account | – |
| `PERSONAL_AGENT_MODEL` | model of the chosen vendor for ranking and extraction (a model of another vendor is ignored with a warning) | `claude-sonnet-5-5` (`claude_code`), `claude-opus-5-5` (`anthropic_api`), `gemini-3.8-flash` (`gemini_api`, `gemini_cli`) |
| `PERSONAL_AGENT_EFFORT` | reasoning effort: `low`, `medium`, `high` (mapped to the vendor's control where available) | `low` |
| `PERSONAL_AGENT_CONFIG_DIR` | folder with the three config files | `./config` |
| `PERSONAL_AGENT_DATA_DIR` | folder for `agent.db` (state and caches) | platform data folder, e.g. `%LOCALAPPDATA%\personal-agent` |
| `PERSONAL_AGENT_GOOGLE_CLIENT_SECRETS` | OAuth client file for `agent auth google` | – |
| `PERSONAL_AGENT_GOOGLE_TOKEN_JSON` | Google token for cloud mode (instead of the keyring) | – |

---

## Costs

With the default `claude_code` provider, runs cost nothing extra: they use your Claude plan's
usage limits, and each run prints `included in plan (~$X at API prices)` so you can see how
much of the plan a run uses (a full run with 3 web searches measured ~$0.14 on Sonnet 5.5).

With an API key provider the agent prints the estimated cost of every run. Measured with
Claude Opus 5.5 (`anthropic_api`):

| Step | Typical cost |
|---|---|
| `ical` source | free |
| `web_page` source, unchanged since last run | free (cached) |
| `web_page` source, new entries | about $0.01 per batch |
| ranking (one call per run) | about $0.01–0.03 |
| `web_search`, one query **with** page fetching | about $0.30–0.45 |

Web search is the main cost because opened pages add tens of thousands of tokens. To spend
less: lower `max_queries`, set `fetch_pages: 0` (cheaper, but finds far fewer dated events),
uncheck the `web` source on days you don't need it, or set `PERSONAL_AGENT_MODEL` to a
cheaper model such as `claude-sonnet-5-5`. A spend limit on the key's Console workspace is
the safety net. `gemini_api` (default `gemini-3.8-flash`: $0.75 / $3.75 per million input /
output tokens until 2026-12-31, then double; Google Search grounding free for 5,000 queries a
month, then $0.014 each) is much cheaper per call; its estimate uses those list prices.
Gemini costs per run have not been measured live yet (no key set up). See
[Choosing an LLM provider](#choosing-an-llm-provider).

---

## Where things are stored

| What | Where |
|---|---|
| Your settings | `config/*.md` |
| Digests | `digests/` (or the output's `path`), relative to where the run starts (cloud mode: on the `state` branch) |
| State: seen items, rankings, deliveries, feedback, extraction cache, the Suggestions calendar id | `agent.db` in the data folder (cloud mode: on the `state` branch) |
| Google token | OS keyring, entry `personal-agent / google-token` (cloud mode: repo secret) |
| Scheduled run log | the `--log-file` path; by default the platform log folder (`agent schedule install` prints it) |

Deleting `agent.db` makes the agent forget what it already sent (expect repeats) and
re-extract everything once. Nothing in it is needed by Google: the calendar keeps its events.

---

## Troubleshooting

| Symptom | Fix |
|---|---|
| `No LLM (Claude Code CLI (`claude`) not found on PATH)` | install Claude Code and sign in, or choose another provider |
| `LLM provider unavailable (Claude Code: Not logged in ...)` | run `claude`, then `/login` |
| `LLM provider unavailable (... usage limit reached ...)` | your plan's limit is used up for now; the run used keyword ranking. Try later or switch provider for a day |
| `No LLM (PERSONAL_AGENT_..._API_KEY not set)` | set it (or choose another provider), then **restart the terminal or VS Code** (running programs don't see new variables) |
| `source type 'web_page' ... needs a 'url'` | fill in `url:` (a value of `TBD` counts as missing) |
| `SSL ... trust relationship` / certificate errors | try the `www.` address of the site |
| `robots.txt disallows fetching` | the site opts out of bots; use `web_search` with `allowed_domains` instead |
| A source returns 0 items | run `agent sources test <id>`; for `web_page`, the site may only report past events; for `web_search`, keep `fetch_pages` ≥ 1 |
| `Google is not connected` | run `agent auth google` |
| `Google token lacks ...` | you enabled an output needing a new permission: run `agent auth google` again |
| Sign-in stops working after a week | the app is still in *Testing*: re-run `agent auth google`, or publish it (step 3 of the Google setup) |
| An event shows up again after deleting `agent.db` | expected; the agent lost its memory of what it sent |
| Ticked boxes in a digest aren't picked up | the tick must be `[x]` right under the item's line; only digests from the last 14 days are read; a digest moved out of its folder is ignored |
| Scheduled run didn't happen (Windows) | Task Scheduler → *personal-agent* → *History*; you must be logged in; check the log file |
| Cloud run is red | open the run and download `run-log-<n>`; its end says why (`--fail-on-error`) |
