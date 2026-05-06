from __future__ import annotations

import pytest

from services import notification_service


@pytest.mark.asyncio
async def test_send_alert_is_noop_when_pushover_disabled(monkeypatch):
    called = False

    class Client:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            return None

        async def post(self, *args, **kwargs):
            nonlocal called
            called = True

    monkeypatch.delenv("PUSHOVER_ENABLED", raising=False)
    monkeypatch.delenv("PUSHOVER_APP_TOKEN", raising=False)
    monkeypatch.delenv("PUSHOVER_USER_KEY", raising=False)
    monkeypatch.setattr(notification_service.httpx, "AsyncClient", lambda **kwargs: Client())

    result = await notification_service.send_alert("Title", "Message")

    assert result is False
    assert called is False


@pytest.mark.asyncio
async def test_send_alert_posts_to_pushover_when_enabled(monkeypatch):
    payloads = []

    class Response:
        status_code = 200
        text = "ok"

    class Client:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            return None

        async def post(self, url, data):
            payloads.append((url, data))
            return Response()

    monkeypatch.setenv("PUSHOVER_ENABLED", "true")
    monkeypatch.setenv("PUSHOVER_APP_TOKEN", "app-token")
    monkeypatch.setenv("PUSHOVER_USER_KEY", "user-key")
    monkeypatch.setattr(notification_service.httpx, "AsyncClient", lambda **kwargs: Client())

    result = await notification_service.send_alert(
        "Leadgen Alert",
        "Tennessee scraper failed",
        priority=1,
        tags={"job": "Tennessee", "stage": "fetch docket list"},
    )

    assert result is True
    assert payloads == [
        (
            notification_service.PUSHOVER_API_URL,
            {
                "token": "app-token",
                "user": "user-key",
                "title": "Leadgen Alert",
                "message": "Tennessee scraper failed\n\njob: Tennessee\nstage: fetch docket list",
                "priority": "1",
            },
        )
    ]


@pytest.mark.asyncio
async def test_send_alert_returns_false_when_pushover_fails(monkeypatch):
    class Response:
        status_code = 500
        text = "server error"

    class Client:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            return None

        async def post(self, *args, **kwargs):
            return Response()

    monkeypatch.setenv("PUSHOVER_ENABLED", "true")
    monkeypatch.setenv("PUSHOVER_APP_TOKEN", "app-token")
    monkeypatch.setenv("PUSHOVER_USER_KEY", "user-key")
    monkeypatch.setattr(notification_service.httpx, "AsyncClient", lambda **kwargs: Client())

    result = await notification_service.send_alert("Title", "Message")

    assert result is False
