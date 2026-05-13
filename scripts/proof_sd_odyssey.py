"""
Playwright proof: San Diego Odyssey ROA — confirm whether property address
is exposed for out-of-confidentiality UD case numbers pulled from the civil calendar.

Usage:
    python scripts/proof_sd_odyssey.py
    python scripts/proof_sd_odyssey.py --case-numbers 26UD016659C 25UD069316C
    python scripts/proof_sd_odyssey.py --headless false
"""

from __future__ import annotations

import argparse
import asyncio
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from playwright.async_api import async_playwright

DISCLAIMER_URL = "https://odyroa.sdcourt.ca.gov/"
AGREE_URL = "https://odyroa.sdcourt.ca.gov/Cases"

# UD case numbers observed on today's SD civil calendar (2026-05-13)
DEFAULT_CASES = [
    "26UD016659C",   # Alliance Properties LP vs Milligan
    "26UD014813C",   # Gaslamp Hospitality LLC vs Polus
    "26UD010511C",   # Stephens vs Au
    "26UD007674C",   # Pinnacle Parkside Development US LP vs Melkonian
    "25UD069316C",   # United Family Enterprises LLC vs La Frontera Parking Solutions
]

_ADDRESS_PATTERN = re.compile(
    r"\d+\s+\w[\w .]+(?:St(?:reet)?|Ave(?:nue)?|Rd|Road|Dr(?:ive)?|Blvd|Boulevard"
    r"|Ln|Lane|Ct|Court|Way|Pl(?:ace)?|Pkwy|Parkway|Cir(?:cle)?|Ter(?:race)?)"
    r"[^\n]{0,80}",
    re.IGNORECASE,
)


async def accept_disclaimer(page) -> bool:
    await page.goto(DISCLAIMER_URL, wait_until="load", timeout=30_000)
    await page.wait_for_timeout(1500)
    agree_link = page.locator("a[href*='Cases']")
    if await agree_link.count() == 0:
        # Already past disclaimer
        return True
    await agree_link.first.click()
    await page.wait_for_load_state("load", timeout=30_000)
    await page.wait_for_timeout(1500)
    return True


async def search_case(page, case_number: str) -> dict:
    result = {
        "case_number": case_number,
        "found": False,
        "page_text_sample": "",
        "address_hits": [],
        "url": "",
    }

    # Try direct URL by case number first
    await page.goto(AGREE_URL, wait_until="load", timeout=30_000)
    await page.wait_for_timeout(1000)

    # Look for a case number search input
    search_input = page.locator("input[type='text'], input[placeholder*='case' i], input[id*='case' i], input[name*='case' i]")
    if await search_input.count() > 0:
        await search_input.first.fill(case_number)
        await page.keyboard.press("Enter")
        await page.wait_for_timeout(3000)

    result["url"] = page.url
    text = await page.evaluate("() => document.body.innerText")
    result["page_text_sample"] = text[:500]

    if case_number.lower() in text.lower():
        result["found"] = True

    result["address_hits"] = _ADDRESS_PATTERN.findall(text)
    return result


async def main(case_numbers: list[str], headless: bool) -> None:
    async with async_playwright() as pw:
        browser = await pw.chromium.launch(
            headless=headless,
            args=["--disable-blink-features=AutomationControlled"],
        )
        context = await browser.new_context(
            user_agent=(
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/124.0.0.0 Safari/537.36"
            ),
        )
        page = await context.new_page()
        await page.add_init_script(
            "Object.defineProperty(navigator, 'webdriver', {get: () => undefined})"
        )

        print(f"Accepting disclaimer at {DISCLAIMER_URL}")
        await accept_disclaimer(page)
        print(f"  landed at: {page.url}")
        print()

        for case_num in case_numbers:
            print(f"Searching: {case_num}")
            r = await search_case(page, case_num)
            print(f"  URL: {r['url']}")
            print(f"  Found in page: {r['found']}")
            print(f"  Address hits: {r['address_hits'] or 'none'}")
            print(f"  Page sample: {r['page_text_sample'][:200]!r}")
            print()

        await browser.close()


def _build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description="SD Odyssey ROA address proof")
    p.add_argument("--case-numbers", nargs="+", default=DEFAULT_CASES)
    p.add_argument("--headless", type=lambda x: x.lower() != "false", default=True)
    return p


if __name__ == "__main__":
    args = _build_parser().parse_args()
    asyncio.run(main(args.case_numbers, args.headless))
