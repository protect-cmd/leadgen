"""Verify required env vars are present and sane.

Run locally to check your .env, or run on Railway after a deploy to check the
service env. Exits non-zero if anything required is missing - safe to wire
into CI or a Railway pre-deploy hook.

Usage:
    python scripts/verify_env_vars.py            # check current process env
    python scripts/verify_env_vars.py --strict   # also fail on warnings
"""
from __future__ import annotations

import argparse
import os
import sys
from dataclasses import dataclass

from dotenv import load_dotenv


@dataclass
class Check:
    key: str
    required: bool
    notes: str = ""
    allowed_values: tuple[str, ...] | None = None
    secret: bool = False


CHECKS: list[Check] = [
    # Core infra
    Check("SUPABASE_URL", required=True),
    Check("SUPABASE_SERVICE_ROLE_KEY", required=True, secret=True),

    # SearchBug
    Check("SEARCHBUG_CO_CODE", required=True),
    Check("SEARCHBUG_API_KEY", required=True, secret=True),
    Check("SEARCHBUG_DAILY_CAP", required=False, notes="default 100"),

    # BatchData (used by landlord track + property lookup)
    Check("BATCHDATA_API_KEY", required=True, secret=True),

    # Track switches
    Check("TENANT_TRACK_ENABLED", required=False, allowed_values=("true", "false"),
          notes="default true"),
    Check("LANDLORD_TRACK_ENABLED", required=False, allowed_values=("true", "false"),
          notes="default false"),

    # ZIP / capture policy
    Check("CAPTURE_EXPANDED_ZIPS", required=False, allowed_values=("true", "false"),
          notes="default true"),
    Check("BYPASS_ZIP_FILTER", required=False, allowed_values=("true", "false"),
          notes="when true, off-allowlist ZIPs flow through enrichment; overrides CAPTURE_EXPANDED_ZIPS"),
    Check("ENRICHMENT_WINDOW_DAYS", required=False, notes="default 10"),

    # LLM recovery
    Check("LLM_RECOVERY_ENABLED", required=False, allowed_values=("true", "false"),
          notes="default false"),
    Check("OPENROUTER_API_KEY", required=False, secret=True,
          notes="required only when LLM_RECOVERY_ENABLED=true"),
    Check("LLM_RECOVERY_MODEL", required=False,
          notes="default qwen/qwen-2.5-7b-instruct"),

    # GHL
    Check("GHL_API_KEY", required=True, secret=True),
    Check("GHL_NEW_FILING_STAGE_ID", required=True),
    Check("GHL_NG_LOCATION_ID", required=False, notes="required if tenant track on"),
    Check("GHL_NG_NEW_FILING_STAGE_ID", required=False, notes="required if tenant track on"),
    Check("GHL_NG_REVIEW_STAGE_ID", required=False,
          notes="optional; name_mismatch/ambiguous leads drop if blank"),

    # Bland
    Check("BLAND_ENABLED", required=False, allowed_values=("true", "false")),
    Check("AUTO_BLAND_CALLS_ENABLED", required=False, allowed_values=("true", "false")),

    # Instantly
    Check("INSTANTLY_ENABLED", required=False, allowed_values=("true", "false")),

    # Pushover
    Check("PUSHOVER_ENABLED", required=False, allowed_values=("true", "false")),

    # Stale vars - fail loudly if anything still references the removed DNC system
    Check("DNC_PROVIDER", required=False, notes="STALE - DNC removed 2026-05-28"),
    Check("DNC_FAIL_CLOSED", required=False, notes="STALE - DNC removed 2026-05-28"),
    Check("FTC_DNC_DB_PATH", required=False, notes="STALE - DNC removed 2026-05-28"),
    Check("YELLOW_SECOND_CALL_ENABLED", required=False,
          notes="STALE - yellow source removed 2026-05-28"),
]


def _fmt(value: str | None, secret: bool) -> str:
    if value is None:
        return "(unset)"
    if not value:
        return "(empty)"
    if secret:
        if len(value) <= 8:
            return "<set>"
        return f"<set, ...{value[-4:]}>"
    return value


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--strict", action="store_true",
                        help="Exit non-zero on warnings too (not just errors)")
    args = parser.parse_args()

    load_dotenv()

    errors: list[str] = []
    warnings: list[str] = []

    print(f"{'KEY':35s} {'STATUS':10s} VALUE / NOTE")
    print("-" * 90)

    for c in CHECKS:
        val = os.environ.get(c.key)
        is_stale = "STALE" in (c.notes or "")

        if c.required and not val:
            status = "MISSING"
            errors.append(f"{c.key} is required but not set")
        elif is_stale and val:
            status = "STALE!"
            warnings.append(f"{c.key} is set but the feature was removed - clean it up ({c.notes})")
        elif not val:
            status = "-"
        elif c.allowed_values and val.lower() not in c.allowed_values:
            status = "BAD"
            errors.append(
                f"{c.key}={val!r} but allowed values are {c.allowed_values}"
            )
        else:
            status = "OK"

        display_val = _fmt(val, c.secret)
        if c.notes and status in ("-", "OK"):
            display_val = f"{display_val}   ({c.notes})"
        print(f"{c.key:35s} {status:10s} {display_val}")

    # Cross-field check: LLM enabled but no API key
    if (os.environ.get("LLM_RECOVERY_ENABLED", "false").lower() == "true"
            and not os.environ.get("OPENROUTER_API_KEY")):
        errors.append(
            "LLM_RECOVERY_ENABLED=true but OPENROUTER_API_KEY is not set "
            "- recovery will fail-closed on every regex-rejected lead"
        )

    # Cross-field check: both tracks off
    if (os.environ.get("TENANT_TRACK_ENABLED", "true").lower() != "true"
            and os.environ.get("LANDLORD_TRACK_ENABLED", "false").lower() != "true"):
        errors.append(
            "Both TENANT_TRACK_ENABLED and LANDLORD_TRACK_ENABLED are false - "
            "runner will refuse to start"
        )

    print()
    if errors:
        print("ERRORS:")
        for e in errors:
            print(f"  - {e}")
    if warnings:
        print("WARNINGS:")
        for w in warnings:
            print(f"  - {w}")

    if errors:
        return 1
    if warnings and args.strict:
        return 1
    print("OK - env vars look good.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
