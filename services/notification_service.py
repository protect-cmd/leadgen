from __future__ import annotations

import logging
import os

import httpx

log = logging.getLogger(__name__)

PUSHOVER_API_URL = "https://api.pushover.net/1/messages.json"


def _truthy(value: str | None) -> bool:
    return (value or "").strip().lower() in {"1", "true", "yes", "on"}


def _config() -> tuple[bool, str, str]:
    enabled = _truthy(os.getenv("PUSHOVER_ENABLED"))
    token = os.getenv("PUSHOVER_APP_TOKEN", "").strip()
    user = os.getenv("PUSHOVER_USER_KEY", "").strip()
    return enabled, token, user


def _message_with_tags(message: str, tags: dict[str, str] | None) -> str:
    if not tags:
        return message
    details = "\n".join(f"{key}: {value}" for key, value in tags.items() if value)
    return f"{message}\n\n{details}" if details else message


async def send_alert(
    title: str,
    message: str,
    *,
    priority: int = 0,
    tags: dict[str, str] | None = None,
) -> bool:
    """Send a Pushover alert. Notification failures never crash the job."""
    enabled, token, user = _config()
    if not enabled:
        return False
    if not token or not user:
        log.warning("Pushover enabled but token/user key is missing")
        return False

    payload = {
        "token": token,
        "user": user,
        "title": title,
        "message": _message_with_tags(message, tags),
        "priority": str(priority),
    }

    try:
        async with httpx.AsyncClient(timeout=10) as client:
            r = await client.post(PUSHOVER_API_URL, data=payload)
    except Exception as e:
        log.warning(f"Pushover alert failed: {e}")
        return False

    if r.status_code != 200:
        log.warning(f"Pushover alert failed {r.status_code}: {r.text[:200]}")
        return False
    return True


async def send_job_error(
    *,
    job: str,
    stage: str,
    error: Exception | str,
    priority: int = 1,
) -> bool:
    return await send_alert(
        "Leadgen job error",
        str(error),
        priority=priority,
        tags={"job": job, "stage": stage},
    )


async def send_run_summary(
    metrics: dict,
    *,
    auto_bland_enabled: bool,
) -> bool:
    state = str(metrics.get("state") or "").strip()
    county = str(metrics.get("county") or "").strip()
    job = "/".join(part for part in (state, county) if part) or "Leadgen"
    elapsed = metrics.get("elapsed_seconds")
    elapsed_text = f"{float(elapsed):.1f}s" if elapsed is not None else "unknown"
    bland_text = (
        "auto-call enabled"
        if auto_bland_enabled
        else "queued only (auto-call off)"
    )

    message = "\n".join(
        [
            f"{job} complete",
            f"Filings: {metrics.get('filings_received', 0)}",
            f"Duplicates: {metrics.get('duplicates_skipped', 0)}",
            f"Discarded/skipped: {metrics.get('address_skipped', 0)}",
            f"BatchData calls: {metrics.get('batchdata_calls', 0)}",
            f"Phones found: {metrics.get('phones_found', 0)}",
            f"GHL created: {metrics.get('ghl_created', 0)}",
            f"Bland: {bland_text}",
            f"Elapsed: {elapsed_text}",
        ]
    )

    return await send_alert(
        "Leadgen job complete",
        message,
        tags={"job": job},
    )
