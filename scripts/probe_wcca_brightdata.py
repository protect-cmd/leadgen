"""
WCCA Bright Data field-mapping probe.

Goal: confirm Bright Data Scraping Browser bypasses WCCA CAPTCHA on eviction
detail pages, and capture real HTML + screenshots for parser mapping.

Outputs to tmp/wcca_probe/:
    listing_<date>.html       full results-page HTML
    listing_<date>.png        results-page screenshot
    detail_<caseNo>.html      detail-page HTML (3-5 cases)
    detail_<caseNo>.png       detail-page screenshot
    summary.json              { listing_rows, captured_cases, captcha_hits }

Does NOT touch Supabase, BatchData, GHL, Bland, or Instantly.

Usage:
    python scripts/probe_wcca_brightdata.py                    # yesterday
    python scripts/probe_wcca_brightdata.py 2026-05-21         # specific date
    python scripts/probe_wcca_brightdata.py 2026-05-20 2026-05-21
"""
from __future__ import annotations

import asyncio
import json
import os
import re
import sys
from datetime import date, datetime, timedelta
from pathlib import Path

from playwright.async_api import async_playwright

OUT_DIR = Path(__file__).resolve().parent.parent / "tmp" / "wcca_probe"
ADVANCED_URL = "https://wcca.wicourts.gov/advanced.html"
CASE_TYPE_SC = "SC"            # Small Claims
CLASS_CODE_EVICTION = "31004"  # Small Claims, Eviction
MAX_DETAIL_CAPTURES = 5
CAPTCHA_TEXT_RE = re.compile(r"complete the CAPTCHA|captcha", re.IGNORECASE)


def _bright_data_ws_url() -> str:
    explicit = os.getenv("BRIGHTDATA_SB_WS")
    if explicit:
        return explicit
    customer = os.getenv("BRIGHTDATA_CUSTOMER_ID", "hl_74fc5212")
    zone = os.getenv("BRIGHTDATA_ZONE", "scraping_browser1")
    password = os.getenv("BRIGHTDATA_ZONE_PASSWORD", "db1ticxa4ik3")
    return f"wss://brd-customer-{customer}-zone-{zone}:{password}@brd.superproxy.io:9222"


def _parse_args(argv: list[str]) -> tuple[date, date]:
    if len(argv) == 1:
        d = date.fromisoformat(argv[0])
        return d, d
    if len(argv) == 2:
        return date.fromisoformat(argv[0]), date.fromisoformat(argv[1])
    y = date.today() - timedelta(days=1)
    return y, y


def _mmddyyyy(d: date) -> str:
    return d.strftime("%m-%d-%Y")


async def _run(start: date, end: date) -> None:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    summary: dict = {
        "range": [start.isoformat(), end.isoformat()],
        "listing_rows": None,
        "captured_cases": [],
        "captcha_hits": [],
        "errors": [],
    }

    ws_url = _bright_data_ws_url()
    print(f"[probe] connecting to Bright Data Scraping Browser…")

    async with async_playwright() as p:
        browser = await p.chromium.connect_over_cdp(ws_url)
        context = browser.contexts[0] if browser.contexts else await browser.new_context()
        page = await context.new_page()

        print(f"[probe] loading {ADVANCED_URL}")
        await page.goto(ADVANCED_URL, wait_until="domcontentloaded", timeout=120_000)

        # Filing date
        await page.fill("input[name='filingDate.start']", _mmddyyyy(start))
        await page.fill("input[name='filingDate.end']", _mmddyyyy(end))

        # Case type = SC
        await page.select_option("select[name='caseType']", CASE_TYPE_SC)
        # Class code dropdown becomes populated after case type — wait briefly
        await page.wait_for_timeout(800)
        try:
            await page.select_option("select[name='classCode']", CLASS_CODE_EVICTION)
        except Exception as e:
            summary["errors"].append(f"classCode select failed: {e!r}")

        await page.click("button[type='submit'], input[type='submit']")
        await page.wait_for_load_state("networkidle", timeout=120_000)

        # Listing capture
        list_html = await page.content()
        list_path = OUT_DIR / f"listing_{start.isoformat()}_{end.isoformat()}.html"
        list_path.write_text(list_html, encoding="utf-8")
        await page.screenshot(path=str(list_path.with_suffix(".png")), full_page=True)
        if CAPTCHA_TEXT_RE.search(list_html):
            summary["captcha_hits"].append({"stage": "listing", "url": page.url})
            print("[probe] CAPTCHA hit on LISTING — Bright Data did not bypass.")

        # Extract detail links
        hrefs = await page.eval_on_selector_all(
            "a[href*='caseDetail.html']",
            "els => els.map(e => e.getAttribute('href'))",
        )
        # Dedupe by caseNo
        seen: set[str] = set()
        detail_urls: list[tuple[str, str]] = []
        for h in hrefs:
            if not h:
                continue
            m = re.search(r"caseNo=([0-9A-Z]+)", h)
            if not m or m.group(1) in seen:
                continue
            seen.add(m.group(1))
            full = h if h.startswith("http") else f"https://wcca.wicourts.gov/{h.lstrip('/')}"
            detail_urls.append((m.group(1), full))
            if len(detail_urls) >= MAX_DETAIL_CAPTURES:
                break

        summary["listing_rows"] = len(hrefs)
        print(f"[probe] listing: {len(hrefs)} detail links, capturing first {len(detail_urls)} unique cases")

        # Detail pages — click from within the same browser session (CAPTCHA was
        # triggered by direct navigation in prior tests; Bright Data should make
        # either route work, but we use in-session navigation for parity).
        for case_no, url in detail_urls:
            try:
                await page.goto(url, wait_until="domcontentloaded", timeout=120_000)
                await page.wait_for_load_state("networkidle", timeout=60_000)
                html = await page.content()
                dpath = OUT_DIR / f"detail_{case_no}.html"
                dpath.write_text(html, encoding="utf-8")
                await page.screenshot(path=str(dpath.with_suffix(".png")), full_page=True)
                hit = bool(CAPTCHA_TEXT_RE.search(html))
                summary["captured_cases"].append({
                    "case_no": case_no,
                    "url": url,
                    "captcha": hit,
                    "bytes": len(html),
                })
                print(f"[probe]   {case_no}: {'CAPTCHA' if hit else 'ok'}  {len(html)} bytes")
                if hit:
                    summary["captcha_hits"].append({"stage": "detail", "case_no": case_no})
            except Exception as e:
                summary["errors"].append(f"{case_no}: {e!r}")
                print(f"[probe]   {case_no}: ERROR {e!r}")

        await browser.close()

    (OUT_DIR / "summary.json").write_text(json.dumps(summary, indent=2), encoding="utf-8")
    print(f"[probe] wrote {OUT_DIR}/summary.json")
    print(json.dumps(summary, indent=2))


if __name__ == "__main__":
    start, end = _parse_args(sys.argv[1:])
    asyncio.run(_run(start, end))
