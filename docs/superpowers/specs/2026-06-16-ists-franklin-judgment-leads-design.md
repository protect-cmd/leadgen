# ISTS Sub-Project A2 — Judgment-Lead Sourcing (Franklin OH)

**Design spec — "I Stopped The Sheriff" (ISTS), second judgment source**
*Date: 2026-06-16 · Status: approved design, pending implementation plan*

---

## 1. Context

ISTS targets **tenants who have lost their eviction case** and sells
document-preparation services. Sub-project A delivered the first judgment source,
**Harris County, TX**, landing tenant-lost leads in the isolated `ists_judgments`
table (see `docs/superpowers/specs/2026-06-08-ists-judgment-leads-subproject-a-design.md`
and `scrapers/texas/harris_judgments.py`). That job is now scheduled daily
(`ScheduledJob("ists_harris", 14, 50, ...)`).

This spec adds **Franklin County, OH (Columbus / Franklin County Municipal Court)**
as the **second** judgment source. It is the closest analog to Harris among our
currently scheduled scrapers: a public, no-auth bulk CSV that carries an explicit
disposition outcome **and** a full defendant address.

## 2. Goal

Produce a reliable, **SearchBug-ready** pool of Franklin ISTS judgment leads —
tenants with a confirmed tenant-lost disposition and a full street address —
landed in the existing `ists_judgments` table with `county='Franklin'`,
`state='OH'`, `window='W1'`.

## 3. Scope boundary

**In scope:** download the FCMC eviction CSV, row-filter to tenant-lost
dispositions, require a full address, cross-reference our DB for prior-work flags,
upsert to `ists_judgments`.

**Out of scope (deferred):** SearchBug enrichment / hit-rate test (sub-project B);
GHL / Bland / SMS; Window-2 (writ/set-out) sourcing — see §11; counties beyond
Franklin. This sub-project performs **no enrichment, no outreach, and spends no
money.**

## 4. Key decisions (locked)

| # | Decision | Rationale |
|---|---|---|
| F1 | **Reuse the existing `ists_judgments` table** — no new migration | The Harris table already has `state`/`county` columns and PK `case_number`. Franklin case numbers (`2026 CVG NNNNNN`) and Harris (`NNNNNN-NNNNNN`) occupy disjoint namespaces, so no PK collision. |
| F2 | **Data source = the FCMC "Civil F.E.D. (Eviction) Case List" monthly CSV** (the same file `scrapers/ohio/franklin.py` already downloads for filings) | Public, no-auth, no Playwright. Carries `LAST_DISPOSITION_DESCRIPTION` + `LAST_DISPOSITION_DATE` + full `FIRST_DEFENDANT_ADDRESS_*`. **100% full-address rate** (verified: 2056/2056 rows in May 2026). |
| F3 | **"Tenant lost" = a fixed set of `LAST_DISPOSITION_DESCRIPTION` values** | Verified 2026-06-16 against FCMC case-detail pages. Keep `{JUDGMENT HEARD BY MAGISTRATE, JUDGMENT FOR PLAINTIFF, AGREED JUDGMENT BOTH CAUSE OF ACTION}`; these map to "JUDGEMENT FOR RESTITUTION OF PREMISES" + writ of restitution. Drop all dismissal/undisposed/transfer/bankruptcy rows. |
| F4 | **Exclude `OTHER TERMINATION - ADMIN JUDGE` in v1** | Spot-check showed this bucket is **mixed** (some restitution, some not). Conservative: never risk calling a tenant who settled/won. Revisit with a larger sample later. |
| F5 | **Window on `LAST_DISPOSITION_DATE`** (the judgment-date equivalent), FLOOR/CEILING days | Mirrors Harris's `judgment_date` lookback. Start `FLOOR=3 / CEILING=30` (disposition→CSV lag is longer than Harris; tune from the metrics). |
| F6 | **Enrichment vendor = SearchBug** (applies to B) | Same as Harris/D3: ISTS targets tenants = people-search = SearchBug. |
| F7 | **Plain `requests` download, no browser** | Unlike Harris (Playwright form portal), the FCMC CSV is a direct GET. Lighter and more reliable. |

## 5. Prod-safety contract

Same contract as sub-project A:

- **No new migration** (F1) — reuses isolated `ists_judgments`; no `ALTER`/`DROP`
  or FK into `filings`/`lead_contacts`.
- **Zero edits to prod code paths** — `scrapers/ohio/franklin.py` (the filings
  scraper) is **not modified**. The new module may import its CSV-discovery and
  column constants read-only.
- **Isolated writes** — writes only `ists_judgments`. Isolation test asserts no
  writes to `filings`/`lead_contacts`.
- **No SearchBug / Bland / GHL** → no spend, no messages.
- **Not wired into cron until smoke passes** — manual `--dry-run` first, then
  schedule (mirroring how Harris was rolled out).

## 6. Data model

No schema change. New rows in `ists_judgments`:

| Column | Franklin source |
|---|---|
| `case_number` | `CASE_NUMBER` (e.g. `2026 CVG 025286`) — PK |
| `defendant_name` | `FIRST_DEFENDANT_*` (company or first/middle/last/suffix) |
| `property_address` | `FIRST_DEFENDANT_ADDRESS_LINE_1/2`, `CITY`, `STATE` (fallback `OH`), `ZIP` |
| `plaintiff_name` | `FIRST_PLAINTIFF_*` |
| `state`, `county` | `OH` / `Franklin` |
| `filing_date` | `CASE_FILE_DATE` |
| `judgment_date` | `LAST_DISPOSITION_DATE` |
| `disposition_status` | raw `LAST_DISPOSITION_DESCRIPTION` (audit) |
| `confirmed` | `True` (CSV column is authoritative — no per-case lookup) |
| `confirmation_method` | `bulk_disposition` (new value; analogous to Harris's column-based confirm) |
| `window` | `'W1'` |
| `prior_phone`, `prior_bland_status` | from `services/ists_prior_work` cross-reference |
| `source_url` | the FCMC CSV report URL |
| `selected_at`, `confirmed_at` | timestamps |

Reuse `models/judgment.py` `JudgmentRecord` as-is (mirror Harris field mapping).

## 7. Components & flow

All net-new files (mirror the Harris module layout):

| File | Purpose |
|---|---|
| `scrapers/ohio/franklin_judgments.py` | Downloads the FCMC eviction CSV(s) for the disposition window (reuses `franklin.py` link-discovery + column constants), parses, row-filters to tenant-lost dispositions, requires full address. **Pure parser is browser-free and unit-tested.** |
| `jobs/run_ists_franklin.py` | Manual orchestrator + `--dry-run`; `sys.path` bootstrap so it runs under `daily_scheduler` as a plain subprocess. Mirrors `jobs/run_ists_harris.py`. |
| `tests/test_franklin_judgments.py` | Fixture-based (real CSV sample rows) + tenant-lost filter cases + isolation test. |

Reused as-is: `models/judgment.py`, `services/ists_store.py`,
`services/ists_prior_work.py`, `pipeline/gates.py` (`gate_name`, `gate_address`),
`services/name_utils.clean_tenant_name`.

**Flow:**
1. **Discover + download** the FCMC eviction CSV file(s) covering the disposition
   lookback. Because files are monthly and we window on `LAST_DISPOSITION_DATE`,
   fetch the **current and previous month** files so dispositions near a month
   boundary are not missed; concatenate rows.
2. **Filter to tenant-lost:** keep rows where `LAST_DISPOSITION_DESCRIPTION` is in
   the locked set (F3); drop everything else. Require a full address via
   `gate_address`; drop entity/`Unknown` names via `gate_name`.
3. **Window:** keep rows whose `LAST_DISPOSITION_DATE` ∈ `[today−CEILING, today−FLOOR]`.
4. **Cross-reference our DB** (read-only, `ists_prior_work.annotate_prior_work`):
   Franklin filings already flow into `filings`/`lead_contacts`, so match by
   `case_number` → set `prior_phone` / `prior_bland_status`.
5. **Store:** upsert to `ists_judgments` (idempotent on `case_number`),
   `window='W1'`, `confirmation_method='bulk_disposition'`.

## 8. Metrics to capture (per run)

- **Tenant-lost pool size** (count after the F3 filter + window).
- **Full-address rate** (expected ~100%).
- **Disposition-bucket breakdown** of the raw window (so the excluded
  `OTHER TERMINATION - ADMIN JUDGE` volume stays visible for the F4 revisit).
- **Disposition-date distribution** (tunes FLOOR/CEILING).
- **Prior-work breakdown** (phone-on-file / prior Bland).

## 9. Verified source facts (Task 0 — DONE 2026-06-16)

- CSV header: `CASE_NUMBER, CASE_FILE_DATE, LAST_DISPOSITION_DATE,
  LAST_DISPOSITION_DESCRIPTION, FIRST_PLAINTIFF_*, FIRST_DEFENDANT_*` (incl. full
  address block).
- May 2026 file: 2056 rows, **2056 with full defendant street+zip**.
- Disposition distribution: `UNDISPOSED 692`, `NOTICE OF DISMISSAL FILED 465`,
  **`JUDGMENT HEARD BY MAGISTRATE 459`**, `OTHER TERMINATION - ADMIN JUDGE 393`,
  `DISMISSAL HEARD BY MAGISTRATE 21`, `JUDGMENT FOR PLAINTIFF 2`,
  `AGREED JUDGMENT BOTH CAUSE OF ACTION 1`, plus small transfer/bankruptcy/
  voluntary-dismissal tails. ⇒ ~460+ tenant-lost/month (Harris-scale volume).
- Case-detail confirmation (`/case/view`): `JUDGMENT HEARD BY MAGISTRATE` →
  "JUDGEMENT FOR RESTITUTION OF PREMISES" + "WRIT OF RESTITUTION ISSUED" (+ SET
  OUT). `JUDGMENT FOR PLAINTIFF` → restitution entry. `NOTICE OF DISMISSAL FILED`
  → dismissal (tenant kept). `OTHER TERMINATION - ADMIN JUDGE` → mixed.

## 10. Error handling

Mirror existing scraper conventions. CSV fetch → retry ≤3, polite UA; failure →
set `last_error`, return `[]` (isolated, no prod impact). Per-row parse failure →
log + skip + count. Store write → retry then raise (isolated table). Upsert on
`case_number` keeps re-runs idempotent.

## 11. Window 2 opportunity (noted, not built here)

FCMC case-detail pages expose `WRIT OF RESTITUTION ISSUED` and `SET OUT` events
with dates — the **Window-2** signal that the Harris JP portal cannot source
(see `project_ists_window2`). This is per-case (not in the bulk CSV), so it is
**deferred to sub-project C**, but it makes Franklin a candidate to be the first
county that feeds **both** W1 and W2.

## 12. Testing & verification

- **Unit (fixtures):** tenant-lost filter (each disposition value → keep/drop),
  full-address vs partial, entity/`Unknown` name drop, disposition-date window,
  address assembly incl. blank-state fallback to `OH`.
- **Isolation test:** assert the module issues no writes to `filings`/`lead_contacts`.
- **Live smoke:** `python -m jobs.run_ists_franklin --dry-run` prints selected
  records + §8 metrics for eyeball validation before any DB write.

## 13. First tasks (for the implementation plan)

0. ~~Source verification~~ **DONE (2026-06-16)** — see §9.
1. `scrapers/ohio/franklin_judgments.py` (CSV download via reused discovery +
   disposition filter + window + full-address gate) + tests.
2. `jobs/run_ists_franklin.py` with `--dry-run` and `sys.path` bootstrap.
3. Live smoke + metrics; tune FLOOR/CEILING from the disposition-date distribution.
4. After smoke passes, add `ScheduledJob("ists_franklin", ...)` to
   `services/daily_scheduler.py` (slot near `ists_harris` at 14:50 / before the
   post-scrape chain).
5. Revisit F4 (`OTHER TERMINATION - ADMIN JUDGE`) with a larger case-detail sample
   to decide whether any of its ~393/month are recoverable tenant-lost leads.
