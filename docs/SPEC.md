# Personal Agent — Spec v1

A personal agent that finds **events** and **news** matching my interests, delivers a
digest, and suggests events into Google Calendar.

## 1. Architecture

One script, three steps, two config files, one SQLite file.

```
interests.yaml ─┐
sources.yaml  ──┼─► 1. COLLECT ─► 2. SELECT (LLM) ─► 3. DELIVER ─► digest (email/markdown)
feedback      ──┘                                              └─► "Suggestions" calendar
                                                                         │
            ◄──────── feedback: kept / deleted suggestions, 👍/👎 news ────┘
```

| Step | What it does | LLM? |
|---|---|---|
| **Collect** | Fetch every enabled source. Search-capable sources get interest keywords. Normalize to `Item`. Drop items already seen (URL hash). | No |
| **Select** | Hard filters (date, distance, price, free/busy). Then one batched LLM call per kind: score, merge duplicates, write a 1–2 line reason/summary with link. | Yes |
| **Deliver** | Top events → tentative entries on a dedicated **Suggestions** calendar. Top news + events → one digest. | No |

**Feedback without a UI:** a suggestion still on the Suggestions calendar at event time (or
moved to main calendar) = accepted; deleted = rejected. News is rated from the CLI. Recent
feedback is fed back into the Select prompt as examples.

**Not in v1:** watches (news → event tracking), embedding clustering, chat bot, web UI,
auto-editing interests.

## 2. User stories

### Interests
- **US-1** As a user, I edit `interests.yaml` to define topics (description, keywords, weight,
  kinds: event/news) so the agent knows what I care about.
- **US-2** As a user, I set constraints (city, max distance, max price, time windows, max events
  per week) so I never see events I can't or won't attend.
- **US-3** As a user, I list dislikes so matching items are excluded.

### Sources
- **US-4** As a user, I add/disable sources in `sources.yaml` without touching interests.
- **US-5** As a user, I run `agent sources test <id>` to see what a source returns.

### Events
- **US-6** As a user, I get up to N suggested events per week that fit my free time, each with
  a one-line reason it matches.
- **US-7** As a user, suggested events appear on a separate Suggestions calendar, never on my
  main calendar, so the agent cannot clutter my agenda.
- **US-8** As a user, I accept a suggestion by keeping/moving it and reject by deleting it.

### News
- **US-9** As a user, I get a daily digest of the top ≤10 news items for my interests, each with
  a short summary and source link.
- **US-10** As a user, the same story from multiple outlets appears once.
- **US-11** As a user, I rate news with `agent rate <id> up|down`.

### Learning & ops
- **US-12** As a user, my past ratings influence future selections.
- **US-13** As a user, the agent runs on a schedule (news daily, events weekly) without me.
- **US-14** As a user, I can run `agent run --dry-run` to see the digest without writing
  to the calendar or sending email.

## 3. Data

### `config/interests.yaml`
```yaml
location: { city: Lisbon, lat: 38.72, lon: -9.14, max_km: 15 }
constraints:
  max_price: 60
  time_windows: [weekday_evenings, weekends]   # weekday_evenings = Mon–Fri 18:00–24:00
  max_events_per_week: 3
interests:
  - id: ai
    description: "Technical AI/ML; skip funding and hype"
    keywords: [LLM, machine learning]
    weight: 0.8          # 0–1
    kinds: [event, news] # default both
  - id: jazz
    description: "Small-venue live jazz"
    keywords: [jazz]
    weight: 0.9
    kinds: [event]
dislikes: [networking mixers, tribute bands]
news: { max_items: 10 }
```

### `config/sources.yaml`
```yaml
sources:
  - id: ticketmaster
    kind: event
    type: ticketmaster      # adapter
    search: true            # receives interest keywords
    env: TICKETMASTER_API_KEY
  - id: venue-x
    kind: event
    type: ical
    url: https://example.com/cal.ics
  - id: hn
    kind: news
    type: hackernews
  - id: arxiv-cl
    kind: news
    type: rss
    url: http://export.arxiv.org/rss/cs.CL
```

### `Item` (normalized)
| Field | Type | Notes |
|---|---|---|
| id | str | sha1(url) |
| kind | `event` \| `news` | |
| title, url, source_id | str | |
| summary | str | raw text, truncated to 1,000 chars |
| published_at | datetime | news |
| start, end | datetime | events |
| venue, lat, lon, price | optional | events |

### SQLite tables
- `items(id, kind, json, first_seen)` — seen items, dedupe
- `selections(item_id, run_at, score, reason, calendar_event_id)`
- `feedback(item_id, signal ∈ {up, down, accepted, rejected}, at)`

## 4. Behavior

**Collect**
- Sources with `search: true` get one query per matching interest keyword set; others fetch all.
- Per-source timeout 20 s; a failing source is logged and skipped, never aborts the run.

**Select**
- Hard filters first (deterministic): event in next 14 days, within `max_km`, ≤ `max_price`,
  inside time windows, no overlap with main-calendar busy slots (Google `freeBusy`),
  no dislike keyword match. News: published in last 48 h.
- LLM input: interests, dislikes, last 20 feedback examples, candidate batch (≤ 50 items).
  LLM output (JSON, schema-validated): `[{id, score 0–100, reason, duplicate_of?}]`.
- Keep events: top `max_events_per_week` with score ≥ 60. Keep news: top `news.max_items`
  with score ≥ 50, duplicates collapsed into the canonical item (other links listed).

**Deliver**
- Create tentative events on the Suggestions calendar (created on first run if missing);
  description holds reason + URL + `agent-id:<item id>`.
- Digest: markdown file in `out/` and optional email via Gmail API. Sections: *Plan* (events),
  *Read* (news).

**Feedback sync** (start of each run)
- For past suggestions: still present or copied to main calendar → `accepted`; missing →
  `rejected`.

## 5. CLI
```
agent run [--events] [--news] [--dry-run]
agent sources test <id>
agent rate <item-id> up|down
```

## 6. Stack
Python 3.12 · Anthropic SDK (model configurable via env) · google-api-python-client
(Calendar, Gmail) · feedparser · httpx · pydantic · PyYAML · SQLite · cron.

## 7. Acceptance criteria (v1 done)
1. `agent run --dry-run` with 1 event source + 2 news sources produces a digest with both sections.
2. No suggestion overlaps a busy slot or violates a constraint (unit-tested filters).
3. Nothing is ever written to the main calendar.
4. A source failure does not fail the run.
5. Deleting a suggestion is recorded as `rejected` on the next run and appears in the next
   Select prompt.
6. Cost per daily run < $0.10 at ≤ 200 candidates.

## 8. Open questions
- Digest channel: email vs. markdown only?
- Home city and initial interests.
- Run location: local cron vs. small VM vs. GitHub Actions (needs persisted SQLite).
