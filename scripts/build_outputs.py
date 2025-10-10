#!/usr/bin/env python3
import os, json, re, uuid
from datetime import datetime, timedelta, timezone

HTML_PATH = "index.html"
ICS_PATH  = "London_Expos.ics"
EVENTS_JSON = "events.json"

def load_events():
    with open(EVENTS_JSON, "r", encoding="utf-8") as f:
        return json.load(f)

def parse_iso(dt_str: str) -> datetime:
    return datetime.fromisoformat(dt_str.replace("Z","+00:00"))

def within_next_three_months(dt: datetime) -> bool:
    now = datetime.now(timezone.utc)
    return now <= dt <= (now + timedelta(days=92))

def esc(s: str) -> str:
    s = (s or "")
    return s.replace("\\","\\\\").replace(";","\\;").replace(",","\\,").replace("\n","\\n")

def to_utc_ics(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).strftime("%Y%m%dT%H%M%SZ")  # ends with Z

def write_ics(events):
    dtstamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    lines = [
        "BEGIN:VCALENDAR","VERSION:2.0","PRODID:-//ExpoApp//London Expos//EN",
        "CALSCALE:GREGORIAN","METHOD:PUBLISH","X-WR-CALNAME:London Expos (Next 3 Months)"
    ]
    for e in events:
        s = to_utc_ics(parse_iso(e["start"]))
        en = to_utc_ics(parse_iso(e["end"]))
        desc = f"{e['title']} — {e.get('url','')}"
        if e.get("free") is True: desc += " (Free event)"
        lines += [
            "BEGIN:VEVENT",
            f"UID:{uuid.uuid4()}@expoapp",
            f"DTSTAMP:{dtstamp}",
            f"DTSTART:{s}",
            f"DTEND:{en}",
            f"SUMMARY:{esc(e['title'])}",
            f"LOCATION:{esc(e.get('venue',''))}",
            f"DESCRIPTION:{esc(desc)}",
            f"URL:{e.get('url','')}",
            "END:VEVENT"
        ]
    lines.append("END:VCALENDAR")
    with open(ICS_PATH,"w",encoding="utf-8") as f: f.write("\n".join(lines))

def inject_events_into_html(html_text: str, events_window):
    m = re.search(r'const allEvents = (\[.*?\]);', html_text, flags=re.DOTALL)
    if not m: return html_text
    new_blob = json.dumps(events_window, ensure_ascii=False)
    return re.sub(r'const allEvents = \[.*?\];', f'const allEvents = {new_blob};', html_text, flags=re.DOTALL)

def main():
    all_events = load_events()

    # Keep only NEXT ~3 months for the site/feed
    window = []
    for e in all_events:
        try:
            sdt = parse_iso(e["start"])
        except Exception:
            continue
        if within_next_three_months(sdt):
            window.append(e)

    # Write ICS (UTC Z timestamps so Outlook reads it)
    write_ics(window)

    # Inject into index.html (so the site shows the same window)
    with open(HTML_PATH,"r",encoding="utf-8") as f: html=f.read()
    new_html = inject_events_into_html(html, window)
    with open(HTML_PATH,"w",encoding="utf-8") as f: f.write(new_html)

    print(f"Built {len(window)} events for next 3 months")

if __name__ == "__main__":
    main()
