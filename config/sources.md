# Sources

One section per content plugin, one checkbox per source. Format: docs/SPEC.md §3.

## Events

- [X] **web** — web_search
  - search: true
  - max_queries: 3   ← one search per topic, highest weight first
  - fetch_pages: 2   ← listing pages opened per search (main cost driver)
- [X] **safejourney** — web_page
  - url: https://www.safejourney.pt/eventos/   ← bare safejourney.pt has a broken TLS certificate
- [X] **lisboa-psychedelic-society** — ical
  - url: https://www.meetup.com/lisboa-psychedelic-society/events/ical/   ← Meetup's iCal feed (free, no LLM)
  - page: https://www.meetup.com/lisboa-psychedelic-society/
- [X] **ecstatic-dance-lisboa** — web_page
  - url: https://ecstaticdance.org/dance/ecstatic-dance-lisboa/   ← recurring monthly (3rd Saturday); no feed, tickets via Eventbrite
  - feed: none
- [ ] **example-ical** — ical
  - url: https://example.com/calendar.ics

## News

Unchecked until the news plugin exists (milestone M5).

- [ ] **hn** — hackernews
- [ ] **arxiv-cl** — rss
  - url: http://export.arxiv.org/rss/cs.CL
- [ ] **gdelt** — gdelt
  - search: true
