# personal-agent

A personal assistant agent that researches **events** and **news** matching your interests,
sends you a digest, and suggests events into Google Calendar.

> **Status:** design stage. The spec is in [`docs/SPEC.md`](docs/SPEC.md); no code yet.

## How it works

A small **kernel** runs the loop. Domain logic lives in **plugins**.

```
interests.md + sources.md ─► kernel ─► events plugin ─► Suggestions calendar ─┐
                                   └─► news plugin ─────────────────────────────┼─► digest
                         ◄──── feedback (kept/deleted suggestions, 👍/👎) ◄───────┘
```

- **Kernel:** reads config, discovers plugins, runs `sync_feedback → collect → dedupe →
  filter → rank → deliver`, and provides shared services (SQLite, LLM ranker, calendar,
  digest).
- **Plugins:** `events` (iCal, Ticketmaster) and `news` (RSS, Hacker News, GDELT). A new
  content type is a new plugin; the kernel doesn't change.
- **Safety:** the agent writes only to a dedicated *Suggestions* calendar, never to your main
  calendar.

## Configuration

Two markdown checklists you edit by hand. A checked box is enabled; uncheck to pause.

`config/interests.md`
```markdown
## Topics
- [x] **jazz** — Small-venue live jazz
  - keywords: jazz, improv
  - plugins: events
```

`config/sources.md`
```markdown
## News
- [x] **arxiv-cl** — rss
  - url: http://export.arxiv.org/rss/cs.CL
```

Full format: [`docs/SPEC.md` §3](docs/SPEC.md#3-configuration-documents).

## Planned CLI

```
agent run [--plugin events|news] [--dry-run]
agent plugins
agent sources test <id>
agent rate <item-id> up|down
```

## Requirements (planned)

- Python 3.12+
- `ANTHROPIC_API_KEY` (optional; without it a keyword ranker is used)
- Google OAuth credentials for Calendar (and Gmail, if the digest is emailed)
- API keys for keyed sources, e.g. `TICKETMASTER_API_KEY`

## Docs

- [`docs/SPEC.md`](docs/SPEC.md): architecture, user stories, config format, acceptance criteria
- [`AGENTS.md`](AGENTS.md): guidance for coding agents working in this repo
