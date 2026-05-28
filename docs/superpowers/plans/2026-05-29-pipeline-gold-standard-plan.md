# Pipeline Gold Standard Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Ship the two Spec 1 deliverables — a reference doc that codifies the five-layer pipeline contract, and a verifier script that audits the running production environment against that contract.

**Architecture:** A single Python script (`scripts/verify_pipeline_health.py`) that runs five independent check functions (env, schema, scrapers, searchbug cap, GHL stages) and prints a grouped report with OK/FLAG/FAIL per check. The verifier exits non-zero on any FAIL so it can be wired into pre-deploy hooks later. No changes to `pipeline/`, `services/`, or `models/` — the verifier reads existing modules to discover what's scheduled and what's expected.

**Tech Stack:** Python 3.13, pytest + pytest-asyncio, python-dotenv, supabase-py (already in repo), sqlite3 (stdlib). All dependencies already present.

**Spec reference:** [docs/superpowers/specs/2026-05-29-pipeline-gold-standard-design.md](../specs/2026-05-29-pipeline-gold-standard-design.md)

---

## File Structure

**To create:**
- `docs/pipeline_gold_standard.md` — 1-2 page reference; the operator's quick-look version of the spec
- `scripts/verify_pipeline_health.py` — the audit script
- `tests/test_verify_pipeline_health.py` — unit tests for each check function

**To modify:** none

**Why these boundaries:** The verifier is a single file because each check is small (~30 lines) and they share a `CheckResult` dataclass. Splitting into a package would be premature. Tests mirror the source file 1:1.

---

## Task 1: Write the operator-facing reference doc

**Files:**
- Create: `docs/pipeline_gold_standard.md`

This is a condensed, operator-facing version of the spec. Where the spec is 250 lines of "why and what", this is a 1-page "if you only read one thing, read this" reference.

- [ ] **Step 1: Write the doc**

Create `docs/pipeline_gold_standard.md` with this exact content:

```markdown
# Pipeline Gold Standard (tenant track)

A scraper stays on the cron schedule only if every layer below meets its bar.
Reference; full reasoning in `docs/superpowers/specs/2026-05-29-pipeline-gold-standard-design.md`.

## Layer 1 — Scraper

Required `Filing` fields (non-null, non-placeholder): `case_number`,
`tenant_name`, `property_address` (must contain a digit + a 5-digit ZIP),
`landlord_name`, `filing_date` (within last 14 days), `state`, `county`,
`notice_type`, `source_url`.

Optional: `court_date`, `claim_amount`, `property_type_hint`.

**Pass-rate bar:** ≥85% of the last 100 filings pass `gate_address` AND
`gate_name` *without* LLM recovery. LLM is a safety net, not load-bearing.

**Runtime budget:** ≤20 minutes per county for a 2-day lookback.

**Failure handling:** detect portal maintenance and raise a specific
exception; set `scraper.last_error` for the cron job's error handler.

## Layer 2 — Gates

Existing 9-gate filter in `pipeline/gates.py` is the contract. Each
rejection increments a named `run_metrics` counter. `gate_address` and
`gate_name` get LLM rescue when `LLM_RECOVERY_ENABLED=true`.

## Layer 3 — SearchBug

- Every qualifying lead reaches `search_tenant_detailed()` and persists
  `searchbug_status` to `lead_contacts`.
- `SEARCHBUG_DAILY_CAP` ≥ `expected_daily_volume × 1.5`.
- Circuit breaker on billing errors → high-priority Pushover (built).

## Layer 4 — GHL push

| SearchBug status | Routing |
|------------------|---------|
| `phone_found` | `GHL_NG_NEW_FILING_STAGE_ID` (or `_COMMERCIAL_STAGE_ID`) + Instantly + Bland |
| `name_mismatch` / `ambiguous` | `GHL_NG_REVIEW_STAGE_ID`; skip Instantly + Bland |
| `no_records` / `no_phone` / `invalid_name` / `account_error` | drop, no push |

**Required env vars:** `GHL_API_KEY`, `GHL_NG_LOCATION_ID`,
`GHL_NG_NEW_FILING_STAGE_ID`, `GHL_NG_REVIEW_STAGE_ID`. The review stage
ID is hard-required — missing it silently drops every review-lane lead.

## Layer 5 — Observability

One Pushover summary per county per run, including all `gate_*` counters,
`searchbug_calls`, `gate_llm_recovered`, `ng_phones_pushed`,
`ng_review_pushed`. All counters persist to `run_metrics` (post-migration
013). Errors at any layer fire `send_job_error`.

## Quick checks

Run `python scripts/verify_pipeline_health.py` before every prod change.
It exits non-zero on FAIL.
```

- [ ] **Step 2: Commit**

```bash
git add docs/pipeline_gold_standard.md
git commit -m "docs: operator-facing pipeline gold standard reference"
```

---

## Task 2: Create verifier skeleton + CheckResult + report printer

**Files:**
- Create: `scripts/verify_pipeline_health.py`
- Create: `tests/test_verify_pipeline_health.py`

- [ ] **Step 1: Write failing test for `print_report`**

Create `tests/test_verify_pipeline_health.py`:

```python
"""Unit tests for scripts/verify_pipeline_health.py."""
from __future__ import annotations

import io
import sys
from contextlib import redirect_stdout
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from scripts.verify_pipeline_health import CheckResult, print_report


def test_check_result_dataclass_fields():
    r = CheckResult(
        layer="env", name="SUPABASE_URL", status="OK", detail="set"
    )
    assert r.layer == "env"
    assert r.status == "OK"
    assert r.fix_hint is None


def test_print_report_groups_by_layer():
    results = [
        CheckResult("env", "SUPABASE_URL", "OK", "set"),
        CheckResult("env", "GHL_NG_REVIEW_STAGE_ID", "FAIL", "not set", "Railway env"),
        CheckResult("schema", "lead_contacts.searchbug_status", "OK", "present"),
    ]
    buf = io.StringIO()
    with redirect_stdout(buf):
        print_report(results)
    out = buf.getvalue()
    assert "=== env ===" in out
    assert "=== schema ===" in out
    assert "[OK]" in out
    assert "[FAIL]" in out
    assert "GHL_NG_REVIEW_STAGE_ID" in out
    assert "Railway env" in out  # fix hint shown


def test_print_report_summary_counts():
    results = [
        CheckResult("env", "a", "OK", ""),
        CheckResult("env", "b", "FLAG", ""),
        CheckResult("env", "c", "FAIL", ""),
        CheckResult("env", "d", "OK", ""),
    ]
    buf = io.StringIO()
    with redirect_stdout(buf):
        print_report(results)
    out = buf.getvalue()
    assert "2 OK" in out
    assert "1 FLAG" in out
    assert "1 FAIL" in out
```

- [ ] **Step 2: Run test to verify it fails**

```bash
cd "d:\Freelance Projects\EvictionCommand\leadgen"
python -m pytest tests/test_verify_pipeline_health.py -v
```

Expected: ImportError or ModuleNotFoundError ("scripts.verify_pipeline_health" doesn't exist yet)

- [ ] **Step 3: Write the skeleton + CheckResult + print_report**

Create `scripts/verify_pipeline_health.py`:

```python
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
    # Subsequent tasks will populate these
    print_report(results)

    has_fail = any(r.status == "FAIL" for r in results)
    has_flag = any(r.status == "FLAG" for r in results)
    return 1 if (has_fail or (args.strict and has_flag)) else 0


if __name__ == "__main__":
    raise SystemExit(main())
```

- [ ] **Step 4: Run test to verify it passes**

```bash
python -m pytest tests/test_verify_pipeline_health.py -v
```

Expected: 3 passed.

- [ ] **Step 5: Commit**

```bash
git add scripts/verify_pipeline_health.py tests/test_verify_pipeline_health.py
git commit -m "feat: verify_pipeline_health skeleton (CheckResult + report printer)"
```

---

## Task 3: Env var check

**Files:**
- Modify: `scripts/verify_pipeline_health.py` (add `check_env_vars()`)
- Modify: `tests/test_verify_pipeline_health.py` (add tests)

Required env vars and the cross-layer rule: if `TENANT_TRACK_ENABLED` is true (or unset, since the default is true), `GHL_NG_REVIEW_STAGE_ID` must be set — otherwise every review-lane lead silently drops.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_verify_pipeline_health.py`:

```python
from scripts.verify_pipeline_health import check_env_vars


def test_check_env_vars_all_set(monkeypatch):
    monkeypatch.setenv("SUPABASE_URL", "https://x.supabase.co")
    monkeypatch.setenv("SUPABASE_SERVICE_ROLE_KEY", "k")
    monkeypatch.setenv("SEARCHBUG_CO_CODE", "co")
    monkeypatch.setenv("SEARCHBUG_API_KEY", "k")
    monkeypatch.setenv("BATCHDATA_API_KEY", "k")
    monkeypatch.setenv("GHL_API_KEY", "k")
    monkeypatch.setenv("GHL_NEW_FILING_STAGE_ID", "ec-stage")
    monkeypatch.setenv("GHL_NG_LOCATION_ID", "loc")
    monkeypatch.setenv("GHL_NG_NEW_FILING_STAGE_ID", "ng-stage")
    monkeypatch.setenv("GHL_NG_REVIEW_STAGE_ID", "review-stage")
    monkeypatch.setenv("TENANT_TRACK_ENABLED", "true")
    results = check_env_vars()
    assert all(r.status == "OK" for r in results), [
        (r.name, r.status, r.detail) for r in results if r.status != "OK"
    ]


def test_check_env_vars_missing_supabase_url_fails(monkeypatch):
    for k in ["SUPABASE_URL"]:
        monkeypatch.delenv(k, raising=False)
    results = check_env_vars()
    fails = [r for r in results if r.status == "FAIL"]
    assert any(r.name == "SUPABASE_URL" for r in fails)


def test_check_env_vars_tenant_enabled_requires_review_stage(monkeypatch):
    monkeypatch.setenv("SUPABASE_URL", "x")
    monkeypatch.setenv("SUPABASE_SERVICE_ROLE_KEY", "k")
    monkeypatch.setenv("SEARCHBUG_CO_CODE", "co")
    monkeypatch.setenv("SEARCHBUG_API_KEY", "k")
    monkeypatch.setenv("BATCHDATA_API_KEY", "k")
    monkeypatch.setenv("GHL_API_KEY", "k")
    monkeypatch.setenv("GHL_NEW_FILING_STAGE_ID", "x")
    monkeypatch.setenv("GHL_NG_LOCATION_ID", "loc")
    monkeypatch.setenv("GHL_NG_NEW_FILING_STAGE_ID", "ng-stage")
    monkeypatch.setenv("TENANT_TRACK_ENABLED", "true")
    monkeypatch.delenv("GHL_NG_REVIEW_STAGE_ID", raising=False)
    results = check_env_vars()
    review = [r for r in results if r.name == "GHL_NG_REVIEW_STAGE_ID"]
    assert review and review[0].status == "FAIL"
    assert "review-lane leads" in review[0].detail.lower() or "review" in review[0].detail.lower()


def test_check_env_vars_llm_enabled_requires_api_key(monkeypatch):
    monkeypatch.setenv("SUPABASE_URL", "x")
    monkeypatch.setenv("SUPABASE_SERVICE_ROLE_KEY", "k")
    monkeypatch.setenv("SEARCHBUG_CO_CODE", "co")
    monkeypatch.setenv("SEARCHBUG_API_KEY", "k")
    monkeypatch.setenv("BATCHDATA_API_KEY", "k")
    monkeypatch.setenv("GHL_API_KEY", "k")
    monkeypatch.setenv("GHL_NEW_FILING_STAGE_ID", "x")
    monkeypatch.setenv("GHL_NG_LOCATION_ID", "loc")
    monkeypatch.setenv("GHL_NG_NEW_FILING_STAGE_ID", "x")
    monkeypatch.setenv("GHL_NG_REVIEW_STAGE_ID", "x")
    monkeypatch.setenv("LLM_RECOVERY_ENABLED", "true")
    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)
    results = check_env_vars()
    fails = [r for r in results if r.status == "FAIL"]
    assert any(r.name == "OPENROUTER_API_KEY" for r in fails)
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
python -m pytest tests/test_verify_pipeline_health.py -v
```

Expected: ImportError on `check_env_vars`.

- [ ] **Step 3: Add `check_env_vars()` to `scripts/verify_pipeline_health.py`**

Add this function above `main()`:

```python
import os

# Required env vars and the layer they belong to. (key, layer, required).
# Some are conditionally required and handled separately below.
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
```

Update the top imports of the file — add `import os` if not already there (the skeleton already imports os transitively, but make it explicit).

- [ ] **Step 4: Wire `check_env_vars` into `main()`**

In `scripts/verify_pipeline_health.py`, replace the empty `results` list in `main()` with:

```python
    results: list[CheckResult] = []
    results.extend(check_env_vars())
    print_report(results)
```

- [ ] **Step 5: Run tests to verify they pass**

```bash
python -m pytest tests/test_verify_pipeline_health.py -v
```

Expected: all tests pass.

- [ ] **Step 6: Commit**

```bash
git add scripts/verify_pipeline_health.py tests/test_verify_pipeline_health.py
git commit -m "feat: verify_pipeline_health env var checks (+ NG_REVIEW_STAGE_ID required)"
```

---

## Task 4: Schema check

**Files:**
- Modify: `scripts/verify_pipeline_health.py` (add `check_schema()`)
- Modify: `tests/test_verify_pipeline_health.py` (add tests)

Verifies that migrations 012 + 013 are applied:
- `lead_contacts` HAS `searchbug_status`, `searchbug_returned_name`
- `lead_contacts` does NOT have `dnc_status`, `dnc_source`, `dnc_checked_at`
- `run_metrics` HAS `captured`, `gate_out_of_window`, `gate_overdue`,
  `gate_invalid_address`, `gate_bad_name`, `gate_existing_phone`,
  `gate_duplicate_in_run`, `gate_llm_recovered`, `ng_phones_pushed`,
  `ng_review_pushed`, `searchbug_calls`, `searchbug_daily_total`
- `filings` does NOT have any `dnc_*` columns

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_verify_pipeline_health.py`:

```python
from unittest.mock import MagicMock, patch
from scripts.verify_pipeline_health import check_schema


def _mock_supabase(lead_cols: set[str], run_cols: set[str], filing_cols: set[str]):
    """Build a mock _client that returns the given column sets on .select('*').limit(1)."""
    def _table(name: str):
        cols = {"lead_contacts": lead_cols, "run_metrics": run_cols, "filings": filing_cols}[name]
        t = MagicMock()
        # client.table(name).select('*').limit(1).execute() chain
        t.select.return_value.limit.return_value.execute.return_value = MagicMock(
            data=[{c: None for c in cols}]
        )
        return t

    client = MagicMock()
    client.table.side_effect = _table
    return client


def test_check_schema_all_applied():
    lead = {"case_number", "track", "phone", "searchbug_status", "searchbug_returned_name"}
    run = {
        "filings_received", "captured", "gate_out_of_window", "gate_overdue",
        "gate_invalid_address", "gate_bad_name", "gate_existing_phone",
        "gate_duplicate_in_run", "gate_llm_recovered", "ng_phones_pushed",
        "ng_review_pushed", "searchbug_calls", "searchbug_daily_total",
    }
    filings = {"case_number", "tenant_name", "property_address"}
    with patch("scripts.verify_pipeline_health._supabase_client", return_value=_mock_supabase(lead, run, filings)):
        results = check_schema()
    fails = [r for r in results if r.status == "FAIL"]
    assert not fails, [(r.name, r.detail) for r in fails]


def test_check_schema_missing_searchbug_status_fails():
    lead = {"case_number"}  # missing searchbug_status
    run = {
        "captured", "gate_out_of_window", "gate_overdue", "gate_invalid_address",
        "gate_bad_name", "gate_existing_phone", "gate_duplicate_in_run",
        "gate_llm_recovered", "ng_phones_pushed", "ng_review_pushed",
        "searchbug_calls", "searchbug_daily_total",
    }
    filings = {"case_number"}
    with patch("scripts.verify_pipeline_health._supabase_client", return_value=_mock_supabase(lead, run, filings)):
        results = check_schema()
    fails = [r for r in results if r.status == "FAIL"]
    assert any("searchbug_status" in r.name for r in fails)


def test_check_schema_stale_dnc_columns_flag():
    lead = {"searchbug_status", "searchbug_returned_name", "dnc_status"}  # stale
    run = {
        "captured", "gate_out_of_window", "gate_overdue", "gate_invalid_address",
        "gate_bad_name", "gate_existing_phone", "gate_duplicate_in_run",
        "gate_llm_recovered", "ng_phones_pushed", "ng_review_pushed",
        "searchbug_calls", "searchbug_daily_total",
    }
    filings = {"case_number", "dnc_status"}  # also stale
    with patch("scripts.verify_pipeline_health._supabase_client", return_value=_mock_supabase(lead, run, filings)):
        results = check_schema()
    flags = [r for r in results if r.status == "FLAG"]
    assert any("dnc_status" in r.name and "lead_contacts" in r.name for r in flags)
    assert any("dnc_status" in r.name and "filings" in r.name for r in flags)
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
python -m pytest tests/test_verify_pipeline_health.py -v
```

Expected: ImportError on `check_schema`.

- [ ] **Step 3: Add `check_schema()` and `_supabase_client()` helper**

Add to `scripts/verify_pipeline_health.py`:

```python
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

    # Migration 013 — required additions on lead_contacts
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

    # Migration 013 — required additions on run_metrics
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

    # Migration 012 — stale DNC columns should be gone
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
```

- [ ] **Step 4: Wire into `main()`**

In `main()`, after `results.extend(check_env_vars())`:

```python
    results.extend(check_schema())
```

- [ ] **Step 5: Run tests to verify they pass**

```bash
python -m pytest tests/test_verify_pipeline_health.py -v
```

Expected: all schema tests pass.

- [ ] **Step 6: Commit**

```bash
git add scripts/verify_pipeline_health.py tests/test_verify_pipeline_health.py
git commit -m "feat: verify_pipeline_health schema check (migrations 012 + 013)"
```

---

## Task 5: Scheduled-scraper pass-rate check

**Files:**
- Modify: `scripts/verify_pipeline_health.py` (add `check_scheduled_scrapers()`)
- Modify: `tests/test_verify_pipeline_health.py` (add tests)

For each entry in `services.daily_scheduler.SCHEDULED_JOBS`:
1. Map the script_name to (state, county) by reading the corresponding `jobs/run_*.py` (simpler: maintain a small map inside the verifier — we already know the mapping)
2. Pull the last 100 filings for that (state, county) from Supabase
3. Compute % that pass both `gate_address` and `gate_name` without LLM
4. Apply thresholds:
   - ≥85% → OK
   - 60-84% → FLAG (degraded; investigate)
   - <60% → FAIL (drop from schedule until fixed)

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_verify_pipeline_health.py`:

```python
from scripts.verify_pipeline_health import (
    check_scheduled_scrapers,
    _compute_pass_rate,
    SCHEDULED_JOB_COUNTIES,
)


def test_compute_pass_rate_all_pass():
    rows = [
        {"property_address": "123 Main St, Houston, TX 77002", "tenant_name": "Maria Garcia"},
        {"property_address": "456 Elm St, Houston, TX 77003", "tenant_name": "Jose Lopez"},
    ]
    assert _compute_pass_rate(rows) == 1.0


def test_compute_pass_rate_empty_returns_zero():
    assert _compute_pass_rate([]) == 0.0


def test_compute_pass_rate_mixed():
    rows = [
        {"property_address": "123 Main St, Houston, TX 77002", "tenant_name": "Maria Garcia"},  # pass
        {"property_address": "Unknown", "tenant_name": "X X"},  # fail address
        {"property_address": "123 Main St, Houston, TX 77002", "tenant_name": "Acme LLC"},  # fail name (entity)
        {"property_address": "456 Elm St, Houston, TX 77003", "tenant_name": "Carlos Diaz"},  # pass
    ]
    assert _compute_pass_rate(rows) == 0.5


def test_scheduled_job_counties_includes_known_jobs():
    # The map must cover every entry in daily_scheduler.SCHEDULED_JOBS
    from services.daily_scheduler import SCHEDULED_JOBS
    for j in SCHEDULED_JOBS:
        assert j.name in SCHEDULED_JOB_COUNTIES, (
            f"verify_pipeline_health.SCHEDULED_JOB_COUNTIES missing entry for {j.name}"
        )


def test_check_scheduled_scrapers_ok_above_threshold():
    rows = [
        {"property_address": "123 Main St, Houston, TX 77002", "tenant_name": "M Garcia"},
    ] * 100

    def _table_chain(name):
        t = MagicMock()
        chain = t.select.return_value.eq.return_value.eq.return_value.order.return_value.limit.return_value
        chain.execute.return_value = MagicMock(data=rows)
        return t

    client = MagicMock()
    client.table.side_effect = lambda n: _table_chain(n)
    with patch("scripts.verify_pipeline_health._supabase_client", return_value=client):
        results = check_scheduled_scrapers()
    fails = [r for r in results if r.status == "FAIL"]
    assert not fails, [(r.name, r.detail) for r in fails]


def test_check_scheduled_scrapers_fail_below_60_pct():
    # 50% pass rate -> FAIL
    rows = (
        [{"property_address": "123 Main St, Houston, TX 77002", "tenant_name": "Maria Garcia"}] * 50
        + [{"property_address": "Unknown", "tenant_name": "Acme LLC"}] * 50
    )

    def _table_chain(name):
        t = MagicMock()
        chain = t.select.return_value.eq.return_value.eq.return_value.order.return_value.limit.return_value
        chain.execute.return_value = MagicMock(data=rows)
        return t

    client = MagicMock()
    client.table.side_effect = lambda n: _table_chain(n)
    with patch("scripts.verify_pipeline_health._supabase_client", return_value=client):
        results = check_scheduled_scrapers()
    fails = [r for r in results if r.status == "FAIL"]
    assert fails, "expected at least one FAIL"
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
python -m pytest tests/test_verify_pipeline_health.py -v
```

Expected: ImportError on `check_scheduled_scrapers`, etc.

- [ ] **Step 3: Add the function + helpers + county map**

Add to `scripts/verify_pipeline_health.py`:

```python
# Maps daily_scheduler.SCHEDULED_JOBS[].name -> (state, county) as stored in Supabase.
# Keep in sync when new cron jobs are added.
SCHEDULED_JOB_COUNTIES: dict[str, tuple[str, str] | None] = {
    "texas": ("TX", "Harris"),
    "tarrant": ("TX", "Tarrant"),
    "tennessee": ("TN", "Davidson County"),
    "arizona": ("AZ", "Maricopa"),
    "georgia_cobb": ("GA", "Cobb County"),
    # The Franklin raw-push slot bypasses the runner pipeline; we still want
    # to audit the data it produces, so we include it. None for the runner
    # gate check is also valid — listed for completeness.
    "ohio_franklin_raw": ("OH", "Franklin"),
    "ohio_hamilton": ("OH", "Hamilton"),
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


def check_scheduled_scrapers() -> list[CheckResult]:
    """For each scheduled cron job, sample the last 100 filings and compute
    the gate pass rate (without LLM). Apply gold-standard thresholds."""
    from services.daily_scheduler import SCHEDULED_JOBS
    out: list[CheckResult] = []
    client = _supabase_client()

    for job in SCHEDULED_JOBS:
        loc = SCHEDULED_JOB_COUNTIES.get(job.name)
        if loc is None:
            out.append(CheckResult(
                "scrapers", job.name, "FLAG",
                "no (state, county) mapping in SCHEDULED_JOB_COUNTIES; can't audit pass rate",
                fix_hint="add entry to SCHEDULED_JOB_COUNTIES in scripts/verify_pipeline_health.py",
            ))
            continue
        state, county = loc
        try:
            rows = (
                client.table("filings")
                .select("property_address,tenant_name")
                .eq("state", state)
                .eq("county", county)
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
                "no filings persisted yet (new scraper, paused source, or first deploy)",
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
                fix_hint=f"diagnose with python scripts/dry_run_pipeline.py --scraper <name> --max-filings 50",
            ))
        else:
            out.append(CheckResult(
                "scrapers", name, "FAIL",
                f"pass rate {pct} below drop-from-schedule threshold ({int(_PASS_RATE_FAIL*100)}%)",
                fix_hint=f"fix scraper or remove from services/daily_scheduler.SCHEDULED_JOBS until repaired",
            ))

    return out
```

- [ ] **Step 4: Wire into `main()`**

```python
    results.extend(check_scheduled_scrapers())
```

- [ ] **Step 5: Run tests to verify they pass**

```bash
python -m pytest tests/test_verify_pipeline_health.py -v
```

Expected: all tests pass.

- [ ] **Step 6: Commit**

```bash
git add scripts/verify_pipeline_health.py tests/test_verify_pipeline_health.py
git commit -m "feat: verify_pipeline_health per-scraper pass rate audit"
```

---

## Task 6: SearchBug cap headroom check

**Files:**
- Modify: `scripts/verify_pipeline_health.py` (add `check_searchbug_headroom()`)
- Modify: `tests/test_verify_pipeline_health.py` (add tests)

Reads the LOCAL `enrichment_cache.db` daily-cap counter (which is what the script's environment has direct access to). FAIL if at cap, FLAG if >80% utilization.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_verify_pipeline_health.py`:

```python
import sqlite3
from datetime import date as _date
from scripts.verify_pipeline_health import check_searchbug_headroom


def _seed_cap_db(path, used_today: int):
    with sqlite3.connect(str(path)) as con:
        con.execute("CREATE TABLE IF NOT EXISTS daily_cap (date TEXT PRIMARY KEY, count INTEGER NOT NULL DEFAULT 0)")
        con.execute("INSERT OR REPLACE INTO daily_cap (date, count) VALUES (?, ?)", (_date.today().isoformat(), used_today))


def test_check_searchbug_headroom_ok(tmp_path, monkeypatch):
    db = tmp_path / "cache.db"
    _seed_cap_db(db, 30)  # 30/200 used
    monkeypatch.setenv("SEARCHBUG_CACHE_DB_PATH", str(db))
    monkeypatch.setenv("SEARCHBUG_DAILY_CAP", "200")
    results = check_searchbug_headroom()
    assert any(r.status == "OK" for r in results)
    assert not any(r.status == "FAIL" for r in results)


def test_check_searchbug_headroom_flag_above_80_pct(tmp_path, monkeypatch):
    db = tmp_path / "cache.db"
    _seed_cap_db(db, 170)  # 85% used
    monkeypatch.setenv("SEARCHBUG_CACHE_DB_PATH", str(db))
    monkeypatch.setenv("SEARCHBUG_DAILY_CAP", "200")
    results = check_searchbug_headroom()
    assert any(r.status == "FLAG" for r in results)


def test_check_searchbug_headroom_fail_at_cap(tmp_path, monkeypatch):
    db = tmp_path / "cache.db"
    _seed_cap_db(db, 200)  # at cap
    monkeypatch.setenv("SEARCHBUG_CACHE_DB_PATH", str(db))
    monkeypatch.setenv("SEARCHBUG_DAILY_CAP", "200")
    results = check_searchbug_headroom()
    assert any(r.status == "FAIL" for r in results)


def test_check_searchbug_headroom_missing_db_flag(tmp_path, monkeypatch):
    db = tmp_path / "missing.db"
    monkeypatch.setenv("SEARCHBUG_CACHE_DB_PATH", str(db))
    monkeypatch.setenv("SEARCHBUG_DAILY_CAP", "200")
    results = check_searchbug_headroom()
    # No DB yet -> counter assumed 0 (full headroom, OK)
    assert any(r.status == "OK" for r in results)
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
python -m pytest tests/test_verify_pipeline_health.py -v
```

Expected: ImportError on `check_searchbug_headroom`.

- [ ] **Step 3: Add the function**

Add to `scripts/verify_pipeline_health.py`:

```python
import sqlite3
from datetime import date


def check_searchbug_headroom() -> list[CheckResult]:
    """Read the LOCAL enrichment_cache.db daily-cap counter and report
    headroom against SEARCHBUG_DAILY_CAP. The Railway counter is on a
    separate persistent volume and not reachable from here — that's a
    known limitation; this check is for the environment running the script."""
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
```

- [ ] **Step 4: Wire into `main()`**

```python
    results.extend(check_searchbug_headroom())
```

- [ ] **Step 5: Run tests to verify they pass**

```bash
python -m pytest tests/test_verify_pipeline_health.py -v
```

Expected: all tests pass.

- [ ] **Step 6: Commit**

```bash
git add scripts/verify_pipeline_health.py tests/test_verify_pipeline_health.py
git commit -m "feat: verify_pipeline_health SearchBug cap headroom check"
```

---

## Task 7: GHL stage ID presence check

**Files:**
- Modify: `scripts/verify_pipeline_health.py` (add `check_ghl_stage_ids()`)
- Modify: `tests/test_verify_pipeline_health.py` (add tests)

For the under-30-second budget we only verify env presence + length (stage IDs are UUIDs, so very-short values are obviously wrong). Live GHL API resolution is too slow to bake into the default run; defer to a future `--strict` HTTPS round-trip.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_verify_pipeline_health.py`:

```python
from scripts.verify_pipeline_health import check_ghl_stage_ids


def test_check_ghl_stage_ids_all_set(monkeypatch):
    monkeypatch.setenv("GHL_NEW_FILING_STAGE_ID", "11111111-1111-1111-1111-111111111111")
    monkeypatch.setenv("GHL_NG_NEW_FILING_STAGE_ID", "22222222-2222-2222-2222-222222222222")
    monkeypatch.setenv("GHL_NG_REVIEW_STAGE_ID", "33333333-3333-3333-3333-333333333333")
    monkeypatch.setenv("GHL_NG_COMMERCIAL_STAGE_ID", "44444444-4444-4444-4444-444444444444")
    monkeypatch.setenv("TENANT_TRACK_ENABLED", "true")
    results = check_ghl_stage_ids()
    fails = [r for r in results if r.status == "FAIL"]
    assert not fails, [(r.name, r.detail) for r in fails]


def test_check_ghl_stage_ids_short_id_flagged(monkeypatch):
    monkeypatch.setenv("GHL_NEW_FILING_STAGE_ID", "x")
    monkeypatch.setenv("GHL_NG_NEW_FILING_STAGE_ID", "22222222-2222-2222-2222-222222222222")
    monkeypatch.setenv("GHL_NG_REVIEW_STAGE_ID", "33333333-3333-3333-3333-333333333333")
    monkeypatch.setenv("TENANT_TRACK_ENABLED", "true")
    results = check_ghl_stage_ids()
    flags = [r for r in results if r.status == "FLAG"]
    assert any("GHL_NEW_FILING_STAGE_ID" in r.name for r in flags)


def test_check_ghl_stage_ids_missing_review_when_tenant_enabled(monkeypatch):
    monkeypatch.setenv("GHL_NEW_FILING_STAGE_ID", "11111111-1111-1111-1111-111111111111")
    monkeypatch.setenv("GHL_NG_NEW_FILING_STAGE_ID", "22222222-2222-2222-2222-222222222222")
    monkeypatch.delenv("GHL_NG_REVIEW_STAGE_ID", raising=False)
    monkeypatch.setenv("TENANT_TRACK_ENABLED", "true")
    results = check_ghl_stage_ids()
    fails = [r for r in results if r.status == "FAIL"]
    assert any("GHL_NG_REVIEW_STAGE_ID" in r.name for r in fails)
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
python -m pytest tests/test_verify_pipeline_health.py -v
```

Expected: ImportError on `check_ghl_stage_ids`.

- [ ] **Step 3: Add the function**

Add to `scripts/verify_pipeline_health.py`:

```python
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
```

- [ ] **Step 4: Wire into `main()`**

```python
    results.extend(check_ghl_stage_ids())
```

- [ ] **Step 5: Run tests to verify they pass**

```bash
python -m pytest tests/test_verify_pipeline_health.py -v
```

Expected: all tests pass.

- [ ] **Step 6: Commit**

```bash
git add scripts/verify_pipeline_health.py tests/test_verify_pipeline_health.py
git commit -m "feat: verify_pipeline_health GHL stage ID presence check"
```

---

## Task 8: End-to-end smoke + exit code behavior

**Files:**
- Modify: `tests/test_verify_pipeline_health.py` (add main() tests)
- Run: `scripts/verify_pipeline_health.py` against the real environment

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_verify_pipeline_health.py`:

```python
from scripts.verify_pipeline_health import main


def test_main_returns_zero_when_all_ok(monkeypatch):
    # Stub each check to return OK only
    monkeypatch.setattr("scripts.verify_pipeline_health.check_env_vars",
                        lambda: [CheckResult("env", "x", "OK", "")])
    monkeypatch.setattr("scripts.verify_pipeline_health.check_schema",
                        lambda: [CheckResult("schema", "x", "OK", "")])
    monkeypatch.setattr("scripts.verify_pipeline_health.check_scheduled_scrapers",
                        lambda: [CheckResult("scrapers", "x", "OK", "")])
    monkeypatch.setattr("scripts.verify_pipeline_health.check_searchbug_headroom",
                        lambda: [CheckResult("searchbug", "x", "OK", "")])
    monkeypatch.setattr("scripts.verify_pipeline_health.check_ghl_stage_ids",
                        lambda: [CheckResult("ghl", "x", "OK", "")])
    assert main([]) == 0


def test_main_returns_one_on_any_fail(monkeypatch):
    monkeypatch.setattr("scripts.verify_pipeline_health.check_env_vars",
                        lambda: [CheckResult("env", "x", "FAIL", "")])
    monkeypatch.setattr("scripts.verify_pipeline_health.check_schema",
                        lambda: [CheckResult("schema", "x", "OK", "")])
    monkeypatch.setattr("scripts.verify_pipeline_health.check_scheduled_scrapers",
                        lambda: [])
    monkeypatch.setattr("scripts.verify_pipeline_health.check_searchbug_headroom",
                        lambda: [])
    monkeypatch.setattr("scripts.verify_pipeline_health.check_ghl_stage_ids",
                        lambda: [])
    assert main([]) == 1


def test_main_strict_returns_one_on_flag(monkeypatch):
    monkeypatch.setattr("scripts.verify_pipeline_health.check_env_vars",
                        lambda: [CheckResult("env", "x", "FLAG", "")])
    monkeypatch.setattr("scripts.verify_pipeline_health.check_schema",
                        lambda: [])
    monkeypatch.setattr("scripts.verify_pipeline_health.check_scheduled_scrapers",
                        lambda: [])
    monkeypatch.setattr("scripts.verify_pipeline_health.check_searchbug_headroom",
                        lambda: [])
    monkeypatch.setattr("scripts.verify_pipeline_health.check_ghl_stage_ids",
                        lambda: [])
    assert main(["--strict"]) == 1
    assert main([]) == 0
```

- [ ] **Step 2: Run all tests to verify they pass**

```bash
python -m pytest tests/test_verify_pipeline_health.py -v
```

Expected: all tests pass (no new code needed — main() exit-code behavior was already implemented in Task 2).

- [ ] **Step 3: Smoke-run against the real environment**

```bash
python scripts/verify_pipeline_health.py
echo "exit=$?"
```

Expected (against the current 2026-05-29 state of production):
- env layer: all OK except possibly `GHL_NG_REVIEW_STAGE_ID` (not set today)
- schema layer: all OK (migrations 012 + 013 applied earlier today)
- scrapers layer: Harris OK, Franklin OK, Hamilton OK, Maricopa FAIL (~0% pass rate due to address format), Davidson likely OK if recent data exists, Tarrant/Cobb may show "no filings persisted yet" FLAG
- searchbug layer: shows local cap status (100/100 on local today; cap auto-resets at UTC midnight)
- ghl layer: GHL_NG_REVIEW_STAGE_ID = FAIL if still unset

- [ ] **Step 4: Confirm runtime under 30 seconds**

```bash
# Measure wall time
python -c "import time; t=time.time(); import subprocess; subprocess.run(['python','scripts/verify_pipeline_health.py'], check=False); print(f'{time.time()-t:.1f}s')"
```

Expected: under 30s. If over, investigate (most likely culprit: Supabase query latency for the scraper checks — consider parallelizing if needed in a follow-up).

- [ ] **Step 5: Run the full test suite**

```bash
python -m pytest --tb=short -q
```

Expected: all pass except the pre-existing `test_dekalb_scraper` failure (unrelated to this work).

- [ ] **Step 6: Commit**

```bash
git add tests/test_verify_pipeline_health.py
git commit -m "test: verify_pipeline_health main() exit-code behavior + smoke verified"
```

---

## Final review checklist

- [ ] All 5 layers covered by their own check function in `scripts/verify_pipeline_health.py`
- [ ] Each check function returns `list[CheckResult]` so `main()` can aggregate uniformly
- [ ] Cross-layer rules implemented (NG_REVIEW required when tenant track on; OPENROUTER_API_KEY required when LLM enabled)
- [ ] `docs/pipeline_gold_standard.md` is the 1-page operator reference
- [ ] No code changes to `pipeline/`, `services/`, or `models/`
- [ ] Tests use mocks for Supabase + tmp_path for sqlite; no real network/DB calls in tests
- [ ] Smoke run completes in <30s and exits non-zero against today's known issues (Maricopa FAIL, NG_REVIEW_STAGE_ID FAIL)
