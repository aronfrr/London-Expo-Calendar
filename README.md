# London-Expo-Calendar

This project publishes a calendar of trade shows, conferences, and expos taking
place in London over the next three months. A scheduled GitHub Action runs every
Monday morning to:

1. Look up sector-specific Google Custom Search queries derived from
   `data/industry_sectors.yaml`.
2. Merge any newly discovered events into `events.json`, updating existing
   entries when dates shift.
3. Refresh exhibitor lists (when available) and rebuild the public `index.html`
   page together with an `ICS` feed limited to the next quarter.

### Configuration

- **Google Search** – Provide `GOOGLE_API_KEY` and `GOOGLE_CX` repository secrets
  so the workflow can query the Custom Search API. Update
  `data/industry_sectors.yaml` to adjust which industries are searched each
  week.
- **Exhibitor scraping** – Targets are configured in
  `data/exhibitors_targets.yaml`.
- **Manual fixtures** – Add any must-have events to `data/manual_events.yaml`.
  These seed entries are merged before Google search results so known shows
  never disappear while their 2025 listings are still being announced.

Run the scripts locally with Python 3.11+ if you need to test changes:

```bash
python scripts/search_google.py     # requires the Google API credentials
python scripts/scrape_exhibitors.py
python scripts/build_outputs.py
```

`search_google.py` now understands structured JSON-LD, `.ics` calendar links,
and more venue-specific queries, so pages that list multiple expos (for
example venue event calendars) are harvested in a single run instead of being
skipped when they lack simple date text.

The workflow definition lives in `.github/workflows/weekly-discover-refresh.yml`.
