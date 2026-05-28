"""Unit tests for scripts/verify_pipeline_health.py."""
from __future__ import annotations

import io
import sys
from contextlib import redirect_stdout
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from unittest.mock import MagicMock, patch

from scripts.verify_pipeline_health import (
    CheckResult,
    print_report,
    check_env_vars,
    check_schema,
    check_scheduled_scrapers,
    _compute_pass_rate,
    SCHEDULED_JOB_COUNTIES,
)


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


def _clear_all_env(monkeypatch):
    """Strip every env var the verifier reads so each test starts clean."""
    for k in [
        "SUPABASE_URL", "SUPABASE_SERVICE_ROLE_KEY",
        "SEARCHBUG_CO_CODE", "SEARCHBUG_API_KEY",
        "BATCHDATA_API_KEY",
        "GHL_API_KEY", "GHL_NEW_FILING_STAGE_ID",
        "GHL_NG_LOCATION_ID", "GHL_NG_NEW_FILING_STAGE_ID",
        "GHL_NG_REVIEW_STAGE_ID", "GHL_NG_COMMERCIAL_STAGE_ID",
        "TENANT_TRACK_ENABLED", "LANDLORD_TRACK_ENABLED",
        "LLM_RECOVERY_ENABLED", "OPENROUTER_API_KEY",
        "BYPASS_ZIP_FILTER", "CAPTURE_EXPANDED_ZIPS",
        "ENRICHMENT_WINDOW_DAYS",
        "SEARCHBUG_DAILY_CAP", "SEARCHBUG_CACHE_DB_PATH",
    ]:
        monkeypatch.delenv(k, raising=False)


def test_check_env_vars_all_set(monkeypatch):
    _clear_all_env(monkeypatch)
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
    _clear_all_env(monkeypatch)
    results = check_env_vars()
    fails = [r for r in results if r.status == "FAIL"]
    assert any(r.name == "SUPABASE_URL" for r in fails)


def test_check_env_vars_tenant_enabled_requires_review_stage(monkeypatch):
    _clear_all_env(monkeypatch)
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
    results = check_env_vars()
    review = [r for r in results if r.name == "GHL_NG_REVIEW_STAGE_ID"]
    assert review and review[0].status == "FAIL"
    assert "review" in review[0].detail.lower()


def test_check_env_vars_llm_enabled_requires_api_key(monkeypatch):
    _clear_all_env(monkeypatch)
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
    results = check_env_vars()
    fails = [r for r in results if r.status == "FAIL"]
    assert any(r.name == "OPENROUTER_API_KEY" for r in fails)


def _mock_supabase(lead_cols: set[str], run_cols: set[str], filing_cols: set[str]):
    """Build a mock _client that returns the given column sets via
    .table(name).select('*').limit(1).execute()."""
    def _table(name: str):
        cols = {"lead_contacts": lead_cols, "run_metrics": run_cols, "filings": filing_cols}[name]
        t = MagicMock()
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
    lead = {"searchbug_status", "searchbug_returned_name", "dnc_status"}
    run = {
        "captured", "gate_out_of_window", "gate_overdue", "gate_invalid_address",
        "gate_bad_name", "gate_existing_phone", "gate_duplicate_in_run",
        "gate_llm_recovered", "ng_phones_pushed", "ng_review_pushed",
        "searchbug_calls", "searchbug_daily_total",
    }
    filings = {"case_number", "dnc_status"}
    with patch("scripts.verify_pipeline_health._supabase_client", return_value=_mock_supabase(lead, run, filings)):
        results = check_schema()
    flags = [r for r in results if r.status == "FLAG"]
    assert any("dnc_status" in r.name and "lead_contacts" in r.name for r in flags)
    assert any("dnc_status" in r.name and "filings" in r.name for r in flags)


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
        {"property_address": "123 Main St, Houston, TX 77002", "tenant_name": "Maria Garcia"},
        {"property_address": "Unknown", "tenant_name": "X X"},
        {"property_address": "123 Main St, Houston, TX 77002", "tenant_name": "Acme LLC"},
        {"property_address": "456 Elm St, Houston, TX 77003", "tenant_name": "Carlos Diaz"},
    ]
    assert _compute_pass_rate(rows) == 0.5


def test_scheduled_job_counties_includes_known_jobs():
    from services.daily_scheduler import SCHEDULED_JOBS
    for j in SCHEDULED_JOBS:
        assert j.name in SCHEDULED_JOB_COUNTIES, (
            f"SCHEDULED_JOB_COUNTIES missing entry for {j.name}"
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
