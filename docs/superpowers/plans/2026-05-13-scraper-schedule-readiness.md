# Scraper Schedule Readiness Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make the newly added Arizona and Cobb scrapers safe to push by fixing failing tests, clarifying scheduling, and aligning production defaults with the requested UTC schedule.

**Architecture:** The dashboard service owns the recurring daily schedule through `services/daily_scheduler.py`; `jobs/run_daily.py` remains as a backward-compatible one-shot runner and should delegate to the same schedule list. Arizona and Cobb only pipe single-match address filings into the existing pipeline, preserving the DNC and Bland safeguards already in `pipeline/runner.py`.

**Tech Stack:** Python, pytest, pdfplumber, FastAPI dashboard startup scheduler, Railway start command.

---

### Task 1: Repair Cobb PDF Parser Tests

**Files:**
- Modify: `tests/test_cobb_scraper.py`

- [ ] **Step 1: Write the failing test harness expectation**

Update `FakePage` so it behaves like the `pdfplumber` page surface used by production code:

```python
class FakePage:
    height = 792

    def __init__(self, text: str):
        self._text = text

    def crop(self, _bbox):
        return self

    def extract_text(self, **_kwargs) -> str:
        return self._text
```

- [ ] **Step 2: Run focused Cobb tests**

Run: `pytest -q tests/test_cobb_scraper.py`

Expected before the harness fix: `AttributeError: 'FakePage' object has no attribute 'height'`.

Expected after the harness fix: all Cobb parser tests pass.

### Task 2: Make Daily Entrypoints Use One Schedule Source

**Files:**
- Modify: `jobs/run_daily.py`
- Modify: `tests/test_daily_job.py`

- [ ] **Step 1: Add tests for four-state daily runner behavior**

Replace the Texas/Tennessee-only assumptions in `tests/test_daily_job.py` with tests that monkeypatch `jobs.run_daily.daily_scheduler.SCHEDULED_JOBS` and assert all due scripts run in order through `daily_scheduler.run_script_once`.

Core assertion:

```python
assert calls == [
    ("run_texas.py", ()),
    ("sleep", 1200),
    ("run_tennessee.py", ()),
    ("sleep", 1200),
    ("run_arizona.py", ("--pipe", "--notify")),
    ("sleep", 1200),
    ("run_georgia_cobb.py", ("--pipe", "--notify")),
]
```

- [ ] **Step 2: Verify the new tests fail against old code**

Run: `pytest -q tests/test_daily_job.py`

Expected: failure showing only Texas and Tennessee are called.

- [ ] **Step 3: Refactor `jobs/run_daily.py`**

Make `jobs/run_daily.py` import `services.daily_scheduler` and iterate `daily_scheduler.SCHEDULED_JOBS`, waiting until each job's configured UTC time before calling `daily_scheduler.run_script_once(job.script_name, job.args)`.

- [ ] **Step 4: Verify daily job tests pass**

Run: `pytest -q tests/test_daily_job.py tests/test_daily_scheduler.py`

Expected: all tests pass.

### Task 3: Align Scheduled Defaults With Requested Daily Windows

**Files:**
- Modify: `jobs/run_arizona.py`
- Modify: `jobs/run_georgia_cobb.py`
- Modify: `tests/test_run_arizona.py`
- Modify: `tests/test_run_georgia_cobb.py`

- [ ] **Step 1: Add assertions for daily default lookbacks**

In `tests/test_run_arizona.py`, assert default `main()` constructs the scraper with `lookback_days=2`.

In `tests/test_run_georgia_cobb.py`, assert default `main()` constructs the scraper with `lookback_days=2`.

- [ ] **Step 2: Verify assertions fail against current defaults**

Run: `pytest -q tests/test_run_arizona.py tests/test_run_georgia_cobb.py`

Expected: failures showing Arizona still defaults to `7` and Cobb still defaults to `30`.

- [ ] **Step 3: Change production defaults**

Set `lookback_days: int = 2` in both `jobs/run_arizona.py` and `jobs/run_georgia_cobb.py`, and update argparse defaults/help text to match.

- [ ] **Step 4: Verify run job tests pass**

Run: `pytest -q tests/test_run_arizona.py tests/test_run_georgia_cobb.py`

Expected: all tests pass.

### Task 4: Align Deployment Docs

**Files:**
- Modify: `railway.toml`
- Modify: `docs/portal_notes.md`
- Modify: `AGENTS.md`

- [ ] **Step 1: Update Railway comment**

Change the stale `railway.toml` comment from the two-state `run_daily.py` description to the dashboard scheduler four-state schedule.

- [ ] **Step 2: Update portal notes**

Change Arizona notes from proof-only wording to: scheduled at 13:40 UTC, pipes only assessor `single_match` addresses, ambiguous/no-match cases stay out of the pipeline.

- [ ] **Step 3: Update repo operating manual**

Change the Railway section in `AGENTS.md` to list:

```text
Texas — 13:00 UTC
Tennessee — 13:20 UTC
Arizona — 13:40 UTC
Cobb (GA) — 14:00 UTC
```

### Task 5: Final Verification

**Files:**
- No source edits expected.

- [ ] **Step 1: Run focused readiness tests**

Run:

```bash
pytest -q tests/test_daily_scheduler.py tests/test_daily_job.py tests/test_run_arizona.py tests/test_run_georgia_cobb.py tests/test_cobb_scraper.py tests/test_arizona_maricopa_scraper.py tests/test_maricopa_assessor.py tests/test_cobb_assessor.py
```

Expected: all tests pass.

- [ ] **Step 2: Run full suite**

Run: `pytest -q`

Expected: all tests pass or only pre-existing intentionally skipped tests remain skipped.

- [ ] **Step 3: Check working tree**

Run: `git diff --stat` and `git status --short`.

Expected: changes are scoped to scheduler/readiness files plus existing unpushed scraper work; no secrets or unrelated reversions.
