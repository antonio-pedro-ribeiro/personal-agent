# Operations

What to do regularly to keep personal-agent working. For first-time setup, see
[`USAGE.md`](USAGE.md).

> The agent runs by itself every day, in GitHub Actions (cloud mode) or with
> `agent schedule install` (local mode); see [USAGE → Running every day](USAGE.md#running-every-day).
> You can still run it by hand with `agent run`.

## At a glance

| How often | Task | Why |
|---|---|---|
| **Every 7 days** (while the Google app is in Testing) | [Re-run `agent auth google`](#google-sign-in-every-7-days) | Google expires the sign-in of apps in Testing |
| After every run | [Read the warnings](#after-every-run) | Broken sources and expired sign-ins show up there |
| Daily (glance) | [Check the scheduled run](#daily-check-the-scheduled-run) | A red cloud run or a stale log means no suggestions |
| Weekly | [Check LLM spend](#weekly-check-llm-spend) | API providers: web search is the main cost; `claude_code`: plan limits |
| Weekly | [Give feedback](#weekly-give-feedback) | Feedback steers the ranking |
| Monthly | [Review sources and interests](#monthly-review-sources-and-interests) | Sites change, feeds move, interests drift |
| Monthly | [Update the agent and its dependencies](#monthly-update) | Security fixes; API changes |
| Every few months | [Rotate keys](#every-few-months-rotate-keys) | Limits the damage of a leaked key |
| When needed | [Back up or reset state](#state-backup-and-reset) | Moving PCs, or starting fresh |
| Once, before scheduling | [Publish the Google app](#once-publish-the-google-app) | Ends the weekly re-sign-in |

---

## Google sign-in: every 7 days

The `personal-agent` app in Google Cloud is in **Testing**, so Google expires its sign-in
7 days after you approve it.

**Signs it expired:** a run warns `Google connection failed` or mentions `invalid_grant`, the
calendar output reports an error, or no busy slots are checked.

**Fix:**

```powershell
agent auth google --client-secrets C:\Users\anton\.config\personal-agent\client_secret.json
```

Approve in the browser (click past "Google hasn't verified this app"), then check with:

```powershell
agent outputs test calendar
```

Tips:
- Set `PERSONAL_AGENT_GOOGLE_CLIENT_SECRETS` to that path once, and `agent auth google` alone is
  enough.
- Do it on a fixed day, e.g. when you plan your week; or add a weekly reminder in your calendar.
- Also re-run it whenever you enable another Google output; the agent warns
  `Google token lacks ...` when a permission is missing.
- Cloud mode: after re-authorizing, run it with `--print-token` and paste the new
  token into the repo secret `PERSONAL_AGENT_GOOGLE_TOKEN_JSON`.

This task goes away once the app is [published](#once-publish-the-google-app).

## After every run

Each run ends with a summary and a **Warnings** list. Act on:

| Warning | Action |
|---|---|
| `source '<id>' failed: ...` | `agent sources test <id>`; fix or uncheck the source |
| `robots.txt disallows fetching ...` | uncheck it, or replace it with `web_search` limited to that site (`allowed_domains`) |
| `Google connection failed` / `Google is not connected` | [re-run `agent auth google`](#google-sign-in-every-7-days) |
| `LLM ranking failed ... used keywords` | `claude_code`: check you are signed in and within your plan's limits; API providers: check the key, the spend limit and the vendor's status page |
| `LLM provider unavailable (... Not logged in ...)` | run `claude`, then `/login` |
| `No LLM (... not set)` / `No LLM (... not found on PATH)` | the provider's key is missing in this terminal (restart it, or set the key again), or the CLI isn't installed |

A run with warnings still completes; nothing needs fixing in a hurry.

## Daily: check the scheduled run

- **Cloud:** GitHub emails you when the *Agent* workflow fails. Open the run, download the
  `run-log-<n>` artifact and read the end: `--fail-on-error` lists what failed (an output, a
  feedback sync, or no working LLM). State that did succeed is still saved to the `state`
  branch, so a re-run (*Run workflow*) doesn't duplicate anything.
- **Local:** look at the end of the log file (`agent schedule install` printed its path; on
  Windows by default `%LOCALAPPDATA%\personal-agent\Logs\agent.log`). No new `===` block today
  means the task didn't run: check Task Scheduler → *personal-agent* → *History*.

## Weekly: check LLM spend

- With the default `claude_code` provider there is no bill: each run prints
  `included in plan (~$X at API prices)`, and the cost is your plan's usage limits (see below).
- With an API key provider each run prints `LLM: ... ~$0.xx`. Web search is the main cost
  (about $0.30–0.45 per query with Opus 5.5); see [USAGE.md → Costs](USAGE.md#costs). Once a
  week, check the key's usage in the [Anthropic Console](https://console.anthropic.com/) or
  Google AI Studio and confirm it's within the monthly spend limit or budget alert.
- Too expensive or too slow? Lower `max_queries` or `fetch_pages` in `config/sources.md`,
  uncheck `web` on some runs, or switch model or provider (`PERSONAL_AGENT_MODEL`,
  `PERSONAL_AGENT_LLM_PROVIDER`).

## Weekly: give feedback

- **Calendar:** keep suggestions you like; delete the ones you don't. Delete only in the
  *Suggestions* calendar.
- **Digest:** tick 👍 or 👎 under items in `digests/YYYY-MM-DD.md`.
- Cloud mode: the digests are on the `state` branch on github.com; edit the file there (also
  from a phone browser) and tick the box.
- Or from a terminal: `agent rate <id> up|down` with the `id:` from the digest or event.
- The next run reads these back (items from the last 14 days) and the latest 20 signals steer
  the ranking. See [USAGE → Feedback](USAGE.md#feedback).

## Monthly: review sources and interests

- `agent sources test <id>` for each checked source: does it still return items?
- Sources that keep returning 0 events: uncheck them, or point `url` at the site's events page.
- Meetup groups: use the group's iCal feed (`https://www.meetup.com/<group>/events/ical/`, type
  `ical`); it's free and exact.
- Update topics, weights and dislikes in `config/interests.md` (for example, after deleting many
  suggestions of one kind).
- New feeds or venues: add them to `config/sources.md` (an `.ics` or RSS feed is free; a page
  without a feed costs a little per change).

## Monthly: update

```powershell
cd C:\Git\personal-agent
git pull
python -m pip install -e ".[dev]" --upgrade
python -m pytest -q
agent run --dry-run
```

If tests or the dry run fail after an update, go back with `git checkout <previous commit>` and
reinstall.

## Every few months: rotate keys

| Credential | Where it lives | How to rotate |
|---|---|---|
| Claude Code cloud token | repo secret `PERSONAL_AGENT_CLAUDE_CODE_OAUTH_TOKEN` | `claude setup-token`, update the secret |
| Anthropic / Gemini API key (if used) | env var `PERSONAL_AGENT_ANTHROPIC_API_KEY` / `PERSONAL_AGENT_GEMINI_API_KEY` | create a new key in the Console, `setx` it, restart the terminal, run once, then delete the old key |
| Google OAuth client | `C:\Users\anton\.config\personal-agent\client_secret.json` | rarely needed; to replace it, create a new Desktop client in Google Cloud, save the JSON there, re-run `agent auth google`, delete the old client |
| Google sign-in token | Windows Credential Manager (`personal-agent` / `google-token`) | re-running `agent auth google` replaces it |
| Cloud secrets | GitHub repo secrets | update them whenever you rotate the local ones |

Rotate immediately if a key may have leaked (pasted somewhere, committed, shared screen).

To disconnect Google completely: remove access at
[myaccount.google.com/permissions](https://myaccount.google.com/permissions) and delete the
`personal-agent` entry in Windows Credential Manager.

## State: backup and reset

The agent's memory is one file: `agent.db` in `%LOCALAPPDATA%\personal-agent\` (or
`PERSONAL_AGENT_DATA_DIR`).

- **Backup / move PC:** copy `agent.db` and your `config/` folder. Without `agent.db`, the agent
  forgets what it already sent and suggests things again.
- **Reset:** delete `agent.db`. Next run re-extracts every page once (a little LLM cost) and may
  repeat suggestions. Calendar events already created stay in Google.
- **Digests:** `digests/` grows by one file per day; delete old ones whenever you like (feedback
  is only read from the last 14 days).
- **Cloud mode:** state is `agent.db` and `digests/` on the `state` branch. Back up by fetching
  that branch. Reset by deleting the branch (`git push origin --delete state`); the next run
  starts a fresh one.
- **Suggestions calendar:** delete old events freely. If you delete the whole calendar, the
  agent creates a new one on the next run.

## Once: publish the Google app

Scheduled runs can't click through a browser every week, so before scheduling:

1. Have a public **home page** and **privacy policy** URL for the app (for example, a public
   repo with a `PRIVACY.md`).
2. Google Cloud → *Google Auth Platform → Branding*: fill in those links and save.
3. *Audience → Publish app*; confirm.
4. Re-run `agent auth google` once.

After this, the sign-in no longer expires weekly; you'll still see the "unverified app" warning
when signing in, which is fine for personal use.

## Claude subscription (`claude_code`, the default)

- Stay signed in to Claude Code on this PC; if a run warns that the CLI is logged out, sign in
  again (`claude` → `/login`).
- Usage limits are shared with your own use of Claude; heavy days may leave the agent with
  keyword ranking only (the run warns and still completes).
- After a Claude Code update, run `agent run --dry-run` once: if its output format changed, the
  run warns instead of ranking.
- Cloud mode with `claude_code`: refresh `PERSONAL_AGENT_CLAUDE_CODE_OAUTH_TOKEN` when it
  expires (`claude setup-token`).
