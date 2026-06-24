"""Verify the production pipeline against the gold standard contract.

Reads the running environment and persisted Supabase state to confirm every
layer (env, schema, scrapers, SearchBug cap, GHL stages) is in the
expected configuration. Exits non-zero on any FAIL.

Usage:
    python scripts/verify_pipeline_health.py
    python scripts/verify_pipeline_health.py --strict   (treat FLAG as FAIL)

Designed to be wired into a Railway pre-deploy hook later; for now it's a
manual one-shot operator check.

Spec: docs/superpowers/specs/2026-05-29-pipeline-gold-standard-design.md
Quick reference: docs/pipeline_gold_standard.md
"""
from __future__ import annotations

import argparse
import os
import sqlite3
import sys
from collections import Counter, defaultdict
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from dotenv import load_dotenv


@dataclass(frozen=True)
class CheckResult:
    """One audit finding from a single check.

    layer:  "env" | "schema" | "scrapers" | "searchbug" | "ghl"
    name:   short identifier (e.g. "SUPABASE_URL", "Maricopa pass rate")
    status: "OK" | "FLAG" | "FAIL"
    detail: human-readable observation
    fix_hint: optional pointer to where to fix (env var, file:line, etc.)
    """
    layer: str
    name: str
    status: str
    detail: str
    fix_hint: str | None = None


_BASE_ENV: list[tuple[str, str]] = [
    ("SUPABASE_URL", "env"),
    ("SUPABASE_SERVICE_ROLE_KEY", "env"),
    ("SEARCHBUG_CO_CODE", "env"),
    ("SEARCHBUG_API_KEY", "env"),
    ("BATCHDATA_API_KEY", "env"),
    ("GHL_API_KEY", "env"),
    ("GHL_NEW_FILING_STAGE_ID", "env"),
]


def check_env_vars() -> list[CheckResult]:
    """Verify required env vars are set, plus cross-layer rules:
    - If TENANT_TRACK_ENABLED, GHL_NG_LOCATION_ID + GHL_NG_NEW_FILING_STAGE_ID
      + GHL_NG_REVIEW_STAGE_ID must be set.
    - If LLM_RECOVERY_ENABLED, OPENROUTER_API_KEY must be set.
    """
    out: list[CheckResult] = []

    for key, layer in _BASE_ENV:
        val = os.environ.get(key)
        if val:
            out.append(CheckResult(layer, key, "OK", "set"))
        else:
            out.append(CheckResult(
                layer, key, "FAIL", "not set",
                fix_hint=f"set {key} in Railway env (or .env locally)",
            ))

    # Tenant track conditional vars
    tenant_enabled = os.environ.get("TENANT_TRACK_ENABLED", "true").lower() == "true"
    if tenant_enabled:
        for key in ("GHL_NG_LOCATION_ID", "GHL_NG_NEW_FILING_STAGE_ID"):
            val = os.environ.get(key)
            if val:
                out.append(CheckResult("env", key, "OK", "set (tenant track)"))
            else:
                out.append(CheckResult(
                    "env", key, "FAIL",
                    "tenant track enabled but key not set",
                    fix_hint=f"set {key} in Railway env",
                ))
        review = os.environ.get("GHL_NG_REVIEW_STAGE_ID")
        if review:
            out.append(CheckResult("env", "GHL_NG_REVIEW_STAGE_ID", "OK", "set"))
        else:
            out.append(CheckResult(
                "env", "GHL_NG_REVIEW_STAGE_ID", "FAIL",
                "not set; name_mismatch/ambiguous review-lane leads will be dropped silently",
                fix_hint="create a Review stage in the NG GHL subaccount and set GHL_NG_REVIEW_STAGE_ID",
            ))

    # LLM recovery conditional
    llm_enabled = os.environ.get("LLM_RECOVERY_ENABLED", "false").lower() == "true"
    if llm_enabled:
        if os.environ.get("OPENROUTER_API_KEY"):
            out.append(CheckResult("env", "OPENROUTER_API_KEY", "OK", "set (LLM enabled)"))
        else:
            out.append(CheckResult(
                "env", "OPENROUTER_API_KEY", "FAIL",
                "LLM_RECOVERY_ENABLED=true but OPENROUTER_API_KEY missing",
                fix_hint="set OPENROUTER_API_KEY or set LLM_RECOVERY_ENABLED=false",
            ))

    return out


def _supabase_client():
    """Lazy import so tests can patch without touching real Supabase."""
    from services.dedup_service import _client
    return _client


_REQUIRED_LEAD_CONTACT_COLS = {
    "searchbug_status",
    "searchbug_returned_name",
}

_STALE_LEAD_CONTACT_COLS = {
    "dnc_status",
    "dnc_source",
    "dnc_checked_at",
}

_REQUIRED_RUN_METRICS_COLS = {
    "captured",
    "gate_out_of_window",
    "gate_overdue",
    "gate_invalid_address",
    "gate_bad_name",
    "gate_existing_phone",
    "gate_duplicate_in_run",
    "gate_llm_recovered",
    "ng_phones_pushed",
    "ng_review_pushed",
    "searchbug_calls",
    "searchbug_daily_total",
}

_STALE_FILING_COLS = {
    "dnc_status", "dnc_source", "dnc_checked_at",
    "ng_dnc_status", "ng_dnc_source", "ng_dnc_checked_at",
    "dnc_override_source", "dnc_override_notes", "dnc_override_at",
}


def _table_columns(client, table: str) -> set[str]:
    """Discover columns by SELECT * LIMIT 1. Returns empty set on empty table."""
    try:
        r = client.table(table).select("*").limit(1).execute()
        if r.data:
            return set(r.data[0].keys())
    except Exception:
        pass
    return set()


def check_schema() -> list[CheckResult]:
    """Verify migration 012 (DNC drop) and migration 013 (searchbug + metrics) applied."""
    out: list[CheckResult] = []
    client = _supabase_client()

    lead_cols = _table_columns(client, "lead_contacts")
    run_cols = _table_columns(client, "run_metrics")
    filing_cols = _table_columns(client, "filings")

    for col in sorted(_REQUIRED_LEAD_CONTACT_COLS):
        name = f"lead_contacts.{col}"
        if col in lead_cols:
            out.append(CheckResult("schema", name, "OK", "present"))
        else:
            out.append(CheckResult(
                "schema", name, "FAIL",
                "missing; migration 013 not applied",
                fix_hint="apply migrations/013_searchbug_status_and_run_metrics.sql via Supabase SQL Editor",
            ))

    for col in sorted(_REQUIRED_RUN_METRICS_COLS):
        name = f"run_metrics.{col}"
        if col in run_cols:
            out.append(CheckResult("schema", name, "OK", "present"))
        else:
            out.append(CheckResult(
                "schema", name, "FAIL",
                "missing; migration 013 not applied",
                fix_hint="apply migrations/013_searchbug_status_and_run_metrics.sql via Supabase SQL Editor",
            ))

    for col in sorted(_STALE_LEAD_CONTACT_COLS):
        if col in lead_cols:
            out.append(CheckResult(
                "schema", f"lead_contacts.{col}", "FLAG",
                "stale DNC column still present; migration 012 partially or not applied",
                fix_hint="apply migrations/012_drop_dnc.sql via Supabase SQL Editor",
            ))

    for col in sorted(_STALE_FILING_COLS):
        if col in filing_cols:
            out.append(CheckResult(
                "schema", f"filings.{col}", "FLAG",
                "stale DNC column still present; migration 012 partially or not applied",
                fix_hint="apply migrations/012_drop_dnc.sql via Supabase SQL Editor",
            ))

    return out


# Maps daily_scheduler.SCHEDULED_JOBS[].name -> (state, county) as stored
# in the filings table. Note: filings.county is the bare county name
# ("Davidson", "Cobb"), NOT "Davidson County" — that latter form appears
# in run_metrics but not in filings. Verified 2026-05-29.
SCHEDULED_JOB_COUNTIES: dict[str, tuple[str, str] | None] = {
    "texas": ("TX", "Harris"),
    # tarrant + georgia_cobb descheduled 2026-05-29 — see daily_scheduler.py
    # and the follow-up specs (2026-05-29-tarrant-rebuild-design.md,
    # 2026-05-29-cobb-address-enrichment-rebuild-design.md). Map entries
    # removed so the verifier doesn't try to audit descheduled scrapers.
    "tennessee": ("TN", "Davidson"),
    "arizona": ("AZ", "Maricopa"),
    "ohio_franklin_raw": ("OH", "Franklin"),
    "ohio_hamilton": ("OH", "Hamilton"),
    "ohio_montgomery": ("OH", "Montgomery"),
    # Non-filings jobs: ISTS writes ists_judgments, Cosner Drake writes
    # cosner_filings, the chain is post-scrape automation. Mapped to None so the
    # verifier skips them (it audits the prod `filings` table) instead of
    # flagging them as unmapped.
    "ists_harris": None,
    "ists_franklin": None,
    "post_scrape_chain": None,
    "cosner_drake": None,
}

_PASS_RATE_OK = 0.85
_PASS_RATE_FAIL = 0.60


def _compute_pass_rate(rows: list[dict]) -> float:
    """Fraction of rows that pass BOTH gate_address and gate_name (no LLM)."""
    from pipeline import gates as _gates
    if not rows:
        return 0.0
    passed = 0
    for r in rows:
        addr = r.get("property_address") or ""
        name = r.get("tenant_name") or ""
        if _gates.gate_address(addr) and _gates.gate_name(name):
            passed += 1
    return passed / len(rows)


# Only score the *recent* output of a scraper. Without this bound the last-100
# sample reaches weeks back and scores stale, already-superseded rows: Maricopa
# read as 40% FAIL purely because old malformed addresses (fixed 2026-05-30)
# still sat in the window, while every row since passes. Pair this "is current
# output good?" check with check_scraper_freshness ("is it still producing?").
PASS_RATE_LOOKBACK_DAYS = 14


def check_scheduled_scrapers(now: datetime | None = None) -> list[CheckResult]:
    """For each scheduled cron job, sample recent filings and compute the gate
    pass rate (without LLM). Apply gold-standard thresholds."""
    now = now or datetime.now(timezone.utc)
    cutoff = (now - timedelta(days=PASS_RATE_LOOKBACK_DAYS)).isoformat()
    from services.daily_scheduler import SCHEDULED_JOBS
    out: list[CheckResult] = []
    client = _supabase_client()

    for job in SCHEDULED_JOBS:
        if job.name not in SCHEDULED_JOB_COUNTIES:
            out.append(CheckResult(
                "scrapers", job.name, "FLAG",
                "no (state, county) mapping in SCHEDULED_JOB_COUNTIES; can't audit pass rate",
                fix_hint="add entry to SCHEDULED_JOB_COUNTIES in scripts/verify_pipeline_health.py",
            ))
            continue
        loc = SCHEDULED_JOB_COUNTIES[job.name]
        if loc is None:
            continue  # non-filings job (ISTS, post-scrape chain) — nothing to audit here
        state, county = loc
        try:
            rows = (
                client.table("filings")
                .select("property_address,tenant_name")
                .eq("state", state)
                .eq("county", county)
                .gte("scraped_at", cutoff)
                .order("scraped_at", desc=True)
                .limit(100)
                .execute()
                .data
                or []
            )
        except Exception as exc:
            out.append(CheckResult(
                "scrapers", f"{state}/{county}", "FLAG",
                f"Supabase query failed: {exc!r}",
            ))
            continue

        if not rows:
            out.append(CheckResult(
                "scrapers", f"{state}/{county}", "FLAG",
                f"no filings in the last {PASS_RATE_LOOKBACK_DAYS}d "
                "(new scraper, paused source, or dark — see freshness check)",
            ))
            continue

        rate = _compute_pass_rate(rows)
        pct = f"{100 * rate:.0f}%"
        name = f"{state}/{county} (n={len(rows)})"
        if rate >= _PASS_RATE_OK:
            out.append(CheckResult("scrapers", name, "OK", f"pass rate {pct} (>={int(_PASS_RATE_OK*100)}%)"))
        elif rate >= _PASS_RATE_FAIL:
            out.append(CheckResult(
                "scrapers", name, "FLAG",
                f"pass rate {pct} below gold bar ({int(_PASS_RATE_OK*100)}%); LLM recovery may still rescue it but the source is fragile",
                fix_hint="diagnose with python scripts/dry_run_pipeline.py --scraper <name> --max-filings 50",
            ))
        else:
            out.append(CheckResult(
                "scrapers", name, "FAIL",
                f"pass rate {pct} below drop-from-schedule threshold ({int(_PASS_RATE_FAIL*100)}%)",
                fix_hint="fix scraper or remove from services/daily_scheduler.SCHEDULED_JOBS until repaired",
            ))

    return out


# A scheduled scraper that stops persisting new filings is the most damaging
# silent failure: the pass-rate check above happily scores the *stale* rows as
# healthy (Hamilton sat at 95% on 27-day-old data while it had been dark since
# the day its per-case address upgrade shipped). Freshness catches that class.
# Thresholds account for weekend gaps (courts file Mon-Fri) without masking a
# scraper that has genuinely died.
FRESHNESS_FLAG_DAYS = 3
FRESHNESS_FAIL_DAYS = 7


def _age_days(iso_ts: str, now: datetime) -> float | None:
    """Age in days of an ISO-8601 timestamp, or None if unparseable."""
    if not iso_ts:
        return None
    try:
        dt = datetime.fromisoformat(iso_ts.replace("Z", "+00:00"))
    except ValueError:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return (now - dt).total_seconds() / 86400.0


def check_scraper_freshness(now: datetime | None = None) -> list[CheckResult]:
    """For each scheduled job, confirm it has persisted a filing recently.

    Pass rate alone cannot detect a scraper that has silently stopped
    producing — it scores whatever rows exist, however old. This check reads
    the newest filings.scraped_at per county and flags staleness.
    """
    now = now or datetime.now(timezone.utc)
    from services.daily_scheduler import SCHEDULED_JOBS
    out: list[CheckResult] = []
    client = _supabase_client()

    for job in SCHEDULED_JOBS:
        loc = SCHEDULED_JOB_COUNTIES.get(job.name)
        if loc is None:
            continue  # already FLAGged by check_scheduled_scrapers
        state, county = loc
        try:
            rows = (
                client.table("filings")
                .select("scraped_at")
                .eq("state", state)
                .eq("county", county)
                .order("scraped_at", desc=True)
                .limit(1)
                .execute()
                .data
                or []
            )
        except Exception as exc:
            out.append(CheckResult(
                "scrapers", f"{state}/{county} freshness", "FLAG",
                f"Supabase query failed: {exc!r}",
            ))
            continue

        name = f"{state}/{county} freshness"
        if not rows:
            out.append(CheckResult(
                "scrapers", name, "FLAG",
                "no filings persisted yet (new scraper or first deploy)",
            ))
            continue

        age = _age_days(rows[0].get("scraped_at") or "", now)
        if age is None:
            out.append(CheckResult(
                "scrapers", name, "FLAG",
                f"latest scraped_at unparseable: {rows[0].get('scraped_at')!r}",
            ))
            continue

        detail = f"newest filing {age:.1f}d old"
        if age <= FRESHNESS_FLAG_DAYS:
            out.append(CheckResult("scrapers", name, "OK", detail))
        elif age <= FRESHNESS_FAIL_DAYS:
            out.append(CheckResult(
                "scrapers", name, "FLAG",
                f"{detail}; no new filings in >{FRESHNESS_FLAG_DAYS}d (possible missed runs)",
                fix_hint="check Railway scheduler logs for this job",
            ))
        else:
            out.append(CheckResult(
                "scrapers", name, "FAIL",
                f"{detail}; scraper appears dark (no new filings in >{FRESHNESS_FAIL_DAYS}d)",
                fix_hint="scraper has stopped producing — check portal access (IP block?), selectors, and scheduler logs",
            ))

    return out


def check_searchbug_headroom() -> list[CheckResult]:
    """Read the LOCAL enrichment_cache.db daily-cap counter and report
    headroom against SEARCHBUG_DAILY_CAP. The Railway counter is on a
    separate persistent volume and not reachable from here — known
    limitation; this check is for the environment running the script."""
    db_path = os.environ.get("SEARCHBUG_CACHE_DB_PATH", "data/enrichment_cache.db")
    cap = int(os.environ.get("SEARCHBUG_DAILY_CAP", "200"))
    today = date.today().isoformat()

    used = 0
    note_suffix = ""
    if Path(db_path).exists():
        try:
            with sqlite3.connect(db_path) as con:
                row = con.execute(
                    "SELECT count FROM daily_cap WHERE date=?", (today,)
                ).fetchone()
            used = row[0] if row else 0
        except sqlite3.Error as exc:
            return [CheckResult(
                "searchbug", "daily_cap counter", "FLAG",
                f"cache DB present but unreadable: {exc!r}",
                fix_hint=f"inspect {db_path}",
            )]
    else:
        note_suffix = " (local cache DB not yet created)"

    remaining = max(0, cap - used)
    util = used / cap if cap else 0.0
    detail = f"{used}/{cap} used today ({100*util:.0f}%), {remaining} remaining{note_suffix}"

    if used >= cap:
        status = "FAIL"
        hint = "raise SEARCHBUG_DAILY_CAP on Railway or wait for UTC midnight reset"
    elif util > 0.8:
        status = "FLAG"
        hint = "consider raising SEARCHBUG_DAILY_CAP; under 20% headroom"
    else:
        status = "OK"
        hint = None

    return [CheckResult("searchbug", "daily_cap", status, detail, fix_hint=hint)]


def _looks_like_uuid(s: str) -> bool:
    """Lightweight check: 32 hex chars + 4 dashes = 36 total chars."""
    s = s.strip()
    return len(s) >= 32 and s.count("-") >= 4


_GHL_STAGE_KEYS = [
    # (env_key, required_for_tenant, label)
    ("GHL_NEW_FILING_STAGE_ID", False, "EC primary"),
    ("GHL_NG_NEW_FILING_STAGE_ID", True, "NG primary (residential)"),
    ("GHL_NG_COMMERCIAL_STAGE_ID", False, "NG commercial"),
    ("GHL_NG_REVIEW_STAGE_ID", True, "NG review (name_mismatch/ambiguous)"),
]


def check_ghl_stage_ids() -> list[CheckResult]:
    """Verify GHL stage ID env vars are set and look UUID-shaped.

    Live API resolution (call GHL to confirm the stage exists in the
    pipeline) is intentionally NOT done here — too slow for the <30s
    budget and noisy on rate limits. Belongs to a future --strict mode.
    """
    out: list[CheckResult] = []
    tenant_enabled = os.environ.get("TENANT_TRACK_ENABLED", "true").lower() == "true"

    for key, required, label in _GHL_STAGE_KEYS:
        val = (os.environ.get(key) or "").strip()
        if not val:
            if required and tenant_enabled:
                out.append(CheckResult(
                    "ghl", key, "FAIL",
                    f"missing; required ({label})",
                    fix_hint=f"set {key} in Railway env",
                ))
            else:
                out.append(CheckResult("ghl", key, "OK", f"not set; {label} optional"))
            continue
        if not _looks_like_uuid(val):
            out.append(CheckResult(
                "ghl", key, "FLAG",
                f"set but doesn't look UUID-shaped: {val[:20]!r}",
                fix_hint="confirm the stage ID copied correctly from GHL",
            ))
        else:
            out.append(CheckResult("ghl", key, "OK", f"set ({label})"))

    return out


def print_report(results: list[CheckResult]) -> None:
    """Group results by layer and print one section per layer."""
    by_layer: dict[str, list[CheckResult]] = defaultdict(list)
    for r in results:
        by_layer[r.layer].append(r)

    counts: Counter[str] = Counter(r.status for r in results)

    for layer in ["env", "schema", "scrapers", "searchbug", "ghl"]:
        if layer not in by_layer:
            continue
        print(f"\n=== {layer} ===")
        for r in by_layer[layer]:
            line = f"  [{r.status}] {r.name:40s} {r.detail}"
            print(line)
            if r.fix_hint:
                print(f"         fix: {r.fix_hint}")

    print()
    print("-" * 70)
    print(
        f"{counts.get('OK', 0)} OK   "
        f"{counts.get('FLAG', 0)} FLAG   "
        f"{counts.get('FAIL', 0)} FAIL"
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--strict", action="store_true",
        help="Exit non-zero on FLAG as well as FAIL",
    )
    args = parser.parse_args(argv)

    load_dotenv()

    results: list[CheckResult] = []
    results.extend(check_env_vars())
    results.extend(check_schema())
    results.extend(check_scheduled_scrapers())
    results.extend(check_scraper_freshness())
    results.extend(check_searchbug_headroom())
    results.extend(check_ghl_stage_ids())
    print_report(results)

    has_fail = any(r.status == "FAIL" for r in results)
    has_flag = any(r.status == "FLAG" for r in results)
    return 1 if (has_fail or (args.strict and has_flag)) else 0


if __name__ == "__main__":
    raise SystemExit(main())
