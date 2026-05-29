# Current Schedule Hardening — Spec 2

**Date:** 2026-05-29
**Author:** Zee
**Status:** Draft (brainstormed 2026-05-29)
**Spec 1:** [2026-05-29-pipeline-gold-standard-design.md](2026-05-29-pipeline-gold-standard-design.md)

## Problem

Spec 1 defined the gold-standard contract and shipped `scripts/verify_pipeline_health.py`. A live smoke run against production on 2026-05-29 surfaced three FAILs and two FLAGs:

| Status | Finding | Layer |
|--------|---------|-------|
| FAIL | `GHL_NG_REVIEW_STAGE_ID` not set — name_mismatch/ambiguous leads silently drop | env + ghl |
| FAIL | Maricopa AZ scraper at 0% `gate_address` pass rate — assessor returns valid addresses but missing commas and state abbreviation | scrapers |
| FLAG | Tarrant TX scheduler entry has `--pipe` enabled but no recent filings persisted | scrapers |
| FLAG | Cobb GA scheduler entry has `--pipe` enabled but no recent filings persisted | scrapers |

Davidson (93%), Harris (99%), Franklin (100%), and Hamilton (95%) are above the 85% bar and stay green.

This spec brings the four flagged jobs to the standard. Verification is the same `verify_pipeline_health.py` run that surfaced them.

## Scope

In scope:
- Create the GHL Review stage via API; update Railway env var; redeploy
- Fix Maricopa's `_property_address` to emit comma-and-state-separated strings
- Diagnose Tarrant + Cobb silence; apply small fixes if available
- For Tarrant/Cobb diagnoses that surface a rebuild-class problem: file a follow-up spec and deschedule until rebuilt

Out of scope:
- Backfilling the 83 stale Maricopa rows in Supabase — they'll be replaced naturally as fresh cron runs accumulate
- Rebuilding Tarrant or Cobb if diagnosis says it's needed — those become Spec 2b / 2c
- Hamilton's 5% non-passing tail (already above gold bar)
- New unscheduled green sources — confirmed there are none waiting

## The four work items

### Item 1 — GHL Review stage creation

A new stage in the NG GHL subaccount's existing primary pipeline (the one
containing `GHL_NG_NEW_FILING_STAGE_ID`), positioned at index 0 (before "New
Filing"). Name: "Review — SearchBug Mismatch".

Implementation:

1. New `services/ghl_service.create_pipeline_stage(location_id, pipeline_id, name, position) -> str` returning the new stage ID. Idempotent — if a stage with the same name exists, returns its ID instead of creating a duplicate.
2. New `scripts/ghl_create_review_stage.py` that:
   - Calls `ghl_service` to list the location's pipelines, finds the one containing `GHL_NG_NEW_FILING_STAGE_ID`
   - Calls `create_pipeline_stage()` with `position=0`
   - Prints the resulting stage ID
   - Runs `railway variable set "GHL_NG_REVIEW_STAGE_ID=<id>" --skip-deploys` via subprocess
   - Appends/updates the line in local `.env` for parity (only if the line is missing or empty — never overwrites)
   - Triggers `railway redeploy --service leadgen --yes` so the runner picks up the new env var

Runtime budget: under 30 seconds end-to-end. Auth via existing `GHL_API_KEY`. Endpoint to call:
`POST https://services.leadconnectorhq.com/opportunities/pipelines/{pipeline_id}/stages` (v2 API).
Fallback to the v1 path if v2 doesn't accept stage creation — discovered during implementation.

Acceptance:
- Stage exists in NG pipeline, visible in GHL UI
- `GHL_NG_REVIEW_STAGE_ID` set on Railway
- `verify_pipeline_health.py` flips this from FAIL to OK

### Item 2 — Maricopa address format fix

Current state in `scrapers/arizona/maricopa.py:162`:

```python
@staticmethod
def _property_address(detail: MaricopaCaseDetail) -> str:
    match = detail.address_match
    if match and match.status == "single_match" and match.records:
        return match.records[0].physical_address or "Unknown"
    return "Unknown"
```

`physical_address` returns assessor-formatted strings like `"310 S 3RD AVE AVONDALE 85323"` — space-delimited, no commas, no state abbreviation. `gate_address` rejects them because its `_ADDR_STATE_ZIP_RE` requires `\b[A-Z]{2}\s+\d{5}\b` (state abbreviation immediately before ZIP).

Fix uses the assessor's **structured fields** (`physical_city`, `physical_zip` already exist on `ParcelRecord` per `scrapers/arizona/maricopa_assessor.py:17-25`) rather than parsing the joined string. The joined `physical_address` ends with `" {physical_city} {physical_zip}"`, so we strip that suffix to isolate the street and rebuild with proper format.

```python
@staticmethod
def _property_address(detail: MaricopaCaseDetail) -> str:
    match = detail.address_match
    if not (match and match.status == "single_match" and match.records):
        return "Unknown"
    rec = match.records[0]
    if not (rec.physical_address and rec.physical_city and rec.physical_zip):
        return rec.physical_address or "Unknown"
    suffix = f" {rec.physical_city} {rec.physical_zip}"
    raw = rec.physical_address
    street = raw[: -len(suffix)] if raw.endswith(suffix) else raw
    return f"{street.strip()}, {rec.physical_city.title()}, AZ {rec.physical_zip}"
```

Why this is better than regex parsing: no ambiguity on multi-word cities, no city-token guessing, and the structured fields are authoritative (assessor API source of truth). The conditional fallback handles the rare case where the joined string doesn't end with the structured suffix (data drift on the assessor side).

Unit test in `tests/test_maricopa_scraper.py` (extend if exists, create if not) covering:
- Single-word city (AVONDALE, PHOENIX) → properly formatted
- Multi-word city (QUEEN CREEK, PARADISE VALLEY) → properly formatted via structured fields
- Empty assessor record → "Unknown"
- Missing assessor match → "Unknown"
- Result passes `pipeline.gates.gate_address`

Acceptance:
- Unit tests pass
- Live Maricopa cron run produces filings whose `property_address` passes `gate_address`
- Within 1-3 cron runs, `verify_pipeline_health.py` flips Maricopa from FAIL to OK

### Item 3 — Tarrant + Cobb investigation

New script `scripts/diagnose_scraper_silence.py` for diagnosing a scraper that the verifier reports as "no filings persisted yet." Usage:

```
python scripts/diagnose_scraper_silence.py --scraper tarrant
python scripts/diagnose_scraper_silence.py --scraper cobb
```

For each scraper it:

1. Looks up the most recent successful `run_metrics` entry for that (state, county) and reports the date + filing count
2. Runs the scraper standalone with `lookback_days=7`, capturing stdout/stderr
3. Classifies the result:
   - `no_volume` — clean run, 0 filings (legitimate quiet week — common for some JP courts)
   - `connectivity` — exception during fetch (Bright Data zone issue, portal down, DNS)
   - `parsing` — fetch succeeded but extraction returned 0 (selector drift, layout change)
   - `format_mismatch` — produced filings but `gate_address` rejects 100% (would be a Maricopa-class fix)
4. Prints a short report with class + recommended next action
5. For each scraper, if class is `connectivity` or `parsing`, dumps an HTML snapshot to `data/diagnostics/<scraper>_<date>.html` for the operator to inspect

Action policy by class:
- `no_volume` → leave the scraper scheduled; verifier FLAG is informational only
- `format_mismatch` → fix in this spec (Maricopa-class)
- `connectivity` → if Bright Data zone or env config issue, fix in this spec; if portal is genuinely down, file Spec 2b
- `parsing` → if a small selector update, fix in this spec; if portal layout changed substantially, file Spec 2b and remove from `SCHEDULED_JOBS`

Acceptance:
- Diagnostic produces a clear class + next-action for both Tarrant and Cobb
- All `parsing`/`connectivity` small-fix cases shipped
- All rebuild-class cases filed as Spec 2b/2c and removed from `SCHEDULED_JOBS`
- `verify_pipeline_health.py` either flips to OK or no longer reports the descheduled scrapers

### Item 4 — Verification

Run `python scripts/verify_pipeline_health.py`. Expected delta from baseline:

| Layer | Before | After |
|-------|--------|-------|
| env | `GHL_NG_REVIEW_STAGE_ID` FAIL | OK |
| schema | unchanged | unchanged |
| scrapers — Harris | OK 99% | OK 99% |
| scrapers — Tarrant | FLAG no data | OK (cheap fix) or absent (descheduled) |
| scrapers — Davidson | OK 93% | OK 93% |
| scrapers — Maricopa | FAIL 0% | OK (within 1-3 cron runs) |
| scrapers — Cobb | FLAG no data | OK (cheap fix) or absent (descheduled) |
| scrapers — Franklin | OK 100% | OK 100% |
| scrapers — Hamilton | OK 95% | OK 95% |
| searchbug | OK | OK |
| ghl | `GHL_NG_REVIEW_STAGE_ID` FAIL | OK |

Exit code 0 in the happy path. Exit code 1 acceptable only if Tarrant/Cobb diagnoses surface an unresolved rebuild-class issue that we explicitly file as Spec 2b/2c.

## Deliverables summary

Code:
- `services/ghl_service.py` — new `create_pipeline_stage()` function
- `scrapers/arizona/maricopa.py` — `_property_address()` normalizer
- `tests/test_ghl_service.py` — new test for `create_pipeline_stage()`
- `tests/test_maricopa_scraper.py` — new tests for the address normalizer (extend if file exists)
- `scripts/ghl_create_review_stage.py` — operational one-shot
- `scripts/diagnose_scraper_silence.py` — diagnostic tool
- Possible scheduler edit removing Tarrant/Cobb if rebuild needed

Operational:
- GHL Review stage created in NG subaccount via API
- `GHL_NG_REVIEW_STAGE_ID` set on Railway, local `.env` updated for parity
- Railway redeployed so runner picks up the new env var

Out of scope, deferred:
- Maricopa row backfill (let natural turnover replace)
- Tarrant/Cobb rebuild specs (only filed if diagnosis demands)

## Success criteria

Spec 2 is done when:

1. `verify_pipeline_health.py` exits 0, or exits 1 only because of explicitly-deferred rebuild-class Tarrant/Cobb findings tracked in their own follow-up specs
2. The next scheduled cron tick that touches a name_mismatch or ambiguous SearchBug result successfully pushes the lead to the Review stage in GHL
3. Within 3 cron runs after the Maricopa scraper code lands, Maricopa's verifier line moves from FAIL to OK as fresh filings accumulate
