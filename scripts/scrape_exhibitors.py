#!/usr/bin/env python3
import json, yaml, re, os, sys, time
from pathlib import Path
from playwright.sync_api import sync_playwright

TARGETS_YAML = "data/exhibitors_targets.yaml"
OUT_DIR = Path("data/exhibitors")
OUT_DIR.mkdir(parents=True, exist_ok=True)

def slug(s: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", s.lower()).strip("-")

def save_list(match_title: str, names: list[str]):
    names = [n.strip() for n in names if n and n.strip()]
    # dedupe preserving order
    seen=set(); cleaned=[]
    for n in names:
        if n.lower() not in seen:
            seen.add(n.lower()); cleaned.append(n)
    out = OUT_DIR / f"{slug(match_title)}.json"
    with out.open("w", encoding="utf-8") as f:
        json.dump({"match": match_title, "exhibitors": cleaned}, f, ensure_ascii=False, indent=2)
    print(f"[ok] {match_title}: saved {len(cleaned)} exhibitors -> {out}")

def fetch_page(target: dict) -> list[str]:
    url = target["url"]
    selector = target.get("selector")
    wait_for = target.get("wait_for", selector)
    paginate = target.get("paginate", {})
    click_sel = paginate.get("click")
    max_clicks = int(paginate.get("max_clicks", 0))

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        context = browser.new_context(user_agent="Mozilla/5.0 (London-Expos-Bot)")
        page = context.new_page()
        page.goto(url, wait_until="domcontentloaded", timeout=60000)
        if wait_for:
            page.wait_for_selector(wait_for, timeout=60000)

        for _ in range(max_clicks):
            try:
                btn = page.locator(click_sel)
                if btn.count()==0 or not btn.first.is_visible():
                    break
                btn.first.click()
                time.sleep(1.0)
            except Exception:
                break

        names=[]
        for el in page.locator(selector).all():
            txt = (el.text_content() or "").strip()
            if txt: names.append(txt)
        browser.close()
        return names

def main():
    if not os.path.exists(TARGETS_YAML):
        print(f"[info] No {TARGETS_YAML}; skipping.")
        return
    with open(TARGETS_YAML, "r", encoding="utf-8") as f:
        targets = yaml.safe_load(f) or []
    for t in targets:
        try:
            names = fetch_page(t)
            save_list(t["match"], names)
        except Exception as ex:
            print(f"[warn] {t.get('match')} failed: {ex}", file=sys.stderr)

if __name__ == "__main__":
    main()
