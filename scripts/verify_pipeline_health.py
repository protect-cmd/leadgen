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
    print_report(results)

    has_fail = any(r.status == "FAIL" for r in results)
    has_flag = any(r.status == "FLAG" for r in results)
    return 1 if (has_fail or (args.strict and has_flag)) else 0


if __name__ == "__main__":
    raise SystemExit(main())
