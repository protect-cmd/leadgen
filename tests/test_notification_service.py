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
    monkeypatch.delenv("PUSHOVER_USER_KEYS", raising=False)
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
    monkeypatch.delenv("PUSHOVER_USER_KEYS", raising=False)
    monkeypatch.setattr(notification_service.httpx, "AsyncClient", lambda **kwargs: Client())

    result = await notification_service.send_alert("Title", "Message")

    assert result is False


@pytest.mark.asyncio
async def test_send_run_summary_posts_success_details(monkeypatch):
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
    monkeypatch.delenv("PUSHOVER_USER_KEYS", raising=False)
    monkeypatch.setattr(notification_service.httpx, "AsyncClient", lambda **kwargs: Client())

    result = await notification_service.send_run_summary(
        {
            "state": "TX",
            "county": "Harris",
            "filings_received": 141,
            "duplicates_skipped": 55,
            "address_skipped": 82,
            "batchdata_calls": 7,
            "phones_found": 4,
            "ghl_created": 7,
            "elapsed_seconds": 110.5,
        },
        auto_bland_enabled=False,
    )

    assert result is True
    assert payloads[0][1]["title"] == "Leadgen job complete"
    assert "TX/Harris complete" in payloads[0][1]["message"]
    assert "Filings: 141" in payloads[0][1]["message"]
    assert "BatchData calls: 7" in payloads[0][1]["message"]
    assert "Bland: queued only (auto-call off)" in payloads[0][1]["message"]


@pytest.mark.asyncio
async def test_send_alert_sends_to_multiple_keys_when_pushover_user_keys_set(monkeypatch):
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
            payloads.append(data["user"])
            return Response()

    monkeypatch.setenv("PUSHOVER_ENABLED", "true")
    monkeypatch.setenv("PUSHOVER_APP_TOKEN", "app-token")
    monkeypatch.setenv("PUSHOVER_USER_KEYS", "key-zee,key-sunshine")
    monkeypatch.delenv("PUSHOVER_USER_KEY", raising=False)
    monkeypatch.setattr(notification_service.httpx, "AsyncClient", lambda **kwargs: Client())

    result = await notification_service.send_alert("Title", "Message")

    assert result is True
    assert payloads == ["key-zee", "key-sunshine"]


@pytest.mark.asyncio
async def test_send_alert_one_failed_recipient_does_not_cancel_others(monkeypatch):
    responses = [500, 200]
    calls = []

    class Response:
        def __init__(self, status):
            self.status_code = status
            self.text = "err" if status != 200 else "ok"

    class Client:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            return None

        async def post(self, url, data):
            calls.append(data["user"])
            return Response(responses.pop(0))

    monkeypatch.setenv("PUSHOVER_ENABLED", "true")
    monkeypatch.setenv("PUSHOVER_APP_TOKEN", "app-token")
    monkeypatch.setenv("PUSHOVER_USER_KEYS", "key-a,key-b")
    monkeypatch.delenv("PUSHOVER_USER_KEY", raising=False)
    monkeypatch.setattr(notification_service.httpx, "AsyncClient", lambda **kwargs: Client())

    result = await notification_service.send_alert("Title", "Message")

    assert result is True
    assert calls == ["key-a", "key-b"]


@pytest.mark.asyncio
async def test_send_alert_disabled_sends_nothing(monkeypatch):
    called = False

    class Client:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            return None

        async def post(self, *args, **kwargs):
            nonlocal called
            called = True

    monkeypatch.setenv("PUSHOVER_ENABLED", "false")
    monkeypatch.setenv("PUSHOVER_APP_TOKEN", "app-token")
    monkeypatch.setenv("PUSHOVER_USER_KEYS", "key-zee,key-sunshine")
    monkeypatch.setattr(notification_service.httpx, "AsyncClient", lambda **kwargs: Client())

    result = await notification_service.send_alert("Title", "Message")

    assert result is False
    assert called is False


@pytest.mark.asyncio
async def test_send_alert_all_recipients_fail_returns_false(monkeypatch):
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
    monkeypatch.setenv("PUSHOVER_USER_KEYS", "key-a,key-b")
    monkeypatch.delenv("PUSHOVER_USER_KEY", raising=False)
    monkeypatch.setattr(notification_service.httpx, "AsyncClient", lambda **kwargs: Client())

    result = await notification_service.send_alert("Title", "Message")

    assert result is False
