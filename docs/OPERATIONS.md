# Operations

What to do regularly to keep personal-agent working. For first-time setup, see
[`USAGE.md`](USAGE.md).

> Today you run the agent by hand (`agent run`). Scheduled, unattended runs come in M4; items
> marked *(from M4)* or *(from M3)* only apply once those milestones are built.

## At a glance

| How often | Task | Why |
|---|---|---|
| **Every 7 days** (while the Google app is in Testing) | [Re-run `agent auth google`](#google-sign-in-every-7-days) | Google expires the sign-in of apps in Testing |
| After every run | [Read the warnings](#after-every-run) | Broken sources and expired sign-ins show up there |
| Weekly | [Check LLM spend](#weekly-check-llm-spend) | Web search is the main cost |
| Weekly | [Give feedback](#weekly-give-feedback) | Feedback steers the ranking *(read back from M4)* |
| Monthly | [Review sources and interests](#monthly-review-sources-and-interests) | Sites change, feeds move, interests drift |
| Monthly | [Update the agent and its dependencies](#monthly-update) | Security fixes; API changes |
| Every few months | [Rotate keys](#every-few-months-rotate-keys) | Limits the damage of a leaked key |
| When needed | [Back up or reset state](#state-backup-and-reset) | Moving PCs, or starting fresh |
| Once, before M4 | [Publish the Google app](#once-before-m4-publish-the-google-app) | Ends the weekly re-sign-in |

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
- Cloud mode *(from M4)*: after re-authorizing, run it with `--print-token` and paste the new
  token into the repo secret `PERSONAL_AGENT_GOOGLE_TOKEN_JSON`.

This task goes away once the app is [published](#once-before-m4-publish-the-google-app).

## After every run

Each run ends with a summary and a **Warnings** list. Act on:

| Warning | Action |
|---|---|
| `source '<id>' failed: ...` | `agent sources test <id>`; fix or uncheck the source |
| `robots.txt disallows fetching ...` | uncheck it, or replace it with `web_search` limited to that site (`allowed_domains`) |
| `Google connection failed` / `Google is not connected` | [re-run `agent auth google`](#google-sign-in-every-7-days) |
| `LLM ranking failed ... used keywords` | check the key, the spend limit and the Anthropic status page |
| `PERSONAL_AGENT_ANTHROPIC_API_KEY not set` | the variable is missing in this terminal; restart it, or set the key again |

A run with warnings still completes; nothing needs fixing in a hurry.

## Weekly: check LLM spend

- Each run prints `LLM: ... ~$0.xx`. Web search is the main cost (about $0.30–0.45 per query
  with Opus 5.5); see [USAGE.md → Costs](USAGE.md#costs).
- Once a week, look at the usage of the `personal-agent` key in the
  [Anthropic Console](https://console.anthropic.com/) and confirm it's within the workspace's
  monthly spend limit.
- Too expensive? Lower `max_queries` or `fetch_pages` in `config/sources.md`, uncheck `web` on
  some runs, or switch model or provider (`PERSONAL_AGENT_MODEL`; subscription providers *from
  M3*).

## Weekly: give feedback

- **Calendar:** keep suggestions you like; delete the ones you don't. Delete only in the
  *Suggestions* calendar.
- **Digest:** tick 👍 or 👎 under items in `digests/YYYY-MM-DD.md`.
- *(from M4)* The agent reads these back and uses them in ranking. Until then, feedback is only
  for you, but it's a good habit to start.

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
| Anthropic API key | env var `PERSONAL_AGENT_ANTHROPIC_API_KEY` | create a new key in the Console, `setx` it, restart the terminal, run once, then delete the old key |
| Google OAuth client | `C:\Users\anton\.config\personal-agent\client_secret.json` | rarely needed; to replace it, create a new Desktop client in Google Cloud, save the JSON there, re-run `agent auth google`, delete the old client |
| Google sign-in token | Windows Credential Manager (`personal-agent` / `google-token`) | re-running `agent auth google` replaces it |
| Cloud secrets *(from M4)* | GitHub repo secrets | update them whenever you rotate the local ones |

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
- **Digests:** `digests/` grows by one file per day; delete old ones whenever you like.
- **Suggestions calendar:** delete old events freely. If you delete the whole calendar, the
  agent creates a new one on the next run.

## Once, before M4: publish the Google app

Scheduled runs can't click through a browser every week, so before M4:

1. Have a public **home page** and **privacy policy** URL for the app (for example, a public
   repo with a `PRIVACY.md`).
2. Google Cloud → *Google Auth Platform → Branding*: fill in those links and save.
3. *Audience → Publish app*; confirm.
4. Re-run `agent auth google` once.

After this, the sign-in no longer expires weekly; you'll still see the "unverified app" warning
when signing in, which is fine for personal use.

## From M3: subscription providers

If you use `claude_code` or `gemini_cli`:
- Stay signed in to Claude Code / Gemini CLI on this PC; if a run warns that the CLI is logged
  out, sign in again (`claude` → `/login`, or `gemini`).
- Usage limits are shared with your own use of Claude or Gemini; heavy days may leave the agent
  with keyword ranking only.
- Cloud mode with `claude_code`: refresh `PERSONAL_AGENT_CLAUDE_CODE_OAUTH_TOKEN` when it
  expires (`claude setup-token`).
