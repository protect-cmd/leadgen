# Davidson Tenant-Name Regression — Spec 2d (stub)

**Date:** 2026-05-29
**Status:** Stub — captures a regression surfaced by `verify_pipeline_health.py` during Spec 2 final verification. Full design + plan to be done when work is picked up.

## Problem

Davidson TN scraper (`scrapers/tennessee/davidson.py`, scheduled via `run_tennessee.py`) regressed from a 93% `gate_address + gate_name` pass rate (observed 2026-05-28) to **51%** (observed 2026-05-29 after that day's 13:20 UTC cron).

The verifier classified it FAIL (below the 60% drop-from-schedule threshold per Spec 1).

Diagnostic on the most-recent-100 Davidson rows:
- **0/100** fail `gate_address` (addresses parse fine)
- **49/100** fail `gate_name` because `tenant_name == "Unknown"`

The Davidson scraper outputs `"Unknown"` as a fallback when its PDF parsing can't extract a defendant name (`scrapers/tennessee/davidson.py:98`):

```python
tenant_name=clean_tenant_name(case["defendant"] or "") or (case["defendant"] or "Unknown"),
```

`case["defendant"]` is sourced from `current["first_defendant"]` after regex match on each PDF line (`scrapers/tennessee/davidson.py:165-175`). When the line doesn't match `_CASE_RE` or matches with an empty `first_def` group, the fallback "Unknown" kicks in.

## Likely root causes (to investigate)

1. **PDF format change on the Davidson clerk side** — the docket layout may have shifted; `_CASE_RE` no longer captures the defendant column consistently
2. **`pdfplumber` parse anomaly** — `text.splitlines()` ordering or column extraction may have changed between pdfplumber versions
3. **Legitimately-unnamed defendants** — today's docket may genuinely include a large batch of corporate-only filings or unserved cases (less likely at 49%)

## Action taken (none yet)

Davidson stays scheduled — the verifier flagged it, but the failure mode (Unknown name) is downstream-safe: the runner's `gate_name` will drop those rows cleanly without burning SearchBug calls or pushing bad leads to GHL. The cost of the regression is missed enrichment volume on the 49 affected leads, not bad data.

Did NOT deschedule because: 51% of Davidson is still producing usable leads, the underlying scrape works fine, and the fix should be a small PDF-parser tweak rather than a rebuild.

## Path forward (open)

When this work is picked up:

1. Pull a sample of recent Davidson PDFs to compare against the working version from 2026-05-28
2. Diff the layouts; identify whether `_CASE_RE` needs to be extended for a new defendant-column pattern
3. Add a regression test using the failing PDF as a fixture
4. Re-run verifier; expect Davidson to flip OK

If the format change is permanent and the regex can't be salvaged, options:
- Switch from regex-on-text to column-based extraction via pdfplumber's `extract_tables()`
- Treat unnamed cases as `gate_name` rejections deliberately (already happens; just acknowledge the lower yield)

## Related

- Source matrix: `docs/source_discovery_matrix.md` — Davidson is rated `green`
- Spec 2 (parent): `docs/superpowers/specs/2026-05-29-current-schedule-hardening-design.md`
- Davidson scraper: `scrapers/tennessee/davidson.py`
