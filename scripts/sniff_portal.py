"""
Portal network sniffer — opens a real browser, lets you interact manually,
and logs every request + response body to sniff_log.json in this directory.

Usage:
    python scripts/sniff_portal.py <url>

Example:
    python scripts/sniff_portal.py "https://gscivildata.shelbycountytn.gov/pls/gnweb/ck_public_qry_cpty.cp_personcase_setup_idx"

Interact with the page normally. Press Ctrl+C to stop and write the log.
"""

import asyncio
import json
import sys
from datetime import datetime
from pathlib import Path

from playwright.async_api import async_playwright

LOG_PATH = Path(__file__).parent / "sniff_log.json"


async def main(url: str) -> None:
    entries: list[dict] = []

    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=False)
        context = await browser.new_context()
        page = await context.new_page()

        async def on_request(request):
            entries.append({
                "type": "request",
                "time": datetime.now().isoformat(),
                "method": request.method,
                "url": request.url,
                "headers": dict(request.headers),
                "post_data": request.post_data,
            })
            if request.method == "POST":
                print(f"\n>>> POST {request.url}")
                if request.post_data:
                    print(f"    BODY: {request.post_data[:500]}")

        async def on_response(response):
            try:
                body = await response.text()
            except Exception:
                body = "<binary or unreadable>"
            entries.append({
                "type": "response",
                "time": datetime.now().isoformat(),
                "status": response.status,
                "url": response.url,
                "body_preview": body[:2000],
            })

        page.on("request", on_request)
        page.on("response", on_response)

        print(f"Opening: {url}")
        print("Interact with the page. Press Ctrl+C when done.\n")
        await page.goto(url, wait_until="load", timeout=60_000)

        try:
            while True:
                await asyncio.sleep(1)
        except KeyboardInterrupt:
            pass

        await browser.close()

    LOG_PATH.write_text(json.dumps(entries, indent=2))
    print(f"\nLog written to {LOG_PATH} ({len(entries)} entries)")


if __name__ == "__main__":
    target = sys.argv[1] if len(sys.argv) > 1 else "https://gscivildata.shelbycountytn.gov/pls/gnweb/ck_public_qry_cpty.cp_personcase_setup_idx"
    asyncio.run(main(target))
