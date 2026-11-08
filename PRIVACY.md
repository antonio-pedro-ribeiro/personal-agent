# Privacy Policy

*Last updated: 2026-11-08*

personal-agent is open-source software that you run yourself, on your own computer or in your
own GitHub Actions. There is no personal-agent server or service: the authors receive none of
your data.

## Google data the app accesses

When you connect a Google account (`agent auth google`), the app asks only for the permissions
needed by the outputs you enabled:

| Permission | Used for |
|---|---|
| `calendar.freebusy` | Reading the busy/free times of your primary calendar, so suggestions don't clash with your plans. Event titles and details are not visible with this permission. |
| `calendar.app.created` | Creating a separate *Suggestions* calendar and adding, reading and deleting suggestion events in it. The app cannot see or change any other calendar. |

Later versions may offer optional outputs that need `tasks` (Google Tasks) or `gmail.send`
(sending the digest to yourself). They are requested only if you turn those outputs on.

## Where data goes

- **Storage:** the Google sign-in token is kept in your operating system's credential store
  (for example Windows Credential Manager), or in a GitHub Actions secret if you choose to run
  it there. Busy times are used during a run and not stored. A local database on your device
  records which suggestions were created and whether you kept or deleted them.
- **No sharing:** Google user data is never sold, shared with third parties, or used for
  advertising. It is not used to train AI models.
- **AI ranking:** to pick suggestions, the app sends public event information (titles, dates,
  venues) and your stated interests to the language model provider you configure (by default
  Claude, through your own Claude account). It does not send your calendar contents. Busy
  times are only used locally to filter out events.

The use of information received from Google APIs follows the
[Google API Services User Data Policy](https://developers.google.com/terms/api-services-user-data-policy),
including the Limited Use requirements.

## Removing access and data

- Revoke access at any time at
  [myaccount.google.com/permissions](https://myaccount.google.com/permissions).
- Delete the local token from your credential store (entry `personal-agent` / `google-token`)
  and the local database (`agent.db` in the app's data folder).
- The *Suggestions* calendar is yours: delete it in Google Calendar whenever you like.

## Contact

Questions: open an issue at
[github.com/antonio-pedro-ribeiro/personal-agent](https://github.com/antonio-pedro-ribeiro/personal-agent/issues).
