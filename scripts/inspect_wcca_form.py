"""Quick one-off: list all form inputs/selects on WCCA advanced.html."""
import asyncio
from playwright.async_api import async_playwright
from playwright_stealth import stealth_async


async def main() -> None:
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        ctx = await browser.new_context()
        page = await ctx.new_page()
        await stealth_async(page)
        await page.goto("https://wcca.wicourts.gov/advanced.html", wait_until="domcontentloaded", timeout=60_000)
        await page.wait_for_selector("form", timeout=30_000)
        await page.wait_for_timeout(3000)

        fields = await page.evaluate("""
            () => {
                const out = [];
                document.querySelectorAll('input, select, textarea').forEach(el => {
                    out.push({
                        tag: el.tagName,
                        type: el.type || null,
                        name: el.name || null,
                        id: el.id || null,
                        placeholder: el.placeholder || null,
                        options: el.tagName === 'SELECT' ? Array.from(el.options).slice(0, 6).map(o => ({v: o.value, t: o.text.slice(0, 60)})) : null,
                    });
                });
                return out;
            }
        """)
        for f in fields:
            print(f)
        print("---buttons---")
        buttons = await page.evaluate("""
            () => Array.from(document.querySelectorAll('button, input[type=submit]')).map(b => ({text: (b.innerText||b.value||'').slice(0,60), type: b.type, name: b.name, id: b.id}))
        """)
        for b in buttons:
            print(b)
        await browser.close()


if __name__ == "__main__":
    asyncio.run(main())
