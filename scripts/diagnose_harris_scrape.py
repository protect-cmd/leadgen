"""Capture a screenshot at each step of the Harris scrape so we can see
exactly where the flow breaks. Writes PNGs to data/harris_diagnostic/.

Usage:
    python scripts/diagnose_harris_scrape.py --lookback 2
"""
from __future__ import annotations

import argparse
import asyncio
import sys
from datetime import timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from dotenv import load_dotenv

OUT_DIR = Path("data/harris_diagnostic")


async def main_async(lookback_days: int) -> int:
    load_dotenv()
    OUT_DIR.mkdir(parents=True, exist_ok=True)

    from scrapers.dates import court_today
    from scrapers.texas import harris as h
    from scrapers.base_scraper import BaseScraper

    scraper = h.HarrisCountyScraper(lookback_days=lookback_days)
    page = await scraper._launch_browser()

    today = court_today(h.COURT_TIMEZONE)
    start = today - timedelta(days=lookback_days)
    start_str = start.strftime("%m/%d/%Y")
    end_str = today.strftime("%m/%d/%Y")
    print(f"Date range: {start_str} -> {end_str}")

    async def snap(label: str):
        p = OUT_DIR / f"{label}.png"
        await page.screenshot(path=str(p), full_page=True)
        print(f"   {p}")

    async def dump_html(label: str):
        p = OUT_DIR / f"{label}.html"
        p.write_text(await page.content(), encoding="utf-8")
        print(f"   {p}")

    try:
        print("\n[1] Loading portal...")
        await page.goto(h.PORTAL_URL, wait_until="networkidle")
        await snap("01_loaded")

        print("\n[2] Clicking Civil radio...")
        await page.click(h.SELECTOR_RADIO_CIVIL)
        await page.wait_for_timeout(1500)
        await snap("02_after_civil")

        print("\n[3] Reading extract dropdown options...")
        extract_opts = await page.eval_on_selector_all(
            f"{h.SELECTOR_EXTRACT} option",
            "els => els.map(o => ({value: o.value, text: o.innerText}))",
        )
        for o in extract_opts:
            print(f"    extract: value={o['value']!r:6s} text={o['text']!r}")
        eviction_val = next((o["value"] for o in extract_opts if o["value"] != "0"), None)
        if not eviction_val:
            print("     no extract value found")
            await dump_html("03_no_extract_html")
            return 1
        print(f"     selecting extract value={eviction_val!r}")
        await page.select_option(h.SELECTOR_EXTRACT, value=eviction_val)
        await page.wait_for_timeout(1000)
        await snap("03_after_extract")

        print("\n[4] Selecting All Courts (300)...")
        await page.select_option(h.SELECTOR_COURT, value=h.COURT_ALL)
        await page.wait_for_timeout(800)
        await snap("04_after_court")

        print("\n[5] Reading casetype options...")
        casetype_opts = await page.eval_on_selector_all(
            f"{h.SELECTOR_CASETYPE} option",
            "els => els.map(o => ({value: o.value, text: o.innerText}))",
        )
        for o in casetype_opts:
            print(f"    casetype: value={o['value']!r:6s} text={o['text']!r}")
        ct_val = next(
            (o["value"] for o in casetype_opts if o["text"].strip().lower() == "eviction"),
            "0",
        )
        if ct_val == "0":
            print("     Eviction casetype not found")
            await dump_html("05_no_casetype_html")
            return 1
        print(f"     selecting casetype value={ct_val!r}")
        await page.select_option(h.SELECTOR_CASETYPE, value=ct_val)
        await page.wait_for_timeout(500)
        await snap("05_after_casetype")

        print("\n[6] CSV format + dates...")
        await page.select_option(h.SELECTOR_FORMAT, value=h.FORMAT_CSV)
        await page.fill(h.SELECTOR_FDATE, start_str)
        await page.fill(h.SELECTOR_TDATE, end_str)
        await snap("06_form_ready")

        print("\n[7] Inspecting submit button + form state...")
        submit_info = await page.eval_on_selector(
            h.SELECTOR_SUBMIT,
            "el => ({tag: el.tagName, type: el.type, value: el.value, onclick: el.onclick && el.onclick.toString(), disabled: el.disabled})",
        )
        print(f"    submit: {submit_info}")

        # Check if there are any visible error messages on the page
        body_text = await page.eval_on_selector("body", "el => el.innerText")
        for kw in ["error", "captcha", "denied", "blocked", "verify", "robot"]:
            if kw.lower() in body_text.lower():
                idx = body_text.lower().find(kw.lower())
                print(f"      Found {kw!r} in body: ...{body_text[max(0,idx-50):idx+100]}...")

        print("\n[8] Clicking submit and watching for download (45s)...")
        try:
            async with page.expect_download(timeout=45_000) as dl_info:
                await page.click(h.SELECTOR_SUBMIT)
                # snapshot during the wait — see if a new page or popup appeared
                await page.wait_for_timeout(3000)
                await snap("08a_after_click_3s")
                await page.wait_for_timeout(7000)
                await snap("08b_after_click_10s")
            download = await dl_info.value
            print(f"     Download triggered: suggested_filename={download.suggested_filename}")
            return 0
        except Exception as e:
            print(f"     No download: {e!r}")
            await snap("09_no_download")
            await dump_html("09_no_download_html")
            # Last-ditch: check for any alert text, post-click body
            body_text_after = await page.eval_on_selector("body", "el => el.innerText")
            print(f"    body innerText[:500]:\n      {body_text_after[:500]!r}")
            return 1

    finally:
        await scraper._close_browser()


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--lookback", type=int, default=2)
    args = parser.parse_args()
    return asyncio.run(main_async(args.lookback))


if __name__ == "__main__":
    raise SystemExit(main())
