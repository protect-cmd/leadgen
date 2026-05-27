"""
Minimal WCCA detail-page probe — skip the form, hit known case URLs directly
with local stealth and report whether CAPTCHA renders.

Cases used are the three Milwaukee evictions named in the prior probe summary:
    2026SC013902, 2026SC013903, 2026SC013904  (countyNo=40 = Milwaukee)

Outputs to tmp/wcca_probe/:
    detail_<caseNo>.html / .png
    detail_summary.json

Usage:
    python scripts/probe_wcca_detail_only.py
    python scripts/probe_wcca_detail_only.py --headed
"""
from __future__ import annotations

import asyncio
import json
import random
import re
import sys
from pathlib import Path

from playwright.async_api import async_playwright
from playwright_stealth import stealth_async

OUT_DIR = Path(__file__).resolve().parent.parent / "tmp" / "wcca_probe"
CAPTCHA_RE = re.compile(r"complete the CAPTCHA|recaptcha|hcaptcha|are you a human", re.IGNORECASE)

CASES = [
    ("2026SC013902", 40),
    ("2026SC013903", 40),
    ("2026SC013904", 40),
]

UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/131.0.0.0 Safari/537.36"
)


async def _run(headed: bool) -> None:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    summary: dict = {"mode": "local-stealth-direct", "captures": []}

    async with async_playwright() as p:
        browser = await p.chromium.launch(
            headless=not headed,
            args=["--disable-blink-features=AutomationControlled", "--no-sandbox"],
        )
        context = await browser.new_context(
            user_agent=UA,
            viewport={"width": 1366, "height": 850},
            locale="en-US",
            timezone_id="America/Chicago",
        )
        page = await context.new_page()
        await stealth_async(page)

        # Warm the session: visit the public landing first
        await page.goto("https://wcca.wicourts.gov/", wait_until="domcontentloaded", timeout=60_000)
        await asyncio.sleep(random.uniform(2.0, 3.5))

        for case_no, county in CASES:
            url = f"https://wcca.wicourts.gov/caseDetail.html?caseNo={case_no}&countyNo={county}"
            print(f"[probe] {case_no} -> {url}")
            try:
                await page.goto(url, wait_until="domcontentloaded", timeout=60_000)
                # WCCA renders progressively; give JS up to ~10s, then capture whatever exists
                await page.wait_for_timeout(8000)
                html = await page.content()
                dpath = OUT_DIR / f"detail_{case_no}.html"
                dpath.write_text(html, encoding="utf-8")
                await page.screenshot(path=str(dpath.with_suffix(".png")), full_page=True)

                hit = bool(CAPTCHA_RE.search(html))
                # Address heuristic: any line containing a Wisconsin city or "WI" plus a 5-digit ZIP
                addr_hit = bool(re.search(r"\b[A-Z][a-zA-Z]+,?\s+WI\s+\d{5}\b", html))
                summary["captures"].append({
                    "case_no": case_no,
                    "captcha": hit,
                    "looks_like_address": addr_hit,
                    "bytes": len(html),
                    "final_url": page.url,
                })
                print(f"[probe]   captcha={hit}  addr_match={addr_hit}  bytes={len(html)}")
                await asyncio.sleep(random.uniform(2.0, 4.0))
            except Exception as e:
                summary["captures"].append({"case_no": case_no, "error": repr(e)})
                print(f"[probe]   ERROR {e!r}")

        await browser.close()

    (OUT_DIR / "detail_summary.json").write_text(json.dumps(summary, indent=2), encoding="utf-8")
    print(json.dumps(summary, indent=2))


if __name__ == "__main__":
    headed = "--headed" in sys.argv
    asyncio.run(_run(headed))
