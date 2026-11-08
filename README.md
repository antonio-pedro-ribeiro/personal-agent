# personal-agent

A personal assistant agent that researches **events** and **news** matching your interests
and publishes them where you already work: Google Calendar, Google Tasks, an Obsidian vault,
a markdown digest, or email.

> **Status:** milestone M1 (foundation) done: kernel, markdown digest output, events from
> iCal, offline. Web sources, LLM ranking and Google Calendar come in M2
> ([`docs/PLAN.md`](docs/PLAN.md)).

## Try it

```bash
python -m pip install -e ".[dev]"      # Python 3.12+, Windows/Linux/macOS
agent --config-dir examples/demo run --dry-run   # offline demo, writes nothing
agent --config-dir examples/demo sources test demo-venues
agent plugins
pytest                                  # tests (also: ruff check ., mypy, lint-imports)
```

The demo reads a local iCal file and shows the filters at work: a disliked "networking
mixers" night, a Monday-morning workshop (outside your time windows), a Porto event (too
far) and a €150 gala (over budget) are dropped. Without `--dry-run`, the digest is written to
`out/digests/YYYY-MM-DD.md`, and a second run adds nothing new.

Your own setup lives in `config/` (`AGENT_CONFIG_DIR` to move it). State is kept in the
platform data dir (`AGENT_DATA_DIR`, or `--db`).

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
  generic **web search** and **web page** adapters for sites without a feed (starting with
  Safejourney.pt and AllTera Tribe).
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

## CLI (planned commands marked *)

```
agent run [--plugin events|news] [--output <id>] [--dry-run]
agent plugins
agent sources test <id>
agent outputs test <id>          *
agent rate <item-id> up|down     *
agent auth google                *
agent schedule install|remove    *
```

## Requirements (planned)

- Python 3.12+ on Windows, Linux or macOS (or just a private GitHub repo for cloud mode)
- `ANTHROPIC_API_KEY` (needed for web search / web page sources and LLM ranking; without it,
  only feed sources and a keyword ranker work)
- Google OAuth credentials, if a Google output is enabled

## Docs

- [`docs/SPEC.md`](docs/SPEC.md): architecture, user stories, config format, acceptance criteria
- [`docs/PLAN.md`](docs/PLAN.md): implementation milestones (events first, then news)
- [`AGENTS.md`](AGENTS.md): guidance for coding agents working in this repo
