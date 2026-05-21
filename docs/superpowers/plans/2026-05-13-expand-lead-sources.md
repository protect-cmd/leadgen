# Expand Lead Sources Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Wire Arizona Maricopa into the live pipeline, discover and build a Georgia Magistrate Court scraper, and research new green TX county extracts — expanding active lead sources from 2 (TX, TN) toward 4+.

**Architecture:** Three independent workstreams in priority order. Task 1 (AZ) is pure code + scheduler config and can run today. Tasks 2–4 (GA) gate on a research step that determines whether to build. Tasks 5–6 (TX) are the same pattern. Each workstream is self-contained — stop at any research gate if the source is red.

**Tech Stack:** Python 3.13, Playwright (async), requests, pdfplumber, Supabase, GHL, BatchData, Pushover (notification_service)

---

## Workstream A — Arizona Maricopa Pipeline

### Task 1: Fix broken tests from run_arizona.py changes

**Context:** `run_arizona.py` was updated today (2026-05-13) to add `--pipe` flag and rename `ArizonaProofSummary` → `ArizonaRunSummary`, add `piped` param to `build_summary()`, and change the last `to_lines()` entry. The existing tests in `tests/test_run_arizona.py` now fail on all three counts.

**Files:**
- Modify: `tests/test_run_arizona.py`

- [ ] **Step 1: Run existing tests to confirm they fail**

```bash
pytest tests/test_run_arizona.py -v 2>&1
```

Expected failures:
- `TypeError: build_summary() missing 1 required keyword-only argument: 'piped'`
- `AssertionError` on `to_lines()` — last line changed from `"Runner/enrichment/outreach: not called"` to `"Runner/enrichment/outreach: not called (scraper-only mode)"`
- `AttributeError` in `test_main_runs_scraper_only_summary`: `ArizonaRunSummary` vs old class name

- [ ] **Step 2: Update FakeArizonaScraper to include address_matches_by_case**

In `tests/test_run_arizona.py`, find `class FakeArizonaScraper` and add the missing attribute:

```python
class FakeArizonaScraper:
    def __init__(self, *, lookback_days: int, max_cases: int, enrich_addresses: bool):
        self.lookback_days = lookback_days
        self.max_cases = max_cases
        self.enrich_addresses = enrich_addresses
        self.address_match_counts = {
            "single_match": 1,
            "ambiguous": 1,
            "no_match": 1,
            "error": 0,
        }
        self.address_matches_by_case = {
            "CC2026000001": _FakeMatch("single_match"),
            "CC2026000002": _FakeMatch("ambiguous"),
            "CC2026000003": _FakeMatch("no_match"),
        }

    def scrape(self) -> list[Filing]:
        # ... (keep existing return unchanged)
```

Add this dataclass above `FakeArizonaScraper`:

```python
from dataclasses import dataclass as _dc

@_dc
class _FakeMatch:
    status: str
    records: list = None

    def __post_init__(self):
        if self.records is None:
            self.records = []
```

- [ ] **Step 3: Fix test_build_summary_counts_only_single_matches_as_usable**

The call to `build_summary()` is missing `piped=False`. Also `to_lines()` last entry changed. Update the test:

```python
def test_build_summary_counts_only_single_matches_as_usable():
    summary = run_arizona.build_summary(
        filings=[
            Filing(
                case_number="CC2026000001",
                tenant_name="Tenant One",
                property_address="123 W MAIN ST PHOENIX 85001",
                landlord_name="Single Owner LLC",
                filing_date=date(2026, 5, 11),
                court_date=None,
                state="AZ",
                county="Maricopa",
                notice_type="Eviction Action Hearing",
                source_url="https://example.com/1",
            )
        ],
        address_match_counts={
            "single_match": 1,
            "ambiguous": 2,
            "no_match": 3,
            "error": 0,
        },
        max_cases=50,
        lookback_days=7,
        piped=False,
    )

    assert summary.total_filings == 1
    assert summary.usable_single_match == 1
    assert summary.held_for_review == 5
    assert summary.to_lines() == [
        "Arizona / Maricopa scraper-only proof",
        "Max cases: 50",
        "Lookback days: 7",
        "Total filings: 1",
        "Usable single-match addresses: 1",
        "Held for review: 5",
        "Ambiguous owner matches: 2",
        "No owner match: 3",
        "Match errors: 0",
        "Runner/enrichment/outreach: not called (scraper-only mode)",
    ]
```

- [ ] **Step 4: Fix test_main_runs_scraper_only_summary**

The test checks that runner is not called and checks output. Update for new summary text:

```python
@pytest.mark.asyncio
async def test_main_runs_scraper_only_summary(monkeypatch, capsys):
    monkeypatch.setattr(run_arizona, "MaricopaJusticeCourtScraper", FakeArizonaScraper)

    summary = await run_arizona.main(max_cases=50, lookback_days=7, notify=False, pipe=False)

    assert summary.total_filings == 3
    assert summary.usable_single_match == 1
    assert summary.held_for_review == 2
    assert summary.piped is False
    output = capsys.readouterr().out
    assert "Arizona / Maricopa scraper-only proof" in output
    assert "Runner/enrichment/outreach: not called (scraper-only mode)" in output
```

- [ ] **Step 5: Add test for pipe=True mode**

Add a new test that verifies `--pipe` calls runner only for single_match filings and not for others:

```python
@pytest.mark.asyncio
async def test_main_pipe_mode_calls_runner_only_for_single_match(monkeypatch, capsys):
    monkeypatch.setattr(run_arizona, "MaricopaJusticeCourtScraper", FakeArizonaScraper)

    piped_filings = []

    async def fake_runner_run(filings, *, state, county):
        piped_filings.extend(filings)

    import pipeline.runner as pipeline_runner
    monkeypatch.setattr(pipeline_runner, "run", fake_runner_run)

    # Need to patch the import inside run_arizona.main
    import jobs.run_arizona as ra_mod
    import types
    fake_runner_module = types.SimpleNamespace(run=fake_runner_run)
    monkeypatch.setattr(ra_mod, "pipeline_runner", fake_runner_module, raising=False)

    summary = await run_arizona.main(max_cases=50, lookback_days=7, notify=False, pipe=True)

    assert summary.piped is True
    assert summary.usable_single_match == 1
    # Only the single_match filing should have been piped
    assert len(piped_filings) == 1
    assert piped_filings[0].case_number == "CC2026000001"
    output = capsys.readouterr().out
    assert "Runner: called with 1 single-match filings" in output
```

Note: the `pipeline_runner` import inside `main()` uses a local import (`from pipeline import runner as pipeline_runner`). Monkeypatching it requires patching after import. If this test proves difficult to wire up due to local import scoping, simplify to asserting `summary.piped is True` and `summary.usable_single_match == 1` only.

- [ ] **Step 6: Run all Arizona tests — confirm green**

```bash
pytest tests/test_run_arizona.py -v 2>&1
```

Expected: all tests pass.

- [ ] **Step 7: Commit**

```bash
git add tests/test_run_arizona.py
git commit -m "test: fix run_arizona tests for pipe mode and renamed summary class"
```

---

### Task 2: Arizona live pipeline test run

**Context:** `jobs/run_arizona.py --pipe` will call `pipeline.runner.run()` with single-match filings. This triggers BatchData, Supabase writes, GHL contact creation, and potentially Bland calls. Use a small cap (10 cases) for the first live run. Check Supabase and GHL after to confirm leads appear.

**Files:** No code changes — this is a live run and verification step.

- [ ] **Step 1: Run with small cap, pipe enabled, notify enabled**

From the project root with `.env` loaded:

```bash
cd "d:\Freelance Projects\EvictionCommand\leadgen"
python -m dotenv run -- python jobs/run_arizona.py --pipe --notify --max-cases 10 --lookback-days 7 2>&1
```

If `dotenv` run isn't available:
```bash
python -c "from dotenv import load_dotenv; load_dotenv(); import asyncio; from jobs.run_arizona import main; asyncio.run(main(pipe=True, notify=True, max_cases=10, lookback_days=7))"
```

Expected output contains:
- `Arizona / Maricopa pipeline run`
- `Usable single-match addresses: N` (N > 0, typically 1–3 in a 10-case window)
- `Runner: called with N single-match filings`
- Supabase HTTP 201 Created lines for each piped filing
- No `pipeline.runner` errors

- [ ] **Step 2: Verify leads in Supabase**

Check the `filings` table for AZ state entries. Expected: rows with `state=AZ`, `county=Maricopa`, non-Unknown `property_address`.

- [ ] **Step 3: Verify leads in GHL**

Open the EC GHL subaccount contacts. Filter by source or tag for AZ. Expected: new contacts created within the last few minutes matching the piped filings.

- [ ] **Step 4: If run is clean — add Arizona to daily scheduler**

In `services/daily_scheduler.py`, add AZ to `SCHEDULED_JOBS` after Tennessee:

```python
SCHEDULED_JOBS: tuple[ScheduledJob, ...] = (
    ScheduledJob("texas",     13,  0, "run_texas.py"),
    ScheduledJob("tennessee", 13, 20, "run_tennessee.py"),
    ScheduledJob("arizona",   13, 40, "run_arizona.py"),
)
```

In `jobs/run_daily.py`, add AZ import and slot. Find the `_run_state_job("Tennessee", ...)` call and add after it:

```python
await _run_state_job("Arizona", "arizona", lambda: run_arizona.main(pipe=True, notify=True))
```

Add import at top:
```python
from jobs import run_tennessee, run_texas, run_arizona
```

- [ ] **Step 5: Update source_discovery_matrix.md**

Change AZ status from `yellow` to `green` and update the Why/Next action columns to reflect that it is now scheduled with `--pipe`.

- [ ] **Step 6: Run scheduler unit tests**

```bash
pytest tests/test_daily_scheduler.py -v 2>&1
```

Expected: all pass. If any test hardcodes the job count (e.g. `assert len(SCHEDULED_JOBS) == 2`), update to `== 3`.

- [ ] **Step 7: Commit**

```bash
git add services/daily_scheduler.py jobs/run_daily.py docs/source_discovery_matrix.md
git commit -m "feat: add arizona maricopa to daily pipeline schedule"
```

---

## Workstream B — Georgia Magistrate Court

### Task 3: Research Georgia Magistrate Court sources

**Context:** Georgia residential dispossessory filings are handled by county Magistrate Courts, not State Court. Each county has its own portal. Target the highest-volume counties in the Atlanta metro: Fulton, Gwinnett, DeKalb, Cobb. Goal: classify each as green/yellow/red using the same criteria as `docs/source_discovery_matrix.md`.

**Files:**
- Modify: `docs/source_discovery_matrix.md`
- Modify: `docs/portal_notes.md`

- [ ] **Step 1: Research Fulton County Magistrate Court**

Run firecrawl scrapes against the known public-access URLs for Fulton Magistrate:

```bash
firecrawl scrape "https://www.fultoncourt.org/magistrate" --only-main-content -o .firecrawl/fulton-magistrate.md
firecrawl scrape "https://efiling.fultoncourt.org" --only-main-content -o .firecrawl/fulton-efiling.md
```

Classify: Can you enumerate dispossessory filings by date without providing a name? Does the result include property address? Green/yellow/red?

- [ ] **Step 2: Research Gwinnett County Magistrate Court**

```bash
firecrawl scrape "https://www.gwinnettcourts.com/magistrate" --only-main-content -o .firecrawl/gwinnett-magistrate.md
firecrawl scrape "https://ody.gwinnettcourts.com/portal" --only-main-content --wait-for 2000 -o .firecrawl/gwinnett-odyssey.md
```

Classify same criteria.

- [ ] **Step 3: Research DeKalb County Magistrate Court**

```bash
firecrawl scrape "https://www.dekalbcountyga.gov/magistrate-court" --only-main-content -o .firecrawl/dekalb-magistrate.md
```

Classify same criteria.

- [ ] **Step 4: Research Cobb County Magistrate Court**

```bash
firecrawl scrape "https://www.cobbsuperiorcourtclerk.com/magistrate" --only-main-content -o .firecrawl/cobb-magistrate.md
```

Classify same criteria.

- [ ] **Step 5: Update source_discovery_matrix.md**

Add a row per county with findings. Use the exact table format already in the file. Example row for a green source:

```markdown
| GA | Fulton County Magistrate | green | Public docket at {URL} enumerates dispossessory by date; confirmed case number, filing date, plaintiff, defendant, and property address in results. | Build scraper next. |
```

For red sources, document why (name-required, CAPTCHA, no address, paid).

- [ ] **Step 6: Update portal_notes.md**

Add a `## Georgia — Magistrate Courts` section. One subsection per county researched. Include portal URL, access method, fields confirmed present, and any blocking factors. Follow the format of the existing `## Nevada Clark County` section.

- [ ] **Step 7: Commit**

```bash
git add docs/source_discovery_matrix.md docs/portal_notes.md .firecrawl/
git commit -m "docs: georgia magistrate court source discovery"
```

---

### Task 4: Build Georgia Magistrate Court scraper (CONDITIONAL — only if Task 3 finds a green source)

**Skip this task if Task 3 found no green/strong-yellow Georgia Magistrate source.**

Replace `{county}` below with the actual county name found in Task 3 (e.g. `fulton`, `gwinnett`).

**Files:**
- Create: `scrapers/georgia/{county}_magistrate.py`
- Create: `tests/test_georgia_{county}_scraper.py`
- Modify: `jobs/run_georgia.py`

- [ ] **Step 1: Write the failing smoke test**

The test should assert that the scraper returns at least 1 Filing with non-Unknown property_address from a 14-day lookback, using a monkeypatched HTTP response based on a real page sample captured during research.

```python
# tests/test_georgia_{county}_scraper.py
from __future__ import annotations

import pytest
from unittest.mock import patch, MagicMock
from scrapers.georgia.{county}_magistrate import {County}MagistrateScraper

SAMPLE_HTML = """
<html>... (paste actual sample HTML from Task 3 research here) ...</html>
"""

def test_scraper_parses_dispossessory_filings():
    with patch("scrapers.georgia.{county}_magistrate.requests.Session") as mock_session_cls:
        mock_session = MagicMock()
        mock_session_cls.return_value = mock_session
        mock_session.get.return_value.status_code = 200
        mock_session.get.return_value.text = SAMPLE_HTML
        mock_session.get.return_value.raise_for_status = MagicMock()

        scraper = {County}MagistrateScraper(lookback_days=14)
        filings = scraper.scrape()

    assert len(filings) >= 1
    f = filings[0]
    assert f.state == "GA"
    assert f.county == "{County}"
    assert f.case_number != ""
    assert f.landlord_name not in ("", "Unknown")
    assert f.tenant_name not in ("", "Unknown")
    # Property address must be present for this source to be useful
    assert f.property_address not in ("", "Unknown")
```

Run: `pytest tests/test_georgia_{county}_scraper.py -v`
Expected: FAIL (class not yet defined)

- [ ] **Step 2: Implement the scraper**

Create `scrapers/georgia/{county}_magistrate.py` following the pattern of `scrapers/arizona/maricopa.py` (requests-based, no Playwright, unless the research portal required it):

```python
from __future__ import annotations

import logging
import re
from datetime import date, datetime, timedelta

import requests

from models.filing import Filing
from scrapers.dates import court_today

log = logging.getLogger(__name__)

STATE = "GA"
COUNTY = "{County}"
COURT_TIMEZONE = "America/New_York"

# Replace with actual portal URL found in Task 3
_DOCKET_URL = "https://{actual-portal-url}/dispossessory"


class {County}MagistrateScraper:
    """
    Scrapes {County} County Magistrate Court for dispossessory filings.
    Source: {portal URL from Task 3}
    Fields available: case_number, filing_date, plaintiff, defendant, property_address
    """

    def __init__(self, lookback_days: int = 7):
        self.lookback_days = lookback_days
        self.last_error: str | None = None
        self.session = requests.Session()
        self.session.headers.update({"User-Agent": "Mozilla/5.0"})

    def scrape(self) -> list[Filing]:
        self.last_error = None
        today = court_today(COURT_TIMEZONE)
        cutoff = today - timedelta(days=self.lookback_days)

        try:
            raw = self._fetch_docket(cutoff, today)
        except Exception as e:
            self.last_error = f"fetch failed: {e}"
            log.error("{County} GA: fetch failed: %s", e)
            return []

        filings = self._parse(raw, cutoff)
        log.info("{County} GA: %d dispossessory filings found", len(filings))
        return filings

    def _fetch_docket(self, from_date: date, to_date: date) -> str:
        # Fill in actual request parameters from Task 3 research
        r = self.session.get(
            _DOCKET_URL,
            params={"from": from_date.isoformat(), "to": to_date.isoformat()},
            timeout=30,
        )
        r.raise_for_status()
        return r.text

    def _parse(self, html: str, cutoff: date) -> list[Filing]:
        # Fill in actual parsing logic based on real HTML structure from Task 3
        filings: list[Filing] = []
        # ... parse rows ...
        return filings
```

Fill in `_fetch_docket` and `_parse` based on the actual portal structure discovered in Task 3.

- [ ] **Step 3: Run the failing test — confirm it fails for the right reason**

```bash
pytest tests/test_georgia_{county}_scraper.py -v 2>&1
```

Expected: ImportError or AttributeError (class exists but parsing not implemented), not an unrelated crash.

- [ ] **Step 4: Implement parsing until test passes**

Iterate on `_parse()` until:
```bash
pytest tests/test_georgia_{county}_scraper.py -v 2>&1
```
Expected: PASS

- [ ] **Step 5: Wire into run_georgia.py**

In `jobs/run_georgia.py`, import the new scraper and add it to the scrapers list:

```python
from scrapers.georgia.{county}_magistrate import {County}MagistrateScraper

async def main(*, lookback_days: int = 2) -> None:
    log.info("Starting Georgia scrape job")

    scrapers = [
        ("{County} Magistrate", {County}MagistrateScraper(lookback_days=lookback_days)),
        # Keep re:SearchGA removed or commented — it returns no addresses
    ]
    ...
```

- [ ] **Step 6: Run a live smoke test (scraper-only, no pipeline)**

```bash
python -c "
from scrapers.georgia.{county}_magistrate import {County}MagistrateScraper
s = {County}MagistrateScraper(lookback_days=14)
filings = s.scrape()
print(f'{len(filings)} filings')
for f in filings[:3]:
    print(f.case_number, f.landlord_name, f.property_address)
"
```

Expected: at least 1 filing with non-Unknown address.

- [ ] **Step 7: Add Georgia to daily scheduler**

In `services/daily_scheduler.py`:

```python
ScheduledJob("georgia", 14, 0, "run_georgia.py"),
```

In `jobs/run_daily.py`, add:
```python
from jobs import run_tennessee, run_texas, run_arizona, run_georgia
```
And:
```python
await _run_state_job("Georgia", "georgia", run_georgia.main)
```

- [ ] **Step 8: Update source_discovery_matrix.md — change GA status to green**

- [ ] **Step 9: Commit**

```bash
git add scrapers/georgia/{county}_magistrate.py tests/test_georgia_{county}_scraper.py jobs/run_georgia.py services/daily_scheduler.py jobs/run_daily.py docs/source_discovery_matrix.md
git commit -m "feat: add {county} county georgia magistrate dispossessory scraper"
```

---

## Workstream C — New TX JP Extract Counties

### Task 5: Research Bexar, Tarrant, and Dallas JP court extracts

**Context:** Harris County JP uses a public CSV extract at `jpwebsite.harriscountytx.gov/PublicExtracts/`. Bexar (San Antonio), Tarrant (Fort Worth), and Dallas JP courts may have the same system or a similar public extract. Goal: find any date-enumerable no-login public extract with eviction cases and defendant addresses.

**Files:**
- Modify: `docs/source_discovery_matrix.md`
- Modify: `docs/portal_notes.md`

- [ ] **Step 1: Research Bexar County JP**

```bash
firecrawl scrape "https://www.bexar.org/2376/Justice-Courts" --only-main-content -o .firecrawl/bexar-jp.md
firecrawl search "Bexar County JP public extract eviction CSV download site:bexar.org" -o .firecrawl/bexar-search.md
```

Check for: a `/PublicExtracts/` or similar path, downloadable CSV, date-range filter, eviction/forcible-detainer case type, defendant address field.

- [ ] **Step 2: Research Tarrant County JP**

```bash
firecrawl scrape "https://www.tarrantcounty.com/en/criminal-district/justice-courts.html" --only-main-content -o .firecrawl/tarrant-jp.md
firecrawl search "Tarrant County Justice Court public extract eviction CSV" -o .firecrawl/tarrant-search.md
```

- [ ] **Step 3: Research Dallas County JP**

```bash
firecrawl scrape "https://www.dallascounty.org/departments/countyclerk/justice-courts.php" --only-main-content -o .firecrawl/dallas-jp.md
firecrawl search "Dallas County Justice Court public extract CSV eviction" -o .firecrawl/dallas-search.md
```

- [ ] **Step 4: Update source_discovery_matrix.md**

Add a row per county. For any green find, note the extract URL, confirmed fields, and "Build scraper next." For red, document why.

- [ ] **Step 5: Update portal_notes.md**

Add subsections under a new `## Texas — Additional JP Courts` section.

- [ ] **Step 6: Commit**

```bash
git add docs/source_discovery_matrix.md docs/portal_notes.md .firecrawl/
git commit -m "docs: bexar/tarrant/dallas jp court source discovery"
```

---

### Task 6: Build new TX JP scraper (CONDITIONAL — only if Task 5 finds a green source)

**Skip this task if Task 5 found no green county.**

Replace `{county}` with the actual county (e.g. `bexar`, `tarrant`).

**Files:**
- Create: `scrapers/texas/{county}.py`
- Create: `tests/test_texas_{county}_scraper.py`
- Modify: `jobs/run_texas.py`

- [ ] **Step 1: Read the existing Harris scraper as the model**

```bash
cat scrapers/texas/harris.py
```

Note: Harris uses `requests` to POST a form and download a CSV. If the new county uses the same extract system, copy the pattern and change the URL and field mappings.

- [ ] **Step 2: Write the failing test**

```python
# tests/test_texas_{county}_scraper.py
from __future__ import annotations

import pytest
from unittest.mock import patch, MagicMock
from scrapers.texas.{county} import {County}JPScraper

SAMPLE_CSV = """\
Case Number,Case File Date,Plaintiff Name,Defendant Name,Defendant Address,Nature of Claim
{county.upper()}2026001,04/01/2026,Landlord LLC,John Tenant,123 Oak St San Antonio TX 78201,Forcible Detainer
"""

def test_scraper_parses_eviction_csv():
    with patch("scrapers.texas.{county}.requests.Session") as mock_session_cls:
        mock_session = MagicMock()
        mock_session_cls.return_value = mock_session
        mock_session.post.return_value.status_code = 200
        mock_session.post.return_value.content = SAMPLE_CSV.encode()
        mock_session.post.return_value.raise_for_status = MagicMock()

        scraper = {County}JPScraper(lookback_days=7)
        filings = scraper.scrape()

    assert len(filings) == 1
    f = filings[0]
    assert f.state == "TX"
    assert f.county == "{County}"
    assert "123 Oak St" in f.property_address
    assert f.landlord_name == "Landlord LLC"
    assert f.tenant_name == "John Tenant"
```

Run: `pytest tests/test_texas_{county}_scraper.py -v`
Expected: FAIL (ImportError)

- [ ] **Step 3: Implement the scraper modeled on harris.py**

Create `scrapers/texas/{county}.py`. If the extract URL and form fields match Harris, copy `scrapers/texas/harris.py` and change:
- `STATE = "TX"` (keep)
- `COUNTY = "{County}"` 
- `_EXTRACT_URL` → actual URL from Task 5
- CSV column names in `_parse_csv()` → actual column names from the downloaded sample

- [ ] **Step 4: Run test until green**

```bash
pytest tests/test_texas_{county}_scraper.py -v 2>&1
```

- [ ] **Step 5: Live smoke test**

```bash
python -c "
from scrapers.texas.{county} import {County}JPScraper
s = {County}JPScraper(lookback_days=7)
filings = s.scrape()
print(f'{len(filings)} filings')
for f in filings[:3]:
    print(f.case_number, f.landlord_name, f.property_address)
"
```

Expected: at least 1 filing with a real TX street address.

- [ ] **Step 6: Wire into run_texas.py**

Read `jobs/run_texas.py` first, then add the new scraper following the same pattern as the existing Harris entry.

- [ ] **Step 7: Add to scheduler if Harris + new county produce leads cleanly**

In `services/daily_scheduler.py`, the existing `"texas"` job runs `run_texas.py` which already covers all TX counties. No new scheduler entry needed — just ensure `run_texas.py` includes the new county.

- [ ] **Step 8: Update source_discovery_matrix.md — change county status to green**

- [ ] **Step 9: Commit**

```bash
git add scrapers/texas/{county}.py tests/test_texas_{county}_scraper.py jobs/run_texas.py docs/source_discovery_matrix.md
git commit -m "feat: add {county} county texas jp extract scraper"
```

---

## Self-Review

**Spec coverage check:**
- ✅ Arizona `--pipe` test run → Task 1 (fix tests) + Task 2 (live run + scheduler)
- ✅ Georgia Magistrate research → Task 3
- ✅ Georgia Magistrate scraper → Task 4 (conditional)
- ✅ TX JP discovery → Task 5
- ✅ TX JP scraper → Task 6 (conditional)

**Placeholder scan:**
- Task 4 Step 2 has `{county}` placeholders intentionally — executor must substitute based on Task 3 findings. This is documented.
- Task 6 Step 3 has the same intentional pattern.
- `SAMPLE_HTML` in Task 4 Step 1 requires executor to paste actual HTML — flagged explicitly.

**Type consistency:**
- `ArizonaRunSummary` used consistently (renamed from `ArizonaProofSummary`).
- `build_summary(..., piped=False)` signature matches the updated `jobs/run_arizona.py`.
- `FakeArizonaScraper.address_matches_by_case` uses `_FakeMatch` dataclass consistent with `AddressMatchResult.status` string field.
