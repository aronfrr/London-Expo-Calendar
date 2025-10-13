#!/usr/bin/env python3
import os, re, json, time, requests, yaml
from datetime import datetime, timedelta, timezone
from bs4 import BeautifulSoup

API_KEY = os.environ.get("GOOGLE_API_KEY")
CX = os.environ.get("GOOGLE_CX")

EVENTS_JSON = "events.json"
CHANGELOG = "CHANGELOG.md"
WINDOW_DAYS = 84   # 12 weeks
HEADERS = {"User-Agent": "Mozilla/5.0 (London-Expos-Updater)"}
LON = timezone.utc

SECTORS_FILE = os.path.join("data", "industry_sectors.yaml")
DEFAULT_SECTORS = [
    {
        "name": "Engineering & Manufacturing",
        "search": "engineering OR manufacturing OR aerospace OR automotive",
        "keywords": ["manufactur", "engineer", "aerospace", "aviation", "automotive", "packaging"],
    },
    {
        "name": "Defence, Cyber & Security",
        "search": "defence OR defense OR cyber OR security OR infosec",
        "keywords": ["defence", "defense", "cyber", "security", "infosec", "risk"],
    },
    {
        "name": "Energy",
        "search": "energy OR smart buildings OR sustainability OR net zero",
        "keywords": ["energy", "smart building", "sustainab", "net zero"],
    },
    {
        "name": "Public Sector",
        "search": "public sector OR government OR NHS OR education",
        "keywords": ["public sector", "government", "nhs", "education"],
    },
    {
        "name": "Fintech",
        "search": "fintech OR banking OR payments OR blockchain OR crypto OR DeFi",
        "keywords": ["fintech", "finance", "bank", "payment", "blockchain", "crypto", "defi"],
    },
    {
        "name": "Life Sciences",
        "search": "life sciences OR biotech OR pharma OR medical",
        "keywords": ["life science", "biotech", "pharma", "medical", "dental", "vet"],
    },
    {
        "name": "Project Management",
        "search": "project management OR PMO OR programme OR portfolio",
        "keywords": ["project management", "pmo", "programme", "portfolio"],
    },
]


def load_sector_definitions():
    try:
        with open(SECTORS_FILE, "r", encoding="utf-8") as f:
            data = yaml.safe_load(f) or []
    except FileNotFoundError:
        return DEFAULT_SECTORS
    except Exception as ex:
        print(f"[warn] Failed to read {SECTORS_FILE}: {ex}")
        return DEFAULT_SECTORS

    sectors = []
    for entry in data:
        if isinstance(entry, str):
            sectors.append({"name": entry, "search": entry, "keywords": [entry.lower()]})
            continue
        if not isinstance(entry, dict):
            continue
        name = (entry.get("name") or "").strip()
        search = (entry.get("search") or entry.get("query") or "").strip()
        keywords = entry.get("keywords") or entry.get("tags") or []
        if not name:
            continue
        if isinstance(keywords, str):
            keywords = [keywords]
        keywords = [k.lower() for k in keywords if isinstance(k, str) and k.strip()]
        sectors.append({
            "name": name,
            "search": search or name,
            "keywords": keywords,
        })
    return sectors or DEFAULT_SECTORS


SECTOR_DEFS = load_sector_definitions()
for sector in SECTOR_DEFS:
    sector["keywords"] = [k.lower() for k in (sector.get("keywords") or [])]
SECTOR_QUERIES = [s["search"] for s in SECTOR_DEFS if s.get("search")]
if not SECTOR_QUERIES:
    SECTOR_QUERIES = [s["search"] for s in DEFAULT_SECTORS]
BASE_QUERY = '(expo OR "trade show" OR exhibition OR fair OR conference) "London"'
RESULTS_PER_QUERY = 10

VENUE_WORDS = [
    "ExCeL London","Olympia London","Business Design Centre",
    "QEII Centre","Tobacco Dock","Design Centre Chelsea Harbour","London"
]

def load_events():
    try:
        with open(EVENTS_JSON, "r", encoding="utf-8") as f: return json.load(f)
    except FileNotFoundError:
        return []

def save_events(events):
    with open(EVENTS_JSON, "w", encoding="utf-8") as f:
        json.dump(events, f, ensure_ascii=False, indent=2)

def write_changelog(added, changed):
    if not added and not changed: return
    ts = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    lines = [f"## {ts} Weekly Google search", f"- NEW: {len(added)}", f"- DATE CHANGED: {len(changed)}", ""]
    for e in added:
        lines.append(f"  - NEW • {e['title']} — {e['venue']} — {e['start'][:10]} → {e['end'][:10]} • {e['url']}")
    for e in changed:
        lines.append(f"  - DATE CHANGED • {e['title']} — {e['venue']} — {e['start'][:10]} → {e['end'][:10]} • {e['url']}")
    with open(CHANGELOG, "a", encoding="utf-8") as f: f.write("\n".join(lines) + "\n")

def google_search(q, num=10):
    url = "https://www.googleapis.com/customsearch/v1"
    params = {"key": API_KEY, "cx": CX, "q": q, "num": num, "safe": "off"}
    r = requests.get(url, params=params, timeout=30)
    r.raise_for_status()
    data = r.json()
    return [it.get("link") for it in data.get("items", []) if it.get("link")]

# ---- parsing helpers ----
DATE_RANGE = re.compile(r"(\d{1,2})\s*(?:–|-|to)\s*(\d{1,2})\s+([A-Za-z]{3,})\s+(\d{4})")
DATE_SINGLE = re.compile(r"(\d{1,2})\s+([A-Za-z]{3,})\s+(\d{4})")
ISO = re.compile(r"(\d{4})-(\d{2})-(\d{2})")
def mon_num(mon): 
    try: return datetime.strptime(mon[:3], "%b").month
    except: return None

def parse_date_range_text(txt):
    t = (txt or "").replace("\u2013","-").replace("\u2014","-").replace("\u00a0"," ")
    m = ISO.search(t)
    if m:
        y, mth, d = map(int, m.groups())
        start = datetime(y, mth, d, 9, 0, 0, tzinfo=LON); end = start + timedelta(hours=8)
        return start, end
    m = DATE_RANGE.search(t)
    if m:
        d1, d2, mon, y = int(m.group(1)), int(m.group(2)), m.group(3), int(m.group(4))
        mn = mon_num(mon)
        if mn: 
            return (datetime(y, mn, d1, 9, 0, 0, tzinfo=LON), datetime(y, mn, d2, 17, 0, 0, tzinfo=LON))
    m = DATE_SINGLE.search(t)
    if m:
        d, mon, y = int(m.group(1)), m.group(2), int(m.group(3))
        mn = mon_num(mon)
        if mn:
            start = datetime(y, mn, d, 9, 0, 0, tzinfo=LON); end = start + timedelta(hours=8)
            return start, end
    return None

def extract_jsonld_event(html):
    soup = BeautifulSoup(html, "html.parser")
    for tag in soup.find_all("script", type="application/ld+json"):
        try:
            data = json.loads(tag.string or "")
        except Exception:
            continue
        items = data if isinstance(data, list) else [data]
        for it in items:
            if not isinstance(it, dict): continue
            t = str(it.get("@type","")).lower()
            if t in ("event","businessevent","exhibitionevent","conferencesevent"):
                name = (it.get("name") or "").strip()
                start = it.get("startDate") or ""
                end = it.get("endDate") or start
                venue = ""
                loc = it.get("location")
                if isinstance(loc, dict):
                    venue = loc.get("name") or ""
                return name, start, end, venue
    return None

def within_window(dt):
    now = datetime.now(timezone.utc)
    return now <= dt <= now + timedelta(days=WINDOW_DAYS)

def sector_for(title):
    t = (title or "").lower()
    for sector in SECTOR_DEFS:
        keywords = sector.get("keywords") or []
        if any(k in t for k in keywords):
            return sector["name"]
    return None

def unify(title, start_iso, end_iso, venue, url):
    return {
        "title": title.strip(),
        "start": start_iso,
        "end": end_iso,
        "url": url,
        "venue": venue or "London",
        "sector": [sector_for(title)] if sector_for(title) else [],
        "exhibitors": [],
        "free": False
    }

def main():
    if not API_KEY or not CX:
        print("Missing GOOGLE_API_KEY or GOOGLE_CX")
        return

    queries = [f'{BASE_QUERY} {q}' for q in SECTOR_QUERIES] + [
        f'{BASE_QUERY} "ExCeL London"', f'{BASE_QUERY} "Olympia London"',
        f'{BASE_QUERY} "Business Design Centre"', f'{BASE_QUERY} "QEII Centre"',
        f'{BASE_QUERY} "Tobacco Dock"', f'{BASE_QUERY} "Design Centre Chelsea Harbour"'
    ]
    urls = []
    for q in queries:
        try:
            urls += google_search(q, RESULTS_PER_QUERY)
            time.sleep(0.25)
        except Exception as ex:
            print(f"[warn] search failed: {q} :: {ex}")
    urls = list(dict.fromkeys(urls))  # dedupe

    # fetch each page, prefer JSON-LD Event, fallback to text dates
    candidates = []
    for u in urls:
        try:
            r = requests.get(u, headers=HEADERS, timeout=30)
            r.raise_for_status()
        except Exception:
            continue
        html = r.text
        parsed = extract_jsonld_event(html)
        if parsed:
            name, s_raw, e_raw, venue = parsed
            try:
                s = datetime.fromisoformat(s_raw.replace("Z","+00:00"))
                e = datetime.fromisoformat((e_raw or s_raw).replace("Z","+00:00"))
            except Exception:
                dr = parse_date_range_text(html) or parse_date_range_text(name)
                if not dr: continue
                s, e = dr
        else:
            name = (BeautifulSoup(html, "html.parser").title.string or u).strip()
            dr = parse_date_range_text(html) or parse_date_range_text(name)
            if not dr: continue
            s, e = dr
            venue = "London"

        if not within_window(s): 
            continue
        candidates.append(unify(name, s.isoformat(), e.isoformat(), venue, u))

    existing = load_events()
    bykey = {(e["title"].lower().strip(), e["venue"]): e for e in existing}
    added, changed = [], []
    for ne in candidates:
        k = (ne["title"].lower().strip(), ne["venue"])
        if k in bykey:
            ex = bykey[k]
            if ex["start"][:10] != ne["start"][:10] or ex["end"][:10] != ne["end"][:10]:
                ne["sector"] = ex.get("sector", []) or ne.get("sector", [])
                ne["exhibitors"] = ex.get("exhibitors", [])
                ne["free"] = ex.get("free", False)
                existing[existing.index(bykey[k])] = ne
                bykey[k] = ne
                changed.append(ne)
            else:
                if not ex.get("url"): ex["url"] = ne["url"]
        else:
            existing.append(ne)
            bykey[k] = ne
            added.append(ne)

    # keep list tidy (optional horizon)
    horizon = datetime.now(timezone.utc) + timedelta(days=190)
    cleaned = []
    for e in existing:
        try:
            dt = datetime.fromisoformat(e["start"].replace("Z","+00:00"))
            if dt <= horizon: cleaned.append(e)
        except Exception:
            cleaned.append(e)

    save_events(cleaned)
    write_changelog(added, changed)
    print(f"Google candidates: {len(candidates)}, added: {len(added)}, changed: {len(changed)}, total: {len(cleaned)}")

if __name__ == "__main__":
    main()
