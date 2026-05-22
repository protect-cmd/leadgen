# Vantage Dashboard — Actionable Filter + Already Called Tab

**Status:** Draft → Review
**Date:** 2026-05-22
**Track:** Tenant-side (NG) only. EC parity deferred.

## Context

The Vantage Residential tab in the lead-queue dashboard currently returns **541
rows** — every NG-track contact attached to a `residential_approved` filing,
regardless of phone, DNC, or Bland-dial state. In practice only a handful of
those are actionable: most have no phone (BatchData/SearchBug returned empty),
some have already been dialed by Bland during the manual launch, and a few
were caught by post-push QA.

After applying the proposed filter, the view returns **14 rows** today — leads
the operator can actually approve or skip. The other 527 either lack a phone
(no operator action possible) or have already been worked (no operator action
needed).

The complaint that triggered this: the operator opened the dashboard expecting
"a lot of new numbers found" but saw mostly familiar already-pushed leads with
no way to tell new-vs-already-worked at a glance.

A related artifact, `launch_results.csv` (78 rows from an earlier manual
launch), is already reflected in Supabase (`lead_contacts.bland_status` =
`triggered` for the dialed subset). No CSV→DB sync is needed; only the
dashboard filter needs to honor the existing state.

## Goals

1. The default Vantage Residential view shows only leads the operator can act on today.
2. Already-called leads (Bland `triggered` or `wrong_brand_review`) are still discoverable in a separate tab for context.
3. Dashboard count tiles match the table content (no inflated numbers).
4. DNC-unknown leads with phones remain visible (the operator decides per-row).
5. No CSV import or DB migration required.

## Non-goals

- EC (landlord) parity — different data path (`filings.phone` legacy column), out of scope here.
- Reworking the count-tile area at the top of the page (the "500 PENDING / 56 READY TO CALL" tiles). Those are driven by `run_metrics` and will start populating correctly once the schema migration from PR #7 runs.
- Pipeline-health checker script — deferred to a follow-up PR.
- Visual styling differentiation for archive-style tabs (Already Called, Discarded). Defer.

## Design

### Filter logic

A lead appears in **Vantage Residential** (`ng_residential` view) when:

```
filings.lead_bucket = 'residential_approved'
AND (filings.language_hint IS NULL OR filings.language_hint != 'spanish_likely')
AND lead_contacts.track = 'ng'
AND lead_contacts.phone IS NOT NULL
AND lead_contacts.bland_status NOT IN (
  'triggered',           -- Bland successfully dialed
  'wrong_brand_review',  -- post-push QA flagged
  'missing_contact_data',
  'blocked_dnc'
)
```

The remaining `bland_status` values stay visible:
- `pending` — queued, awaiting manual approve
- `pending_dnc_review` — DNC unknown, caller decides
- `skipped` — manually skipped (can revisit)
- `NULL` — fresh leads pre-Bland-gate

DNC clear and DNC unknown both appear (per operator preference: "phone present is fine, just flag DNC status on the row").

A lead appears in **Vantage Already Called** (`ng_already_called` view, new) when:

```
filings.lead_bucket = 'residential_approved'
AND lead_contacts.track = 'ng'
AND lead_contacts.bland_status IN ('triggered', 'wrong_brand_review')
```

### Server changes

All in [services/dedup_service.py](services/dedup_service.py):

1. **`_filter_dashboard_query`** — add a branch for `ng_already_called` mirroring the existing `ng_residential` shape (filings-side filter for `lead_bucket`). The bland_status filter is applied separately because it lives on `lead_contacts`.

2. **`_get_ng_dashboard_leads`** — currently pulls *all* NG contacts via `_client.table('lead_contacts').eq('track', 'ng')`. Replace with a view-specific filter:
   - For `ng_residential`: also require `phone IS NOT NULL` and exclude the four worked-set `bland_status` values.
   - For `ng_already_called`: require `bland_status IN ('triggered', 'wrong_brand_review')`.
   - For all other NG views (commercial, held, etc.): unchanged behavior.

3. **`get_dashboard_counts`** — currently has 10 keys. Add `ng_already_called`. Update the existing `ng_residential` count so it matches the new (filtered) table row count. Update `_ng_counts_from_contact_rows` to apply the same filter.

### Frontend changes

All in [dashboard/index.html](dashboard/index.html), three edits:

1. **`brandViews.ng`** (line ~1018) — insert `'ng_already_called'` before `'ng_discarded'`:
   ```js
   ng: ['ng_residential', 'ng_commercial', 'ng_spanish_residential',
        'ng_spanish_commercial', 'ng_held', 'ng_already_called', 'ng_discarded'],
   ```

2. **`viewLabels`** (line ~1021) — add `ng_already_called: 'Vantage Already Called'`.

3. **`viewLabelsShort`** (two copies at lines ~1046 and ~1112) — add `ng_already_called: 'Already Called'`.

The dynamic chip rebuild (`_rebuildViewChips`) already iterates `brandViews[brand]` so no JS function changes are required.

No HTML structure, CSS, or new API routes needed.

### Data flow

```
Browser clicks "Already Called" chip
  → setView('ng_already_called')
    → loadLeads() → fetch /api/leads?view=ng_already_called
      → dashboard.main /api/leads
        → get_dashboard_leads(view='ng_already_called')
          → _get_ng_dashboard_leads('ng_already_called', limit)
            → lead_contacts query filtered to bland_status IN worked-set
            → filings query filtered by _filter_dashboard_query (lead_bucket)
            → intersection joined and decorated
```

The data flow for `ng_residential` is unchanged except the lead_contacts pre-filter adds the phone + bland_status NOT IN worked-set criteria.

### Error handling

- If the lead_contacts filter returns an empty set, the function returns `[]` early without querying filings. (Existing behavior — no change.)
- If Supabase returns a partial error (HTTP 500 mid-query), the existing `_execute_with_retry` wrapper kicks in. (Existing behavior — no change.)
- No new failure modes introduced.

### Testing

Tests in `tests/test_dedup_service.py` (new file or extend existing):

1. `test_ng_residential_filters_actionable_only` — mixed fixture (phone+pending, phone+triggered, no phone, phone+blocked_dnc), the view returns only the actionable subset.
2. `test_ng_residential_includes_pending_dnc_review` — phone + dnc_status='unknown' + bland_status='pending_dnc_review' is in the result.
3. `test_ng_already_called_returns_worked_leads` — only `bland_status IN ('triggered', 'wrong_brand_review')` rows appear.
4. `test_ng_already_called_excludes_other_buckets` — `held` and `discarded` leads with triggered status do not leak in (lead_bucket filter still applies).
5. `test_get_dashboard_counts_ng_already_called_present_and_matches_table` — counts dict has the new key, count matches `len(leads)` after filter.
6. `test_dashboard_index_html_lists_ng_already_called` — static parse of `dashboard/index.html`, verifies `'ng_already_called'` appears in `brandViews.ng`, `viewLabels`, and both `viewLabelsShort` maps.

No JS interaction tests (no existing JS test infra; the changes are config-only).

## Live numbers (sized 2026-05-22)

| View | Before | After |
|---|---|---|
| Vantage Residential | 541 | 14 |
| Vantage Already Called *(new)* | — | 66 |
| Vantage Commercial | (unchanged) | (unchanged) |
| Vantage Spanish Res | (unchanged) | (unchanged) |
| Vantage Held | (unchanged) | (unchanged) |
| Vantage Discarded | (unchanged) | (unchanged) |

The 14 actionable decompose as:
- 11 phone-present, DNC unknown (`pending_dnc_review`)
- 2 phone-present, DNC clear, awaiting manual approve (`pending`)
- 1 manually skipped (still visible for revisit)

## Out of scope / follow-ups

1. EC (landlord) Residential view gets the same treatment in a separate PR.
2. Pipeline-health checker (`scripts/check_pipeline_health.py`) is a separate PR.
3. Visual differentiation for archive-style chips (muted color for Already Called / Discarded).
4. SQL migration for `run_metrics` (`ftc_scrubs_upgraded`, `ng_phones_pushed`, `searchbug_calls`, `searchbug_daily_total`) is still pending operator action — independent of this PR.

## Risks

- **Existing tests fail unexpectedly.** Test fixtures may assume the broad NG view returns everything. Audit and update.
- **Operator confusion** — if someone was using "Vantage Residential" count as the total NG pipeline volume, that number now drops drastically. Mitigated by the Already Called tab making the history discoverable, but worth flagging in the PR description.
- **Bland-status drift.** If new Bland statuses are added in the future (e.g. `failed`, `voicemail_left`), they default to *visible* in the main tab. Acceptable — being seen by the operator is the safer default than being hidden.
