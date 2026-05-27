## Source

- **State / County:**
- **Discovery Pipeline Sheet row:** <!-- paste link or row reference -->
- **Classification:** <!-- Green / Yellow -->

## Build gate checklist

- [ ] `scripts/smoke_scrapers.py` returns ≥ 50 filings over a sensible lookback
- [ ] Output dict matches scraper contract (`case_number`, `filing_date`, `plaintiff`, `defendant`, `defendant_address`, `source_url`, `county`, `state`)
- [ ] Pagination confirmed (multi-page lookback)
- [ ] No crash on empty results / network timeout / malformed page
- [ ] **Address hit rate measured: ___%** (must be ≥ 60% for Green; below = Yellow, no pipeline)
- [ ] `python -m <runner> --yes-write-supabase --lookback-days 2` insert/dedupe confirmed

## Matrix update

<!--
Paste the proposed row for docs/source_discovery_matrix.md below.
Lead applies the matrix edit on merge — do NOT edit the matrix file directly in this PR.
-->

```
| ST | County / source | Status | Why | Next action |
|---|---|---:|---|---|
| .. | .. | .. | .. | .. |
```

## Files touched

- [ ] Only my state's `scrapers/<state>/<county>.py`, `jobs/run_<county>.py`, `tests/test_<county>.py`
- [ ] No edits to `services/`, `scripts/`, other builders' state directories, or already-built green scrapers
- [ ] Matrix update is in this PR description, not as a file edit

## Pipeline wiring

- [ ] I understand `--pipe` (SearchBug + GHL + DNC) is a separate approval after this PR merges
