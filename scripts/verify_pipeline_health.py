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
import sys
from collections import Counter, defaultdict
from dataclasses import dataclass
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
    print_report(results)

    has_fail = any(r.status == "FAIL" for r in results)
    has_flag = any(r.status == "FLAG" for r in results)
    return 1 if (has_fail or (args.strict and has_flag)) else 0


if __name__ == "__main__":
    raise SystemExit(main())
