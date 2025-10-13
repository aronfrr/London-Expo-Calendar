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

Run the scripts locally with Python 3.11+ if you need to test changes:

```bash
python scripts/search_google.py     # requires the Google API credentials
python scripts/scrape_exhibitors.py
python scripts/build_outputs.py
```

The workflow definition lives in `.github/workflows/weekly-discover-refresh.yml`.
