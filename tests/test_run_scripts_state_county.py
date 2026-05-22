"""Verify every county-scheduled run script passes state/county to runner.run.

Pure source inspection — we look at the actual call sites in each script.
A missing state/county results in the Pushover summary saying 'job: Leadgen'
instead of e.g. 'job: TX/Harris', and breaks per-state log filtering.
"""
from __future__ import annotations

import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
JOBS = ROOT / "jobs"

# Scripts that drive enrichment-pipeline runs; raw-only push scripts excluded.
SCRIPTS_AND_STATES = [
    ("run_texas.py", "TX"),
    ("run_tarrant.py", "TX"),
    ("run_tennessee.py", "TN"),
    ("run_arizona.py", "AZ"),
    ("run_georgia.py", "GA"),
    ("run_georgia_cobb.py", "GA"),
    ("run_georgia_dekalb.py", "GA"),
    ("run_ohio.py", "OH"),
    ("run_florida.py", "FL"),
    ("run_indiana.py", "IN"),
    ("run_california.py", "CA"),
]

_RUN_CALL_RE = re.compile(
    r"(?:runner|pipeline_runner)?\.?\s*run\s*\([^)]*\)", re.DOTALL
)


@pytest.mark.parametrize("script_name,expected_state", SCRIPTS_AND_STATES)
def test_run_script_passes_state_and_county(script_name, expected_state):
    src = (JOBS / script_name).read_text(encoding="utf-8")
    matches = list(_RUN_CALL_RE.finditer(src))
    pipeline_calls = [
        m.group(0)
        for m in matches
        if "state=" in m.group(0) or "filings" in m.group(0)
    ]
    assert pipeline_calls, f"{script_name}: no pipeline run() call found"
    for call in pipeline_calls:
        # Skip async-runner.run_script_once or similar; we only check the
        # pipeline runner.run(filings, ...) calls (recognized by 'filings'
        # as the first positional arg).
        if "filings" not in call:
            continue
        assert f'state="{expected_state}"' in call or f"state='{expected_state}'" in call, (
            f"{script_name}: pipeline call is missing state=\"{expected_state}\":\n  {call}"
        )
        assert "county=" in call, (
            f"{script_name}: pipeline call is missing county=:\n  {call}"
        )
