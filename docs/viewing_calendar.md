# Viewing the Calendar Locally

To preview the static calendar page and capture screenshots, run a simple HTTP server from the repository root:

```bash
python -m http.server 8000
```

Then open <http://localhost:8000/index.html> in a browser. This serves the latest committed `index.html` with its embedded event data.

For automated captures (e.g. in CI), launch the server in the background and use a headless browser such as Playwright or Puppeteer to visit the page and take a full-page screenshot.
