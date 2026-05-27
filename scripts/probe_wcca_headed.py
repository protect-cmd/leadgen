"""
WCCA probe — headed + persistent context.

Insight: hCaptcha on WCCA is invisible/bot-triggered. Manual browsing renders
detail pages cleanly with defendant address. Goal here: see if headless+stealth
was the trip wire, by running headed against a persistent profile and
approaching detail pages via the search-results click flow (no direct URL).

Outputs to tmp/wcca_probe/headed/:
    advanced_after_search.html / .png
    detail_<caseNo>.html / .png
    summary.json

Browser profile is stored at tmp/wcca_profile/ — first run is cold, subsequent
runs reuse cookies / cache to look like a returning user.

Usage:
    python scripts/probe_wcca_headed.py                       # SC + 31004 yesterday
    python scripts/probe_wcca_headed.py 2026-05-21
    python scripts/probe_wcca_headed.py --case 2023CV004886   # single case-no path
"""
from __future__ import annotations

import argparse
import asyncio
import json
import random
import re
from datetime import date, timedelta
from pathlib import Path

from playwright.async_api import async_playwright
from playwright_stealth import stealth_async

ROOT = Path(__file__).resolve().parent.parent
OUT_DIR = ROOT / "tmp" / "wcca_probe" / "headed"
PROFILE_DIR = ROOT / "tmp" / "wcca_profile"
CAPTCHA_RE = re.compile(r"complete the CAPTCHA|hcaptcha challenge", re.IGNORECASE)
ADV_URL = "https://wcca.wicourts.gov/advanced.html"

UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/131.0.0.0 Safari/537.36"
)


def _mmddyyyy(d: date) -> str:
    return d.strftime("%m-%d-%Y")


async def _pause(lo: float = 0.6, hi: float = 1.6) -> None:
    await asyncio.sleep(random.uniform(lo, hi))


async def _save(page, name: str) -> tuple[str, bool]:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    html = await page.content()
    (OUT_DIR / f"{name}.html").write_text(html, encoding="utf-8")
    try:
        await page.screenshot(path=str(OUT_DIR / f"{name}.png"), full_page=True)
    except Exception:
        pass
    return html, bool(CAPTCHA_RE.search(html))


async def _do_search_flow(page, filing_date: date) -> list[str]:
    """Fill the advanced form (SC + 31004 = eviction) and submit. Return detail caseNos."""
    print(f"[probe] loading {ADV_URL}")
    await page.goto(ADV_URL, wait_until="domcontentloaded", timeout=60_000)
    await page.wait_for_selector("input[name='filingDate.start']", timeout=30_000)
    await _pause(1.5, 2.5)

    # Filing date range = single day
    await page.fill("input[name='filingDate.start']", _mmddyyyy(filing_date))
    await _pause()
    await page.fill("input[name='filingDate.end']", _mmddyyyy(filing_date))
    await _pause()

    # The Case-Type and Class-Code controls are custom React dropdowns
    # ("Select an option"). Click to open, then click the matching list item.
    # We pick by visible text to avoid relying on internal IDs.
    async def _pick(label_text: str, option_text: str) -> None:
        # Find the dropdown trigger next to its label
        trigger = page.locator(f"label:has-text('{label_text}')").locator("..").locator(":scope >> .select__control, :scope >> [role='combobox'], :scope >> div:has-text('Select an option')").first
        try:
            await trigger.click(timeout=8_000)
        except Exception:
            # Fallback: click any 'Select an option' near label
            box = page.locator(f"text={label_text}").locator("xpath=..").locator("text=Select an option").first
            await box.click(timeout=8_000)
        await _pause(0.4, 0.9)
        await page.get_by_text(option_text, exact=False).first.click(timeout=8_000)
        await _pause(0.4, 0.9)

    try:
        await _pick("Case types", "Small Claims")
    except Exception as e:
        print(f"[probe] case-type pick fallback: {e!r}")
    try:
        await _pick("Class codes", "Small Claims, Eviction")
    except Exception as e:
        print(f"[probe] class-code pick fallback: {e!r}")

    # Submit
    await page.get_by_role("button", name="Search").click()
    await page.wait_for_load_state("domcontentloaded", timeout=60_000)
    await page.wait_for_timeout(4000)

    list_html, list_captcha = await _save(page, f"listing_{filing_date.isoformat()}")
    print(f"[probe] listing captcha={list_captcha}  bytes={len(list_html)}")

    # Pull detail caseNos from anchors
    hrefs = await page.eval_on_selector_all(
        "a[href*='caseDetail.html']",
        "els => els.map(e => e.getAttribute('href'))",
    )
    seen, cases = set(), []
    for h in hrefs:
        m = re.search(r"caseNo=([0-9A-Z]+)", h or "")
        if m and m.group(1) not in seen:
            seen.add(m.group(1))
            cases.append(m.group(1))
        if len(cases) >= 3:
            break
    print(f"[probe] found {len(hrefs)} anchors, {len(cases)} unique caseNos to try")
    return cases


async def _click_detail(page, case_no: str) -> tuple[bool, bool, int]:
    """Click into a detail page from the current results page. Return (captcha, addr_match, bytes)."""
    anchor = page.locator(f"a[href*='caseNo={case_no}']").first
    await anchor.scroll_into_view_if_needed()
    await _pause(0.5, 1.0)
    await anchor.click()
    await page.wait_for_load_state("domcontentloaded", timeout=60_000)
    await page.wait_for_timeout(4000)

    html, captcha = await _save(page, f"detail_{case_no}")
    addr_hit = bool(re.search(r"\bWI\s+\d{5}\b", html))
    return captcha, addr_hit, len(html)


async def _direct_detail(page, case_no: str, county: int) -> tuple[bool, bool, int]:
    url = f"https://wcca.wicourts.gov/caseDetail.html?caseNo={case_no}&countyNo={county}"
    print(f"[probe] direct nav {url}")
    await page.goto(url, wait_until="domcontentloaded", timeout=60_000)
    await page.wait_for_timeout(5000)
    html, captcha = await _save(page, f"detail_{case_no}")
    addr_hit = bool(re.search(r"\bWI\s+\d{5}\b", html))
    return captcha, addr_hit, len(html)


async def _run(filing_date: date | None, case_arg: str | None) -> None:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    PROFILE_DIR.mkdir(parents=True, exist_ok=True)
    summary: dict = {"mode": "headed-persistent", "results": []}

    async with async_playwright() as p:
        ctx = await p.chromium.launch_persistent_context(
            user_data_dir=str(PROFILE_DIR),
            headless=False,
            user_agent=UA,
            viewport={"width": 1366, "height": 900},
            locale="en-US",
            timezone_id="America/Chicago",
            args=["--disable-blink-features=AutomationControlled"],
        )
        page = ctx.pages[0] if ctx.pages else await ctx.new_page()
        await stealth_async(page)

        if case_arg:
            # Direct path: use the simple search page to look up a specific case number
            print(f"[probe] simple-search lookup for {case_arg}")
            await page.goto("https://wcca.wicourts.gov/case.html", wait_until="domcontentloaded", timeout=60_000)
            await _pause(1.0, 2.0)
            await page.fill("input[name='caseNo']", case_arg)
            await _pause()
            await page.get_by_role("button", name="Search").click()
            await page.wait_for_load_state("domcontentloaded", timeout=60_000)
            await page.wait_for_timeout(4000)
            # Click first result anchor
            try:
                anchor = page.locator(f"a[href*='caseNo={case_arg}']").first
                await anchor.click()
                await page.wait_for_load_state("domcontentloaded", timeout=60_000)
                await page.wait_for_timeout(4000)
            except Exception as e:
                print(f"[probe] no anchor; trying View case details button: {e!r}")
                try:
                    await page.get_by_role("button", name="View case details").click()
                    await page.wait_for_timeout(4000)
                except Exception as e2:
                    print(f"[probe] view button fallback failed: {e2!r}")
            html, captcha = await _save(page, f"detail_{case_arg}")
            addr_hit = bool(re.search(r"\bWI\s+\d{5}\b", html))
            summary["results"].append({
                "case_no": case_arg,
                "path": "simple-search-click",
                "captcha": captcha,
                "address_zip_match": addr_hit,
                "bytes": len(html),
            })
            print(f"[probe] {case_arg}: captcha={captcha} addr={addr_hit} bytes={len(html)}")
        else:
            assert filing_date is not None
            cases = await _do_search_flow(page, filing_date)
            for case_no in cases:
                # Need to be on a page that has the anchor; the listing page should still be loaded
                try:
                    captcha, addr_hit, sz = await _click_detail(page, case_no)
                except Exception as e:
                    print(f"[probe] click-detail failed for {case_no}: {e!r}")
                    captcha, addr_hit, sz = await _direct_detail(page, case_no, 40)
                summary["results"].append({
                    "case_no": case_no,
                    "captcha": captcha,
                    "address_zip_match": addr_hit,
                    "bytes": sz,
                })
                print(f"[probe] {case_no}: captcha={captcha} addr={addr_hit} bytes={sz}")
                # Back to results
                try:
                    await page.go_back(wait_until="domcontentloaded", timeout=60_000)
                    await page.wait_for_timeout(2500)
                except Exception:
                    pass

        # Hold for user inspection briefly (real browser stays open ~5s)
        await page.wait_for_timeout(3000)
        await ctx.close()

    (OUT_DIR / "summary.json").write_text(json.dumps(summary, indent=2), encoding="utf-8")
    print(json.dumps(summary, indent=2))


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("date", nargs="?", help="YYYY-MM-DD filing date (default: yesterday)")
    ap.add_argument("--case", help="Look up a single case number via simple search")
    args = ap.parse_args()

    if args.case:
        asyncio.run(_run(None, args.case))
        return

    d = date.fromisoformat(args.date) if args.date else date.today() - timedelta(days=1)
    asyncio.run(_run(d, None))


if __name__ == "__main__":
    main()
