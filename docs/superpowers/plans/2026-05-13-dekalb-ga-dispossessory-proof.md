# DeKalb GA Dispossessory Proof Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a scraper-only DeKalb County GA Magistrate dispossessory proof that can measure tenant-lead volume before production scheduling.

**Architecture:** The DeKalb scraper will mirror the Cobb scraper shape: fetch the official civil calendars page, filter public dispossessory PDF links, parse PDF calendars with `pdfplumber`, and return normalized `Filing` records. The job entrypoint stays scraper-only by default and exposes `--pipe` only for a later explicit production decision, preserving room for Melissa Personator tenant enrichment.

**Tech Stack:** Python, requests, BeautifulSoup, pdfplumber, pytest, existing `models.Filing` and `pipeline.runner`.

---

### Task 1: DeKalb Parser Unit Tests

**Files:**
- Create: `tests/test_dekalb_scraper.py`
- Create: `scrapers/georgia/dekalb.py`

- [ ] **Step 1: Write parser tests first**

Add tests for:
- `_dispo_links_from_html` returns only PDF links with dispossessory/dispo text or href.
- `_parse_date_from_label` handles `05.12.26`, `05-11-2026`, and `5.13.2026`.
- `_parse_pdf_bytes` extracts case number, landlord, tenant, court date, and strips occupant labels from fixture PDF text.

- [ ] **Step 2: Verify tests fail**

Run: `pytest -q tests/test_dekalb_scraper.py`

Expected: import/module errors because `scrapers.georgia.dekalb` does not exist.

- [ ] **Step 3: Implement scraper parser**

Add `scrapers/georgia/dekalb.py` with:
- constants for state/county/timezone/source URL
- `DeKalbDispossessoryScraper`
- `_dispo_links_from_html`
- `_parse_date_from_label`
- `_parse_pdf_bytes`
- tenant and party cleanup helpers

- [ ] **Step 4: Verify parser tests pass**

Run: `pytest -q tests/test_dekalb_scraper.py`

Expected: all tests pass.

### Task 2: DeKalb Job Entrypoint

**Files:**
- Create: `jobs/run_georgia_dekalb.py`
- Create: `tests/test_run_georgia_dekalb.py`

- [ ] **Step 1: Write job tests first**

Add tests that:
- scraper-only default prints a summary and does not import/call pipeline
- `--pipe` mode sends scraped filings to `pipeline.runner.run(..., state="GA", county="DeKalb")`
- default lookback is 2 days

- [ ] **Step 2: Verify job tests fail**

Run: `pytest -q tests/test_run_georgia_dekalb.py`

Expected: import/module errors because `jobs.run_georgia_dekalb` does not exist.

- [ ] **Step 3: Implement job**

Add `jobs/run_georgia_dekalb.py` with:
- `DeKalbRunSummary`
- `build_summary`
- `main(max_cases=200, lookback_days=2, notify=False, pipe=False)`
- CLI flags `--max-cases`, `--lookback-days`, `--notify`, `--pipe`

- [ ] **Step 4: Verify job tests pass**

Run: `pytest -q tests/test_run_georgia_dekalb.py`

Expected: all tests pass.

### Task 3: Documentation And Source Matrix

**Files:**
- Modify: `docs/portal_notes.md`
- Modify: `docs/source_discovery_matrix.md`

- [ ] **Step 1: Update source notes**

Update DeKalb from red to yellow/proof in both docs:
- official civil calendars page exists
- dispossessory PDFs are public
- address is not confirmed in PDFs
- proof job is scraper-only until volume/quality is measured
- Melissa Personator is expected to become the tenant-enrichment path later

- [ ] **Step 2: Verify docs mention DeKalb proof**

Run: `rg -n "DeKalb|Melissa|run_georgia_dekalb" docs/portal_notes.md docs/source_discovery_matrix.md`

Expected: both docs include the proof status.

### Task 4: Verification

**Files:**
- No source edits expected.

- [ ] **Step 1: Run focused tests**

Run: `pytest -q tests/test_dekalb_scraper.py tests/test_run_georgia_dekalb.py`

Expected: all tests pass.

- [ ] **Step 2: Run scraper-only live proof**

Run: `python jobs/run_georgia_dekalb.py --max-cases 10`

Expected: prints a DeKalb scraper-only proof summary and does not call the pipeline.

- [ ] **Step 3: Run full suite**

Run: `pytest -q`

Expected: all tests pass, with only existing intentional skips.
