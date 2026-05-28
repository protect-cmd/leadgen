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
