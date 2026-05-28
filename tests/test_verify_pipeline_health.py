"""Unit tests for scripts/verify_pipeline_health.py."""
from __future__ import annotations

import io
import sys
from contextlib import redirect_stdout
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from scripts.verify_pipeline_health import CheckResult, print_report, check_env_vars


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
