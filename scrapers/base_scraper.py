from __future__ import annotations
from abc import ABC, abstractmethod
from playwright.async_api import async_playwright, Browser, Page, Playwright
from models.filing import Filing


class BaseScraper(ABC):
    def __init__(self, headless: bool = True) -> None:
        self.headless = headless
        self._playwright: Playwright | None = None
        self._browser: Browser | None = None

    @abstractmethod
    async def scrape(self) -> list[Filing]: ...

    async def _launch_browser(self) -> Page:
        self._playwright = await async_playwright().start()
        self._browser = await self._playwright.chromium.launch(headless=self.headless)
        context = await self._browser.new_context(
            user_agent=(
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/124.0.0.0 Safari/537.36"
            )
        )
        return await context.new_page()

    async def _close_browser(self) -> None:
        if self._browser:
            await self._browser.close()
        if self._playwright:
            await self._playwright.stop()
