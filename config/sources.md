# Sources

One section per content plugin, one checkbox per source. Format: docs/SPEC.md §3.

## Events
- [x] **web** — web_search
  - search: true
  - max_queries: 3   ← one search per topic, highest weight first
  - fetch_pages: 2   ← listing pages opened per search (main cost driver)
- [x] **safejourney** — web_page
  - url: https://www.safejourney.pt/eventos/   ← bare safejourney.pt has a broken TLS certificate
- [ ] **alltera-tribe** — web_page
  - url: TBD   ← confirm the official site or event page
- [ ] **example-ical** — ical
  - url: https://example.com/calendar.ics
