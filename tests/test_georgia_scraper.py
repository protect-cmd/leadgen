from __future__ import annotations

import pytest

from scrapers.georgia.researchga import ReSearchGAScraper


class FakeLoginPage:
    def __init__(self) -> None:
        self.actions: list[tuple[str, str]] = []

    async def goto(self, url: str, **kwargs) -> None:
        self.actions.append(("goto", url))

    async def wait_for_timeout(self, ms: int) -> None:
        self.actions.append(("wait", str(ms)))

    async def fill(self, selector: str, value: str) -> None:
        self.actions.append(("fill", selector))

    async def click(self, selector: str) -> None:
        self.actions.append(("click", selector))


@pytest.mark.asyncio
async def test_researchga_login_uses_current_tyler_identity_fields(monkeypatch):
    monkeypatch.setenv("RESEARCHGA_EMAIL", "person@example.com")
    monkeypatch.setenv("RESEARCHGA_PASSWORD", "secret-password")

    page = FakeLoginPage()
    scraper = ReSearchGAScraper()

    await scraper._login(page)

    assert ("click", "text=Sign in with Your eFileGA Account") in page.actions
    assert ("fill", "#UserName") in page.actions
    assert ("fill", "#Password") in page.actions
    assert ("click", "button:has-text('Sign In')") in page.actions
