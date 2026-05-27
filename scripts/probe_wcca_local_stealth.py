"""
WCCA field-mapping probe — LOCAL stealth Playwright (no Bright Data).

Bright Data refuses to proxy .gov; this script runs locally from your home IP
with tf-playwright-stealth applied and human-like pacing, to see whether the
WCCA detail-page CAPTCHA can be avoided without a paid CAPTCHA-bypass vendor.

Outputs to tmp/wcca_probe/ (same layout as the BD probe):
    listing_<dates>.html / .png
    detail_<caseNo>.html / .png
    summary.json

Does NOT touch Supabase, BatchData, GHL, Bland, or Instantly.

Usage:
    python scripts/probe_wcca_local_stealth.py                # yesterday
    python scripts/probe_wcca_local_stealth.py 2026-05-21
    python scripts/probe_wcca_local_stealth.py 2026-05-20 2026-05-21
    python scripts/probe_wcca_local_stealth.py 2026-05-21 --headed
"""
from __future__ import annotations

import asyncio
import json
import random
import re
import sys
from datetime import date, timedelta
from pathlib import Path

from playwright.async_api import async_playwright
from playwright_stealth import stealth_async

OUT_DIR = Path(__file__).resolve().parent.parent / "tmp" / "wcca_probe"
ADVANCED_URL = "https://wcca.wicourts.gov/advanced.html"
CASE_TYPE_SC = "SC"
CLASS_CODE_EVICTION = "31004"
MAX_DETAIL_CAPTURES = 5
CAPTCHA_TEXT_RE = re.compile(r"complete the CAPTCHA|recaptcha|hcaptcha|are you a human", re.IGNORECASE)
UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/131.0.0.0 Safari/537.36"
)


def _parse_args(argv: list[str]) -> tuple[date, date, bool]:
    headed = "--headed" in argv
    argv = [a for a in argv if a != "--headed"]
    if len(argv) == 1:
        d = date.fromisoformat(argv[0])
        return d, d, headed
    if len(argv) == 2:
        return date.fromisoformat(argv[0]), date.fromisoformat(argv[1]), headed
    y = date.today() - timedelta(days=1)
    return y, y, headed


def _mmddyyyy(d: date) -> str:
    return d.strftime("%m-%d-%Y")


async def _human_pause(lo: float = 0.4, hi: float = 1.4) -> None:
    await asyncio.sleep(random.uniform(lo, hi))


async def _run(start: date, end: date, headed: bool) -> None:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    summary: dict = {
        "range": [start.isoformat(), end.isoformat()],
        "mode": "local-stealth",
        "headed": headed,
        "listing_rows": None,
        "captured_cases": [],
        "captcha_hits": [],
        "errors": [],
    }

    async with async_playwright() as p:
        browser = await p.chromium.launch(
            headless=not headed,
            args=[
                "--disable-blink-features=AutomationControlled",
                "--no-sandbox",
            ],
        )
        context = await browser.new_context(
            user_agent=UA,
            viewport={"width": 1366, "height": 850},
            locale="en-US",
            timezone_id="America/Chicago",
        )
        page = await context.new_page()
        await stealth_async(page)

        print(f"[probe] loading {ADVANCED_URL}")
        await page.goto(ADVANCED_URL, wait_until="domcontentloaded", timeout=60_000)
        await _human_pause(1.0, 2.0)

        await page.fill("input[name='filingDate.start']", _mmddyyyy(start))
        await _human_pause()
        await page.fill("input[name='filingDate.end']", _mmddyyyy(end))
        await _human_pause()

        await page.select_option("select[name='caseType']", CASE_TYPE_SC)
        await _human_pause(0.6, 1.2)
        try:
            await page.select_option("select[name='classCode']", CLASS_CODE_EVICTION)
        except Exception as e:
            summary["errors"].append(f"classCode select failed: {e!r}")
        await _human_pause(0.6, 1.2)

        await page.click("button[type='submit'], input[type='submit']")
        await page.wait_for_load_state("networkidle", timeout=90_000)
        await _human_pause(1.0, 2.0)

        list_html = await page.content()
        list_path = OUT_DIR / f"listing_{start.isoformat()}_{end.isoformat()}.html"
        list_path.write_text(list_html, encoding="utf-8")
        await page.screenshot(path=str(list_path.with_suffix(".png")), full_page=True)
        if CAPTCHA_TEXT_RE.search(list_html):
            summary["captcha_hits"].append({"stage": "listing", "url": page.url})
            print("[probe] CAPTCHA on listing.")

        # Collect first N unique caseNo detail anchors
        hrefs = await page.eval_on_selector_all(
            "a[href*='caseDetail.html']",
            "els => els.map(e => e.getAttribute('href'))",
        )
        seen: set[str] = set()
        targets: list[tuple[str, str]] = []
        for h in hrefs:
            if not h:
                continue
            m = re.search(r"caseNo=([0-9A-Z]+)", h)
            if not m or m.group(1) in seen:
                continue
            seen.add(m.group(1))
            full = h if h.startswith("http") else f"https://wcca.wicourts.gov/{h.lstrip('/')}"
            targets.append((m.group(1), full))
            if len(targets) >= MAX_DETAIL_CAPTURES:
                break

        summary["listing_rows"] = len(hrefs)
        print(f"[probe] listing: {len(hrefs)} detail anchors; capturing {len(targets)} unique cases")

        for case_no, url in targets:
            try:
                await _human_pause(1.5, 3.0)
                # Move mouse + click via the anchor when possible to mimic user
                clicked = False
                anchor = await page.query_selector(f"a[href*='caseNo={case_no}']")
                if anchor:
                    try:
                        await anchor.scroll_into_view_if_needed()
                        await _human_pause(0.3, 0.8)
                        await anchor.click()
                        await page.wait_for_load_state("networkidle", timeout=60_000)
                        clicked = True
                    except Exception:
                        pass
                if not clicked:
                    await page.goto(url, wait_until="domcontentloaded", timeout=60_000)
                    await page.wait_for_load_state("networkidle", timeout=60_000)

                html = await page.content()
                dpath = OUT_DIR / f"detail_{case_no}.html"
                dpath.write_text(html, encoding="utf-8")
                await page.screenshot(path=str(dpath.with_suffix(".png")), full_page=True)
                hit = bool(CAPTCHA_TEXT_RE.search(html))
                summary["captured_cases"].append({
                    "case_no": case_no,
                    "url": page.url,
                    "captcha": hit,
                    "bytes": len(html),
                })
                print(f"[probe]   {case_no}: {'CAPTCHA' if hit else 'ok'}  {len(html)} bytes")
                if hit:
                    summary["captcha_hits"].append({"stage": "detail", "case_no": case_no})

                # Back to results so the next anchor click looks natural
                try:
                    await page.go_back(wait_until="domcontentloaded", timeout=60_000)
                    await page.wait_for_load_state("networkidle", timeout=60_000)
                except Exception:
                    pass
            except Exception as e:
                summary["errors"].append(f"{case_no}: {e!r}")
                print(f"[probe]   {case_no}: ERROR {e!r}")

        await browser.close()

    (OUT_DIR / "summary.json").write_text(json.dumps(summary, indent=2), encoding="utf-8")
    print(f"[probe] wrote {OUT_DIR}/summary.json")
    print(json.dumps(summary, indent=2))


if __name__ == "__main__":
    start, end, headed = _parse_args(sys.argv[1:])
    asyncio.run(_run(start, end, headed))
