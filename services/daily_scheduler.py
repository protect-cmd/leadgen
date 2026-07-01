from __future__ import annotations

import asyncio
import logging
import os
import sys
from dataclasses import dataclass
from datetime import datetime, time, timedelta, timezone
from pathlib import Path

log = logging.getLogger(__name__)

CHECK_INTERVAL_SECONDS = 30
DEFAULT_CATCH_UP_SECONDS = 3600


@dataclass(frozen=True)
class ScheduledJob:
    name: str
    hour: int
    minute: int
    script_name: str
    args: tuple[str, ...] = ()


# Schedule (UTC) is set BACKWARD from the operator's deadline: good leads must be
# ready by 9 PM PHT = 13:00 UTC. So the whole chain (scrape -> ISTS -> post-scrape
# flag/normalize/rent/health) must FINISH before 13:00 UTC. These times put the
# last step (post_scrape_chain 12:40) done ~12:50 UTC. Harris pulls (texas,
# ists_harris, cosner) stay spaced so they don't stack and trip Cloudflare.
# CAVEAT: arizona now runs ~11:10 UTC (~04:10 MST), inside the window where some
# court portals do overnight maintenance. If Maricopa starts coming back empty,
# the freshness monitor (scripts/verify_pipeline_health) will alert — move it
# later (closer to 12:00 UTC) and accept a slightly later AZ readiness.
SCHEDULED_JOBS: tuple[ScheduledJob, ...] = (
    # Indiana statewide ISTS (MyCase judgment leads). Runtime ~57 min —
    # scheduled early so it finishes by ~10:30 UTC, before the VDG chain
    # starts. Writes to ists_judgments; does NOT flow through post_scrape_chain.
    ScheduledJob("ists_indiana", 9, 30, "run_indiana_ists.py"),
    ScheduledJob("texas", 10, 30, "run_texas.py"),
    # Indiana statewide MyCase (all 92 counties). Throttled (2-4s/detail fetch);
    # placed early with the most runway since raw EV-case volume varies by day.
    ScheduledJob("indiana_mycase", 10, 45, "run_indiana.py"),
    # tarrant DESCHEDULED 2026-05-29 - Bright Data tunnel failing on every
    # CaseDetail click (ERR_TUNNEL_CONNECTION_FAILED). See follow-up:
    # docs/superpowers/specs/2026-05-29-tarrant-rebuild-design.md
    # ScheduledJob("tarrant", 12, 10, "run_tarrant.py", args=("--pipe",)),
    ScheduledJob("tennessee", 10, 50, "run_tennessee.py"),
    # Raw insert of single-match filings (Phase 5.2: inline enrichment removed —
    # enrichment is operator-driven via /lists "Enrich selected"). run_arizona
    # persisted ONLY via --pipe, so the prior --notify-only job discarded every
    # scrape and Maricopa got near-zero daily volume. --yes-write-supabase
    # persists raw without enriching.
    ScheduledJob("arizona", 11, 10, "run_arizona.py", args=("--yes-write-supabase", "--notify")),
    # Green VDG scrapers verified live 2026-06-28 (address-complete: Butler 100%,
    # Lorain 90%, Barberton 100%, Duval 100%). Scheduled before 13:00 UTC so leads
    # are ready by 9 PM PHT. Hillsborough is intentionally NOT scheduled — its
    # search endpoint is WAF/403-blocked and needs a residential unlocker.
    ScheduledJob("ohio_lorain", 11, 20, "run_ohio.py",
                 args=("--lookback-days", "2", "--counties", "lorain",
                       "--yes-write-supabase", "--notify")),
    ScheduledJob("ohio_butler", 11, 25, "run_ohio.py",
                 args=("--lookback-days", "2", "--counties", "butler",
                       "--yes-write-supabase", "--notify")),
    ScheduledJob("ohio_barberton", 11, 35, "run_ohio.py",
                 args=("--lookback-days", "2", "--counties", "barberton",
                       "--yes-write-supabase", "--notify")),
    ScheduledJob("florida_duval", 11, 40, "run_florida.py", args=("--counties", "duval")),
    # georgia_cobb DESCHEDULED 2026-05-29 - 200 filings / 4% gate pass rate.
    # Underlying cause: Nominatim geocoder (which Cobb's assessor chain
    # depends on for address enrichment) is unreliable. See follow-up:
    # docs/superpowers/specs/2026-05-29-cobb-address-enrichment-rebuild-design.md
    # ScheduledJob("georgia_cobb", 13, 0, "run_georgia_cobb.py", args=("--pipe", "--notify")),
    ScheduledJob(
        "ohio_franklin_raw",
        11,
        50,
        "../scripts/push_franklin_filings.py",
        args=("--lookback-days", "2", "--yes-write-supabase", "--notify"),
    ),
    # Raw Supabase insert (no inline enrichment), matching the Franklin job.
    # run_ohio only persists with --yes-write-supabase or --pipe; the prior
    # args had neither, so scraped Hamilton filings were silently discarded.
    ScheduledJob(
        "ohio_hamilton",
        12,
        10,
        "run_ohio.py",
        args=("--lookback-days", "2", "--counties", "hamilton",
              "--yes-write-supabase", "--notify"),
    ),
    ScheduledJob(
        "ohio_montgomery",
        12,
        15,
        "run_ohio.py",
        args=("--lookback-days", "2", "--counties", "montgomery",
              "--yes-write-supabase", "--notify"),
    ),
    # --- post-scrape automation (Phase 1) ---
    # ISTS judgment scrapes first so judgments exist before the chain's rent step.
    ScheduledJob("ists_harris", 12, 20, "run_ists_harris.py"),
    # Franklin OH tenant-lost judgments (FCMC eviction CSV). Real upsert to
    # ists_judgments (no --dry-run); plain requests, no browser. See
    # docs/superpowers/specs/2026-06-16-ists-franklin-judgment-leads-design.md
    ScheduledJob("ists_franklin", 12, 25, "run_ists_franklin.py"),
    # Ordered chain: flag_enrichable -> normalize_court_date -> backfill_rent
    # (rent OFF unless RENT_BACKFILL_DAILY_CAP is set).
    ScheduledJob("post_scrape_chain", 12, 40, "../scripts/post_scrape_chain.py"),
    # Cosner Drake Sarasota ClerkNet 3.0 Small Claims filings. Ingest-only into
    # cosner_filings; SearchBug enrichment stays manual (run_cd_enrich).
    ScheduledJob("cosner_sarasota", 12, 45, "run_cd_sarasota.py", args=("--lookback", "2")),
    # Cosner Drake — Harris JP "Cases Filed / Debt Claim" filings. Third Harris
    # pull of the day (after texas 12:00 and ists_harris 13:50); spaced last so
    # the Harris requests don't stack and trip Cloudflare. Ingest-only (scrape ->
    # gate -> upsert to cosner_filings); SearchBug enrichment stays manual
    # (run_cd_enrich).
    ScheduledJob("cosner_drake", 12, 50, "run_cd_harris.py", args=("--lookback", "2")),
    # Indiana statewide MyCase debt suits (Cosner Drake new source, isolated
    # cd_debt_suits table — see migrations/029). Ingest-only, no enrichment/GHL/
    # Bland wiring yet. Spaced after cosner_drake (different portal, no Harris
    # Cloudflare contention).
    ScheduledJob("indiana_debt", 12, 55, "run_indiana_debt.py", args=("--lookback-days", "2")),
)


def is_enabled() -> bool:
    explicit = os.getenv("DASHBOARD_DAILY_SCHEDULER_ENABLED")
    if explicit is not None:
        return explicit.strip().lower() in {"1", "true", "yes", "on"}
    return os.getenv("RAILWAY_SERVICE_NAME") == "leadgen"


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _catch_up_seconds() -> int:
    raw = os.getenv("DASHBOARD_DAILY_SCHEDULER_CATCH_UP_SECONDS", "")
    if not raw:
        return DEFAULT_CATCH_UP_SECONDS
    try:
        return max(0, int(raw))
    except ValueError:
        log.warning("Invalid DASHBOARD_DAILY_SCHEDULER_CATCH_UP_SECONDS=%r", raw)
        return DEFAULT_CATCH_UP_SECONDS


def _target_for_date(now: datetime, *, hour: int, minute: int) -> datetime:
    if now.tzinfo is None:
        now = now.replace(tzinfo=timezone.utc)
    now = now.astimezone(timezone.utc)
    return datetime.combine(
        now.date(),
        time(hour=hour, minute=minute, tzinfo=timezone.utc),
    )


def seconds_until_next_utc_time(now: datetime, *, hour: int, minute: int) -> int:
    target = _target_for_date(now, hour=hour, minute=minute)
    if target <= now:
        target += timedelta(days=1)
    return int((target - now).total_seconds())


def is_due_for_catch_up(
    now: datetime,
    *,
    hour: int,
    minute: int,
    catch_up_seconds: int | None = None,
) -> bool:
    if now.tzinfo is None:
        now = now.replace(tzinfo=timezone.utc)
    now = now.astimezone(timezone.utc)
    target = _target_for_date(now, hour=hour, minute=minute)
    elapsed = (now - target).total_seconds()
    return 0 <= elapsed <= (catch_up_seconds if catch_up_seconds is not None else _catch_up_seconds())


async def run_script_once(script_name: str, args: tuple[str, ...] = ()) -> int:
    script = Path(__file__).resolve().parent.parent / "jobs" / script_name
    log.info("Starting scheduled scrape subprocess: %s", script)
    process = await asyncio.create_subprocess_exec(sys.executable, str(script), *args)
    return_code = await process.wait()
    if return_code:
        log.warning("Scheduled scrape %s exited with code %s", script_name, return_code)
    else:
        log.info("Scheduled scrape %s completed", script_name)
    return return_code


async def run_daily_once() -> int:
    """Run both daily state jobs sequentially for manual/backward-compatible use."""
    return_code = 0
    for job in SCHEDULED_JOBS:
        return_code = max(return_code, await run_script_once(job.script_name, job.args))
    return return_code


async def run_forever() -> None:
    started_dates: set[tuple[str, str]] = set()
    while True:
        now = _utc_now()
        due_jobs = [
            job
            for job in SCHEDULED_JOBS
            if is_due_for_catch_up(now, hour=job.hour, minute=job.minute)
            and (job.name, now.date().isoformat()) not in started_dates
        ]
        if due_jobs:
            for job in due_jobs:
                started_dates.add((job.name, now.date().isoformat()))
                log.info("Starting scheduled %s scrape", job.name)
                await run_script_once(job.script_name, job.args)
            continue

        next_delay = min(
            seconds_until_next_utc_time(now, hour=job.hour, minute=job.minute)
            for job in SCHEDULED_JOBS
        )
        sleep_for = min(CHECK_INTERVAL_SECONDS, max(1, next_delay))
        log.info("Next scheduled scrape check in %ss", sleep_for)
        await asyncio.sleep(sleep_for)
