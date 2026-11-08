# personal-agent

A personal assistant agent that researches **events** and **news** matching your interests
and publishes them where you already work: Google Calendar, Google Tasks, an Obsidian vault,
a markdown digest, or email.

> **Status:** M1–M4 built: events from iCal, web pages and web search, LLM ranking on a
> Claude Pro/Max plan (default, via Claude Code) or a Claude / Gemini API key, a markdown
> digest and Google Calendar suggestions, feedback that steers the ranking, and daily runs in
> GitHub Actions or the Windows Task Scheduler. Next: news and more outputs (M5)
> ([`docs/PLAN.md`](docs/PLAN.md)). **How to use it: [`docs/USAGE.md`](docs/USAGE.md).**

## Try it

```bash
python -m pip install -e ".[dev]"      # Python 3.12+, Windows/Linux/macOS
agent --config-dir examples/demo run --dry-run   # offline demo, writes nothing
agent --config-dir examples/demo sources test demo-venues
agent plugins
python -m pip install -e ".[dev]" && pytest   # tests (also: ruff check ., mypy, lint-imports)
pre-commit install   # one-time: blocks commits that fail lint/type-check/import rules
```

The demo reads a local iCal file and shows the filters at work: a disliked "networking
mixers" night, a Monday-morning workshop (outside your time windows), a Porto event (too
far) and a €150 gala (over budget) are dropped. Without `--dry-run`, the digest is written to
`out/digests/YYYY-MM-DD.md`, and a second run adds nothing new.

Your own setup lives in `config/` (`PERSONAL_AGENT_CONFIG_DIR` to move it). State is kept in the
platform data dir (`PERSONAL_AGENT_DATA_DIR`, or `--db`).

## Getting started

First set it up and run it by hand ([`docs/USAGE.md`](docs/USAGE.md) quickstart: install,
`config/`, `agent auth google`, `agent run --dry-run`, `agent run`). Then pick how it runs
every day:

**Cloud mode** (GitHub Actions; none of your devices needs to stay on)

1. Keep this repository **private** (it holds your interests, digests and state).
2. Publish the Google app so the sign-in doesn't expire weekly
   ([`docs/OPERATIONS.md`](docs/OPERATIONS.md#once-publish-the-google-app)), then run
   `agent auth google --print-token`.
3. Add repo secrets (*Settings → Secrets and variables → Actions*):
   `PERSONAL_AGENT_GOOGLE_TOKEN_JSON` (the printed token) and, for the default provider,
   `PERSONAL_AGENT_CLAUDE_CODE_OAUTH_TOKEN` (from `claude setup-token`). For an API provider, set
   the repo variable `PERSONAL_AGENT_LLM_PROVIDER` and that provider's key secret instead.
4. *Actions → Agent → Run workflow* once to check it. After that it runs daily at 06:00 UTC.

Results: the *Suggestions* calendar in Google Calendar, and digests in `digests/` on the
`state` branch (tick 👍/👎 there on github.com). Config stays in `config/` on the main branch.

**Windows local mode** (Task Scheduler; if the PC is off at the set time, the run starts when
it's back on)

```powershell
pipx install C:\Git\personal-agent        # or: python -m pip install -e .
cd C:\Users\me\personal-agent-data        # the run starts here; digests/ are written here
agent --config-dir C:\Git\personal-agent\config schedule install --time 07:00
agent schedule remove                     # to stop
```

Linux and macOS use the same command (cron / launchd). Output goes to a log file, whose path
`agent schedule install` prints.

## How it works

A small **kernel** runs the loop. Everything else is a plugin.

```
interests.md  sources.md                         outputs.md
      │           │                                   │
      ▼           ▼                                   ▼
 content plugins ──► dedupe · filter · rank ──► route ──► output plugins
 (events, news)                                           (Google Calendar, Google Tasks,
                                                           Obsidian, digest, email)
                         ◄────────── feedback ◄──────────┘
```

- **Kernel:** reads config, discovers plugins, ranks items with an LLM, routes them to
  outputs, and collects feedback. It knows nothing about events, news, calendars or notes.
- **Content plugins:** `events` and `news`. Sources: iCal, RSS, Hacker News, GDELT, plus
  generic **web search** and **web page** adapters for sites without a feed. Your sources
  are listed in `config/sources.md`.
- **Output plugins:** `google_calendar`, `google_tasks`, `obsidian`, `markdown_digest`,
  `email`. Each also reads back your feedback: deleted calendar entry, completed task, ticked
  👍/👎 box.
- **Safety:** each output writes only inside its own calendar, task list or folder. The
  Google token can't modify your main calendar (`calendar.app.created`, `calendar.freebusy`).

## Windows and Android

The agent runs headless, by default on a schedule in GitHub Actions, so no device needs to
stay on. Windows can also run it locally (`pipx` + Task Scheduler). On Android you use apps you
already have: Google Calendar, Google Tasks, Obsidian, Gmail, a browser. Config files can live
in your Obsidian vault so you edit them on the phone.

Running the agent itself on Android (Termux) is not supported: background scheduling is
unreliable there. Details: [`docs/SPEC.md`](docs/SPEC.md#running-on-windows-and-android).

## Configuration

Three markdown checklists. A checked box is enabled; uncheck to pause.

`interests.md`
```markdown
## Topics
- [x] **jazz** — Small-venue live jazz
  - keywords: jazz, improv
  - plugins: events
```

`sources.md`
```markdown
## News
- [x] **arxiv-cl** — rss
  - url: http://export.arxiv.org/rss/cs.CL
```

`outputs.md`
```markdown
- [x] **calendar** — google_calendar
  - plugins: events
- [x] **vault** — obsidian
  - plugins: events, news
  - vault_path: C:\Users\me\Obsidian\Main
```

Full format: [`docs/SPEC.md` §3](docs/SPEC.md#3-configuration-documents).

## CLI

```
agent run [--plugin events|news] [--output <id>] [--dry-run] [--explain] [--fail-on-error]
agent plugins
agent sources test <id>
agent outputs test <id>
agent rate <item-id> up|down
agent auth google [--print-token]
agent schedule install [--time HH:MM] | remove
```

## Requirements

- Python 3.12+ on Windows, Linux or macOS (or just a private GitHub repo for cloud mode)
- An LLM for web search / web page sources and ranking (without one, only feed sources and a
  keyword ranker work): by default Claude Code signed in with a Claude Pro/Max plan; or a Claude
  or Gemini API key or Gemini CLI (`PERSONAL_AGENT_LLM_PROVIDER` = `anthropic_api` / `gemini_api` / `gemini_cli`)
- Google OAuth credentials, if a Google output is enabled

## Docs

- [`docs/USAGE.md`](docs/USAGE.md): quickstart, configuration reference, commands, costs, troubleshooting
- [`docs/OPERATIONS.md`](docs/OPERATIONS.md): recurring tasks (Google re-sign-in, spend checks, key rotation, backups)
- [`docs/SPEC.md`](docs/SPEC.md): architecture, user stories, config format, acceptance criteria
- [`docs/PLAN.md`](docs/PLAN.md): implementation milestones (events first, then news)
- [`AGENTS.md`](AGENTS.md): guidance for coding agents working in this repo
