# Usage

How to install, configure and run personal-agent. For the design, see [`SPEC.md`](SPEC.md);
for what is built and what is next, see [`PLAN.md`](PLAN.md).

> **What works today (M1 + M2):** events from iCal, web pages and web search; LLM ranking
> with an API key; a markdown digest; suggestions in Google Calendar. **Not yet:** running on
> a Claude subscription (M3), reading back your feedback and scheduled runs (M4), news,
> Obsidian, Google Tasks and email (M5).

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

**3. Add your Anthropic API key** so the agent can search the web and rank with an LLM.
Use a dedicated key, ideally in its own Console workspace with a monthly spend limit:

```powershell
setx PERSONAL_AGENT_ANTHROPIC_API_KEY "sk-ant-..."   # Windows; then open a new terminal
```
```bash
export PERSONAL_AGENT_ANTHROPIC_API_KEY="sk-ant-..."  # Linux/macOS
```

The agent reads only this variable, never the generic `ANTHROPIC_API_KEY`. Prefer your
Claude Pro/Max subscription instead? That arrives in M3; see
[Using a Claude subscription](#using-a-claude-subscription-planned-m3).

**4. Describe what you like** in [`config/interests.md`](../config/interests.md) (city,
budget, topics) and check which sources are on in [`config/sources.md`](../config/sources.md).

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

---

## How a run works

```
interests.md ─┐
sources.md ───┼─► collect ─► dedupe ─► filter ─► rank ─► route ─► outputs
outputs.md ───┘
```

1. **Collect:** every checked source is fetched.
2. **Dedupe:** items already delivered to every output are skipped, so they cost nothing.
3. **Filter:** deterministic rules (dates, distance, price, time windows, busy slots,
   dislikes). No LLM.
4. **Rank:** the LLM scores what is left from 0 to 100 against your topics and writes a
   one-line reason. Without a key, a keyword matcher is used instead.
5. **Route and publish:** each checked output gets the items routed to it, at most once.

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

| Type | Publishes | Settings | Feedback (M4) |
|---|---|---|---|
| `markdown_digest` | `<path>/YYYY-MM-DD.md`, one section per plugin (*Plan* for events), each item with 👍/👎 boxes | `path` (default `digests`, relative to where you run `agent`) | tick a box |
| `google_calendar` | tentative, *free*-showing events on a calendar the agent creates and owns | `calendar_name` (default `Suggestions`) | keep = accept, delete = reject |

You can have several outputs of the same type, for example two digests with different
`min_score`.

---

## Google Calendar setup

The agent can only write to a calendar it created itself, and can only see *when* you are
busy on your main calendar, not what the events are. One-time setup:

1. In [Google Cloud Console](https://console.cloud.google.com/), create a project.
2. **APIs & Services → Library:** enable the **Google Calendar API**.
3. **APIs & Services → OAuth consent screen:** user type *External*, add yourself as a test
   user, then click **Publish app** so the status is **In production**. You don't need
   Google's verification for personal use; you'll see an "unverified app" warning once.
   Leaving it in *Testing* makes Google expire the sign-in after 7 days.
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

Free/busy is used automatically once connected: suggestions that overlap your busy time are
dropped. If you later enable another Google output, run `agent auth google` again; the
agent warns you when a permission is missing.

---

## Using a Claude subscription (planned, M3)

> Not built yet. This section describes how it will work.

Instead of a pay-per-use API key, the agent will be able to do its LLM work (ranking,
extraction, web search) through **Claude Code** on your Claude Pro or Max plan:

1. Install Claude Code and sign in with your subscription: run `claude`, then `/login`.
2. Set the provider:

   ```powershell
   setx PERSONAL_AGENT_LLM_PROVIDER claude_code
   ```

3. Run as usual: `agent run --dry-run`.

For cloud mode (GitHub Actions), create a long-lived token with `claude setup-token` and store
it as the repo secret `PERSONAL_AGENT_CLAUDE_CODE_OAUTH_TOKEN`.

| | API key (`anthropic_api`, default) | Subscription (`claude_code`) |
|---|---|---|
| Cost | pay per use, shown per run | included in your plan |
| Limits | the spend limit you set | your plan's usage limits, shared with your own Claude use |
| Speed | faster | slower (starts a Claude Code process per call) |
| Control | exact model, effort and output format | Claude Code's settings; output validated afterwards |

With `claude_code`, your API key is ignored and never used, so a run can't quietly turn into
paid API usage. If Claude Code is missing, logged out or out of usage, the run still
completes with keyword ranking and without web sources, and tells you why. Using your
subscription this way is meant for your own personal agent only; check Anthropic's current
terms.

---

## Commands

Global options go before the command: `agent [--config-dir DIR] [--db FILE] [-v] <command>`.

| Command | What it does |
|---|---|
| `agent run` | full run: collect, filter, rank, publish |
| `agent run --dry-run` | same, but writes nothing (no digest, no calendar, no state); prints a preview of each output |
| `agent run --plugin events` | only this content plugin |
| `agent run --output calendar` | only this output |
| `agent plugins` | installed content and output plugins, and whether each is enabled |
| `agent sources test <id>` | fetch one source and list what it returns, before filters |
| `agent outputs test <id>` | publish one sample item to that output |
| `agent auth google [--client-secrets FILE] [--print-token]` | connect Google; `--print-token` prints the token for cloud mode (keep it private) |

Each run prints what was found, filtered and kept, what each output published, the LLM
calls and their estimated cost, and any warnings.

---

## Environment variables

| Variable | Purpose | Default |
|---|---|---|
| `PERSONAL_AGENT_LLM_PROVIDER` | `anthropic_api` or `claude_code` (subscription, from M3) | `anthropic_api` |
| `PERSONAL_AGENT_ANTHROPIC_API_KEY` | Anthropic key for `anthropic_api`; without it, keyword ranking only and web sources are skipped | – |
| `PERSONAL_AGENT_CLAUDE_CODE_OAUTH_TOKEN` | subscription token for `claude_code` in cloud mode (from M3); locally the Claude Code login is used | – |
| `PERSONAL_AGENT_MODEL` | Claude model for ranking and extraction | `claude-opus-5-5` |
| `PERSONAL_AGENT_EFFORT` | reasoning effort: `low`, `medium`, `high` | `low` |
| `PERSONAL_AGENT_CONFIG_DIR` | folder with the three config files | `./config` |
| `PERSONAL_AGENT_DATA_DIR` | folder for `agent.db` (state and caches) | platform data folder, e.g. `%LOCALAPPDATA%\personal-agent` |
| `PERSONAL_AGENT_GOOGLE_CLIENT_SECRETS` | OAuth client file for `agent auth google` | – |
| `PERSONAL_AGENT_GOOGLE_TOKEN_JSON` | Google token for cloud mode (instead of the keyring) | – |

---

## Costs

The agent prints the estimated cost of every run. Measured with Claude Opus 5.5:

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
the safety net. From M3 you can also run on your Claude subscription instead
(`PERSONAL_AGENT_LLM_PROVIDER=claude_code`), within your plan's usage limits.

---

## Where things are stored

| What | Where |
|---|---|
| Your settings | `config/*.md` |
| Digests | `digests/` (or the output's `path`) |
| State: seen items, rankings, deliveries, extraction cache, the Suggestions calendar id | `agent.db` in the data folder |
| Google token | OS keyring, entry `personal-agent / google-token` |

Deleting `agent.db` makes the agent forget what it already sent (expect repeats) and
re-extract everything once. Nothing in it is needed by Google: the calendar keeps its events.

---

## Troubleshooting

| Symptom | Fix |
|---|---|
| `PERSONAL_AGENT_ANTHROPIC_API_KEY not set` | set it, then **restart the terminal or VS Code** (running programs don't see new variables) |
| `source type 'web_page' ... needs a 'url'` | fill in `url:` (a value of `TBD` counts as missing) |
| `SSL ... trust relationship` / certificate errors | try the `www.` address of the site |
| `robots.txt disallows fetching` | the site opts out of bots; use `web_search` with `allowed_domains` instead |
| A source returns 0 items | run `agent sources test <id>`; for `web_page`, the site may only report past events; for `web_search`, keep `fetch_pages` ≥ 1 |
| `Google is not connected` | run `agent auth google` |
| `Google token lacks ...` | you enabled an output needing a new permission: run `agent auth google` again |
| Sign-in stops working after a week | the consent screen is still in *Testing*; publish it (step 3 of the Google setup) and re-run `agent auth google` |
| An event shows up again after deleting `agent.db` | expected; the agent lost its memory of what it sent |
