## Source

- **State / County:**
- **Discovery Pipeline Sheet row:** <!-- paste link or row reference -->
- **Classification:** <!-- Green / Yellow -->

## Build gate checklist

- [ ] **Smoke test** — `python scripts/smoke_scrapers.py --states <state> --counties <county>` returns ≥ 50 filings over a sensible lookback
- [ ] **Schema** — output dict matches scraper contract (`case_number`, `filing_date`, `plaintiff`, `defendant`, `defendant_address`, `source_url`, `county`, `state`) — verified via pytest in `tests/test_<county>_scraper.py`
- [ ] **Pagination** — multi-page lookback returns expected count (pytest)
- [ ] **Error handling** — no crash on empty results / network timeout / malformed page (pytest with mocks)
- [ ] **Address hit rate measured: ___%** — count of filings with `defendant_address` populated divided by total. Hard floor ≥ 60% for Green; below = Yellow, no pipeline.
- [ ] **Supabase insert/dedupe** — `python jobs/run_<state>.py --yes-write-supabase --lookback-days 2` confirms inserts and deduplicates on re-run

## Files I touched

Standard pattern for a new county:

- [ ] `scrapers/<state>/<county>.py` — new file
- [ ] `tests/test_<county>_scraper.py` — new file (per-county convention, see `test_clark_scraper.py`, `test_franklin_scraper.py`, `test_hamilton_scraper.py`)
- [ ] `jobs/run_<state>.py` — appended my county to the existing state runner's `scrapers` list (do NOT create a standalone `run_<county>.py` unless I confirmed with lead that the source needs special stack handling like Bright Data)

Standalone county runner only if:

- [ ] Lead pre-approved a standalone `jobs/run_<county>.py` for a special reason (e.g., Bright Data Scraping Browser, unique cadence)

Off-limits — confirm I did NOT touch:

- [ ] `services/`, `scripts/`, `pipeline/`, `.github/`
- [ ] Other builders' state directories
- [ ] Already-built green scrapers (Harris, Tarrant, Hamilton OH, Franklin, Davidson)
- [ ] `docs/source_discovery_matrix.md` (lead applies the matrix update on merge — see below)

## For lead to apply on merge

### Matrix update

<!-- Paste the proposed row for docs/source_discovery_matrix.md below. -->

```
| ST | County / source | Status | Why | Next action |
|---|---|---:|---|---|
| .. | .. | .. | .. | .. |
```

### Smoke runner registration

<!--
If your county needs to be added to scripts/smoke_scrapers.py
(state factory function + alias map), paste the proposed lines below.
Lead applies these on merge.
-->

```python
# In _<state>_scrapers factory:
("<County Name>", <CountyScraper>(lookback_days=lookback_days)),

# In alias map:
"<county-alias>": "<state>",
```

## Pipeline wiring

- [ ] I understand `--pipe` (SearchBug + GHL + DNC) is a separate approval after this PR merges. This PR only proves the scraper produces clean filings; it does NOT turn on enrichment or outreach.
