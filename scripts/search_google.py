#!/usr/bin/env python3
"""Discover upcoming London expos via Google Custom Search.

This script performs weekly discovery runs by combining curated search
queries, parsing structured data (JSON-LD, microdata, ICS) from the
resulting pages, and reconciling everything with the existing
``events.json`` file.

It also supports optional manual seed events defined in
``data/manual_events.yaml`` so that known fixtures are never dropped
while we wait for them to appear in search results.
"""

from __future__ import annotations

import json
import os
import re
import time
from datetime import datetime, timedelta, timezone
from typing import Dict, Iterable, List, Optional, Sequence, Tuple
from urllib.parse import urlencode, urljoin

try:  # pragma: no cover - optional dependency in CI
    import requests  # type: ignore
except ImportError:  # pragma: no cover
    requests = None

import urllib.request

import yaml
from bs4 import BeautifulSoup

try:  # pragma: no cover - Python < 3.9 support
    from zoneinfo import ZoneInfo
except ImportError:  # pragma: no cover
    ZoneInfo = None  # type: ignore

API_KEY = os.environ.get("GOOGLE_API_KEY")
CX = os.environ.get("GOOGLE_CX")

EVENTS_JSON = "events.json"
CHANGELOG = "CHANGELOG.md"
WINDOW_DAYS = 84  # 12 weeks ≈ 3 months
HEADERS = {"User-Agent": "Mozilla/5.0 (London-Expos-Updater)"}
LONDON_TZ = ZoneInfo("Europe/London") if ZoneInfo is not None else timezone.utc

SECTORS_FILE = os.path.join("data", "industry_sectors.yaml")
MANUAL_FILE = os.path.join("data", "manual_events.yaml")

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


def load_sector_definitions() -> List[Dict[str, object]]:
    try:
        with open(SECTORS_FILE, "r", encoding="utf-8") as f:
            data = yaml.safe_load(f) or []
    except FileNotFoundError:
        return DEFAULT_SECTORS
    except Exception as ex:  # pragma: no cover - IO failure
        print(f"[warn] Failed to read {SECTORS_FILE}: {ex}")
        return DEFAULT_SECTORS

    sectors: List[Dict[str, object]] = []
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


def load_events() -> List[Dict[str, object]]:
    try:
        with open(EVENTS_JSON, "r", encoding="utf-8") as f:
            return json.load(f)
    except FileNotFoundError:
        return []


def save_events(events: Sequence[Dict[str, object]]) -> None:
    with open(EVENTS_JSON, "w", encoding="utf-8") as f:
        json.dump(list(events), f, ensure_ascii=False, indent=2)


def write_changelog(added: Sequence[Dict[str, object]], changed: Sequence[Dict[str, object]]) -> None:
    if not added and not changed:
        return
    ts = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    lines = [
        f"## {ts} Weekly Google search",
        f"- NEW: {len(added)}",
        f"- DATE CHANGED: {len(changed)}",
        "",
    ]
    for e in added:
        lines.append(
            f"  - NEW • {e['title']} — {e['venue']} — {e['start'][:10]} → {e['end'][:10]} • {e['url']}"
        )
    for e in changed:
        lines.append(
            f"  - DATE CHANGED • {e['title']} — {e['venue']} — {e['start'][:10]} → {e['end'][:10]} • {e['url']}"
        )
    with open(CHANGELOG, "a", encoding="utf-8") as f:
        f.write("\n".join(lines) + "\n")


# ---- HTTP helpers -------------------------------------------------------

def http_get(url: str, *, params: Optional[Dict[str, object]] = None, headers=None, timeout: int = 30,
             as_bytes: bool = False) -> str | bytes:
    headers = headers or {}
    if requests is not None:  # pragma: no branch - runtime dependent
        resp = requests.get(url, params=params, headers=headers, timeout=timeout)
        resp.raise_for_status()
        return resp.content if as_bytes else resp.text

    # Fallback to urllib when requests isn't available.
    if params:
        query = urlencode({k: v for k, v in params.items() if v is not None})
        sep = "&" if "?" in url else "?"
        url = f"{url}{sep}{query}"
    req = urllib.request.Request(url, headers=headers)
    with urllib.request.urlopen(req, timeout=timeout) as resp:  # nosec B310 (trusted hosts)
        data = resp.read()
    return data if as_bytes else data.decode("utf-8", errors="replace")


def http_get_json(url: str, *, params=None, headers=None, timeout: int = 30) -> Dict[str, object]:
    payload = http_get(url, params=params, headers=headers, timeout=timeout)
    if isinstance(payload, bytes):
        payload = payload.decode("utf-8", errors="replace")
    if not payload:
        return {}
    return json.loads(payload)


def google_search(q: str, num: int = 10) -> List[str]:
    url = "https://www.googleapis.com/customsearch/v1"
    params = {"key": API_KEY, "cx": CX, "q": q, "num": num, "safe": "off"}
    data = http_get_json(url, params=params, timeout=30)
    items = data.get("items", []) if isinstance(data, dict) else []
    links: List[str] = []
    for item in items:
        if isinstance(item, dict) and item.get("link"):
            links.append(str(item["link"]))
    return links


# ---- parsing helpers ----------------------------------------------------

DATE_RANGE = re.compile(r"(\d{1,2})\s*(?:–|-|to)\s*(\d{1,2})\s+([A-Za-z]{3,})\s+(\d{4})")
DATE_SINGLE = re.compile(r"(\d{1,2})\s+([A-Za-z]{3,})\s+(\d{4})")
ISO = re.compile(r"(\d{4})-(\d{2})-(\d{2})")
EIGHT_DIGIT = re.compile(r"^(\d{4})(\d{2})(\d{2})$")
EIGHT_TIME = re.compile(r"^(\d{4})(\d{2})(\d{2})T(\d{2})(\d{2})(\d{2})(Z?)$")


def parse_date_range_text(txt: str) -> Optional[Tuple[datetime, datetime]]:
    t = (txt or "").replace("\u2013", "-").replace("\u2014", "-").replace("\u00a0", " ")
    m = ISO.search(t)
    if m:
        y, mth, d = map(int, m.groups())
        start = datetime(y, mth, d, 9, 0, 0, tzinfo=LONDON_TZ)
        end = start + timedelta(hours=8)
        return start, end
    m = DATE_RANGE.search(t)
    if m:
        d1, d2, mon, y = int(m.group(1)), int(m.group(2)), m.group(3), int(m.group(4))
        try:
            mn = datetime.strptime(mon[:3], "%b").month
        except ValueError:
            mn = None
        if mn:
            return (
                datetime(y, mn, d1, 9, 0, 0, tzinfo=LONDON_TZ),
                datetime(y, mn, d2, 17, 0, 0, tzinfo=LONDON_TZ),
            )
    m = DATE_SINGLE.search(t)
    if m:
        d, mon, y = int(m.group(1)), m.group(2), int(m.group(3))
        try:
            mn = datetime.strptime(mon[:3], "%b").month
        except ValueError:
            mn = None
        if mn:
            start = datetime(y, mn, d, 9, 0, 0, tzinfo=LONDON_TZ)
            end = start + timedelta(hours=8)
            return start, end
    return None


def parse_possible_datetime(value) -> Optional[datetime]:
    if not value:
        return None
    val = str(value).strip()
    if not val:
        return None
    try:
        if val.endswith("Z"):
            return datetime.fromisoformat(val.replace("Z", "+00:00"))
        parsed = datetime.fromisoformat(val)
        if parsed.tzinfo is None:
            return parsed.replace(tzinfo=LONDON_TZ)
        return parsed
    except Exception:
        pass
    m = EIGHT_TIME.match(val)
    if m:
        y, mth, d, hh, mm, ss, z = m.groups()
        dt = datetime(int(y), int(mth), int(d), int(hh), int(mm), int(ss))
        return dt.replace(tzinfo=timezone.utc if z else LONDON_TZ)
    m = EIGHT_DIGIT.match(val)
    if m:
        y, mth, d = map(int, m.groups())
        return datetime(y, mth, d, 9, 0, 0, tzinfo=LONDON_TZ)
    return None


def ensure_list(value) -> List:
    if value is None:
        return []
    if isinstance(value, (list, tuple, set)):
        return list(value)
    return [value]


def sector_for(title: str) -> Optional[str]:
    t = (title or "").lower()
    for sector in SECTOR_DEFS:
        keywords = sector.get("keywords") or []
        if any(k in t for k in keywords):
            return sector["name"]  # type: ignore[index]
    return None


def unify(title: str, start_iso: str, end_iso: str, venue: str, url: str) -> Dict[str, object]:
    clean_title = title.strip()
    sector = sector_for(clean_title)
    return {
        "title": clean_title,
        "start": start_iso,
        "end": end_iso,
        "url": url,
        "venue": venue or "London",
        "sector": [sector] if sector else [],
        "exhibitors": [],
        "free": False,
    }


# ---- structured-data extraction -----------------------------------------

def extract_jsonld_events(html: str) -> List[Tuple[str, str, str, str, str]]:
    soup = BeautifulSoup(html, "html.parser")
    events: List[Tuple[str, str, str, str, str]] = []
    seen = set()

    def iter_event_nodes(node, inherited=None):
        if isinstance(node, dict):
            types = node.get("@type")
            if isinstance(types, list):
                type_set = {str(t).lower() for t in types}
            elif types:
                type_set = {str(types).lower()}
            else:
                type_set = set()

            if "eventseries" in type_set:
                base = dict(inherited or {})
                base.update(node)
                for sub in ensure_list(node.get("subEvent") or node.get("eventSchedule")):
                    if isinstance(sub, dict):
                        merged = dict(base)
                        merged.update(sub)
                        yield from iter_event_nodes(merged, base)
            elif type_set & {"event", "businessevent", "exhibitionevent", "conferencesevent", "festival", "expositionevent"}:
                base = dict(inherited or {})
                base.update(node)
                yield base

            for value in node.values():
                yield from iter_event_nodes(value, inherited)
        elif isinstance(node, list):
            for item in node:
                yield from iter_event_nodes(item, inherited)

    for tag in soup.find_all("script", type="application/ld+json"):
        try:
            payload = tag.string or tag.contents[0]
            data = json.loads(payload)
        except Exception:
            continue
        for event_obj in iter_event_nodes(data):
            if not isinstance(event_obj, dict):
                continue
            identifier = json.dumps(event_obj, sort_keys=True, ensure_ascii=False)
            if identifier in seen:
                continue
            seen.add(identifier)

            name = (event_obj.get("name") or "").strip()
            start_raw = event_obj.get("startDate") or event_obj.get("startTime")
            end_raw = event_obj.get("endDate") or event_obj.get("endTime") or start_raw
            if not start_raw:
                continue
            start_dt = parse_possible_datetime(start_raw)
            if not start_dt:
                continue
            end_dt = parse_possible_datetime(end_raw) or (start_dt + timedelta(hours=8))
            if end_dt < start_dt:
                end_dt = start_dt + timedelta(hours=8)

            venue = ""
            loc = event_obj.get("location") or event_obj.get("eventVenue")
            if isinstance(loc, dict):
                venue = loc.get("name") or loc.get("addressLocality") or ""
            elif isinstance(loc, list):
                for cand in loc:
                    if isinstance(cand, dict):
                        venue = cand.get("name") or cand.get("addressLocality") or venue
                        if venue:
                            break
            elif isinstance(loc, str):
                venue = loc

            url = (
                event_obj.get("url")
                or event_obj.get("mainEntityOfPage")
                or event_obj.get("@id")
                or ""
            )
            events.append((name, start_dt.isoformat(), end_dt.isoformat(), venue, url))

    return events


def find_ics_links(soup: BeautifulSoup, base_url: str) -> Iterable[str]:
    seen = set()
    for tag in soup.find_all(["a", "link"], href=True):
        href = tag.get("href")
        if not href:
            continue
        if href.lower().endswith(".ics"):
            full = urljoin(base_url, href)
            if full not in seen:
                seen.add(full)
                yield full


def parse_ics_datetime(value: Optional[str], tz_hint: Optional[str]) -> Optional[datetime]:
    if not value:
        return None
    value = value.strip()
    tz = timezone.utc if value.endswith("Z") else None
    if tz_hint and not tz and ZoneInfo is not None:
        try:
            tz = ZoneInfo(tz_hint)
        except Exception:  # pragma: no cover - depends on tz database
            tz = None
    tz = tz or LONDON_TZ
    clean = value.rstrip("Z")
    if len(clean) == 8 and clean.isdigit():
        dt = datetime.strptime(clean, "%Y%m%d")
        return datetime(dt.year, dt.month, dt.day, 9, 0, 0, tzinfo=tz)
    try:
        dt = datetime.strptime(clean, "%Y%m%dT%H%M%S")
        return dt.replace(tzinfo=tz)
    except Exception:
        return parse_possible_datetime(value)


def parse_ics_events(data: str, source_url: str) -> List[Dict[str, object]]:
    lines: List[str] = []
    for raw in data.splitlines():
        raw = raw.rstrip("\r\n")
        if not raw:
            continue
        if raw.startswith(" ") and lines:
            lines[-1] += raw[1:]
        else:
            lines.append(raw)

    events: List[Dict[str, object]] = []
    current: Dict[str, str] = {}
    for line in lines:
        if line == "BEGIN:VEVENT":
            current = {}
            continue
        if line == "END:VEVENT":
            if current.get("SUMMARY") and current.get("DTSTART"):
                start = parse_ics_datetime(current.get("DTSTART"), current.get("DTSTART_TZID"))
                end = parse_ics_datetime(current.get("DTEND"), current.get("DTEND_TZID"))
                if not start:
                    current = {}
                    continue
                if not end or end < start:
                    end = start + timedelta(hours=8)
                events.append(
                    unify(
                        current.get("SUMMARY", "Untitled event"),
                        start.isoformat(),
                        end.isoformat(),
                        current.get("LOCATION", "London"),
                        current.get("URL") or source_url,
                    )
                )
            current = {}
            continue
        if ":" not in line:
            continue
        key, value = line.split(":", 1)
        base_key = key.split(";", 1)[0].upper()
        params = key[len(base_key):]
        if params.startswith(";"):
            params = params[1:]
        else:
            params = ""
        param_map = {}
        if params:
            for segment in params.split(";"):
                if "=" in segment:
                    pk, pv = segment.split("=", 1)
                    param_map[pk.upper()] = pv
        current[base_key] = value.strip()
        for pk, pv in param_map.items():
            current[f"{base_key}_{pk}"] = pv
    return events


def extract_events_from_page(url: str, html: str) -> List[Dict[str, object]]:
    jsonld_events = extract_jsonld_events(html)
    if jsonld_events:
        results: List[Dict[str, object]] = []
        for name, start, end, venue, link in jsonld_events:
            page_link = link or url
            results.append(unify(name, start, end, venue, page_link))
        return results

    soup = BeautifulSoup(html, "html.parser")
    collected: List[Dict[str, object]] = []
    for link in find_ics_links(soup, url):
        try:
            ics_text = http_get(link, headers=HEADERS, timeout=30)
        except Exception:
            continue
        if isinstance(ics_text, bytes):
            ics_text = ics_text.decode("utf-8", errors="replace")
        collected.extend(parse_ics_events(ics_text, url))
    if collected:
        return collected

    name = (soup.title.string if soup.title else url).strip()
    dr = parse_date_range_text(html) or parse_date_range_text(name)
    if not dr:
        return []
    start, end = dr
    return [unify(name, start.isoformat(), end.isoformat(), "London", url)]


# ---- manual seeds -------------------------------------------------------

def load_manual_events() -> List[Dict[str, object]]:
    try:
        with open(MANUAL_FILE, "r", encoding="utf-8") as f:
            data = yaml.safe_load(f) or []
    except FileNotFoundError:
        return []
    except Exception as ex:  # pragma: no cover - IO failure
        print(f"[warn] Failed to read {MANUAL_FILE}: {ex}")
        return []

    manual: List[Dict[str, object]] = []
    for entry in data:
        if not isinstance(entry, dict):
            continue
        title = (entry.get("title") or "").strip()
        start = parse_possible_datetime(entry.get("start"))
        end = parse_possible_datetime(entry.get("end"))
        if not title or not start:
            continue
        if not end:
            end = start + timedelta(hours=8)
        manual.append(
            unify(
                title,
                start.isoformat(),
                end.isoformat(),
                entry.get("venue", "London"),
                entry.get("url", ""),
            )
        )
    return manual


# ---- main workflow ------------------------------------------------------

def within_window(dt: datetime) -> bool:
    now = datetime.now(timezone.utc)
    return now <= dt <= now + timedelta(days=WINDOW_DAYS)


def main() -> None:
    if not API_KEY or not CX:
        print("Missing GOOGLE_API_KEY or GOOGLE_CX")
        return

    queries = [f"{BASE_QUERY} {q}" for q in SECTOR_QUERIES] + [
        f'{BASE_QUERY} "ExCeL London"',
        f'{BASE_QUERY} "Olympia London"',
        f'{BASE_QUERY} "Business Design Centre"',
        f'{BASE_QUERY} "QEII Centre"',
        f'{BASE_QUERY} "Tobacco Dock"',
        f'{BASE_QUERY} "Design Centre Chelsea Harbour"',
        f'{BASE_QUERY} "The O2"',
        f'{BASE_QUERY} site:excel.london',
        f'{BASE_QUERY} site:olympia.london',
    ]

    urls: List[str] = []
    for q in queries:
        try:
            urls += google_search(q, RESULTS_PER_QUERY)
            time.sleep(0.25)
        except Exception as ex:
            print(f"[warn] search failed: {q} :: {ex}")
    urls = list(dict.fromkeys(urls))  # dedupe

    candidates: List[Dict[str, object]] = []
    for url in urls:
        try:
            html = http_get(url, headers=HEADERS, timeout=30)
        except Exception:
            continue
        if isinstance(html, bytes):
            html = html.decode("utf-8", errors="replace")
        for event in extract_events_from_page(url, html):
            try:
                s_dt = datetime.fromisoformat(str(event["start"]).replace("Z", "+00:00"))
            except Exception:
                s_dt = parse_possible_datetime(event["start"])
            if not s_dt or not within_window(s_dt):
                continue
            try:
                e_dt = datetime.fromisoformat(str(event["end"]).replace("Z", "+00:00"))
            except Exception:
                e_dt = parse_possible_datetime(event["end"]) or (s_dt + timedelta(hours=8))
            candidates.append(
                unify(
                    event["title"],
                    s_dt.isoformat(),
                    e_dt.isoformat(),
                    event.get("venue", "London"),
                    event.get("url", url) or url,
                )
            )

    existing = load_events()
    bykey = {(e["title"].lower().strip(), e["venue"]): e for e in existing}

    # ensure manual seeds are always present without being counted as "added"
    for seed in load_manual_events():
        key = (seed["title"].lower().strip(), seed["venue"])
        if key not in bykey:
            existing.append(seed)
            bykey[key] = seed

    added: List[Dict[str, object]] = []
    changed: List[Dict[str, object]] = []
    for ne in candidates:
        key = (ne["title"].lower().strip(), ne["venue"])
        if key in bykey:
            ex = bykey[key]
            if ex["start"][:10] != ne["start"][:10] or ex["end"][:10] != ne["end"][:10]:
                ne["sector"] = ex.get("sector", []) or ne.get("sector", [])
                ne["exhibitors"] = ex.get("exhibitors", [])
                ne["free"] = ex.get("free", False)
                existing[existing.index(ex)] = ne
                bykey[key] = ne
                changed.append(ne)
            else:
                if not ex.get("url"):
                    ex["url"] = ne["url"]
        else:
            existing.append(ne)
            bykey[key] = ne
            added.append(ne)

    horizon = datetime.now(timezone.utc) + timedelta(days=190)
    cleaned: List[Dict[str, object]] = []
    for event in existing:
        try:
            dt = datetime.fromisoformat(str(event["start"]).replace("Z", "+00:00"))
            if dt <= horizon:
                cleaned.append(event)
        except Exception:
            cleaned.append(event)

    save_events(cleaned)
    write_changelog(added, changed)
    print(
        f"Google candidates: {len(candidates)}, added: {len(added)}, changed: {len(changed)}, total: {len(cleaned)}"
    )


if __name__ == "__main__":
    main()
