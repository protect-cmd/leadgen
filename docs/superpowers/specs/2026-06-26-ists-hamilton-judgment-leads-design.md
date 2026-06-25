# ISTS Sub-Project A3 — Judgment-Lead Sourcing (Hamilton OH / Cincinnati)

**Design spec — "I Stopped The Sheriff" (ISTS), third judgment source**
*Date: 2026-06-26 · Status: approved design, building*

---

## 1. Context

ISTS targets **tenants who have lost their eviction case** and sells
document-preparation services. Sub-project A delivered Harris County, TX
(`scrapers/texas/harris_judgments.py`); A2 delivered Franklin County, OH
(`scrapers/ohio/franklin_judgments.py`). Both land tenant-lost leads in the
isolated `ists_judgments` table and are scheduled daily.

This spec adds **Hamilton County, OH (Cincinnati / Hamilton County Municipal
Court)** as the **third** judgment source. Hamilton is already a confirmed green
filings source (`scrapers/ohio/hamilton.py`, scheduled `ohio_hamilton`); this
sub-project reuses that scraper's enumeration + party-address plumbing read-only
and adds a disposition (judgment-outcome) layer on top.

## 2. Source facts (Task-0 — verified live 2026-06-25/26)

Portal: `https://www.courtclerk.org/data/` (no login, browser-like headers, no
Playwright — same as the filings scraper).

- **Enumeration:** the eviction schedule (`eviction_schedule.php?chosendate=…&court=MCV&location=EVIM`)
  lists cases by **hearing date** (the existing filings scraper already parses this).
- **Outcome:** the **default** `case_summary.php?casenumber=…&court[MCV]=on` page
  carries, in `#case_summary_table`, a row `Disposition: MM/DD/YYYY - <DESC>` —
  the judgment outcome **and** the judgment date, in the page we already fetch.
- **Address:** `POST case_summary.php sec=party` returns `#party_info_table` with
  `Name | Address | Party (P n / D n) | …`. The first `D` row is the tenant; the
  first `P` row is the landlord. (The existing filings scraper already pulls the
  D address.)
- **Disposition distribution** over a 233-case decided sample:
  `DISMISSED 111` (tenant kept), **`JUDGMENT FOR PLAINTIFF 59`** (tenant lost,
  ~25%), `UNDISPOSED 57`, `NEW ASSIGNMENT 5`, `REFERRED TO MAGISTRATE 1`.
  The tenant-lost signal is the **single** value `JUDGMENT FOR PLAINTIFF`
  (cleaner than Franklin — no mixed bucket like `OTHER TERMINATION - ADMIN JUDGE`).
- **Confirmed tenant-lost = restitution:** `#case_history_table` (via `sec=history`)
  for JUDGMENT FOR PLAINTIFF cases shows `ENTRY GRANTING PLAINTIFF RESTITUTION OF
  PREMISES` + `WRIT OF RESTITUTION ISSUED` → `PHYSICAL EVICTION EXECUTED`.
- **W1 pool size + address rate:** a full W1-window pull (judgments 3–30 days old,
  2026-05-27…06-23) returned **222 tenant-lost leads, 222/222 with a full
  street address + ZIP** (~220/month — Harris/Franklin scale).

## 3. Goal

Land a reliable, SearchBug-ready pool of Hamilton ISTS judgment leads — tenants
with a `JUDGMENT FOR PLAINTIFF` disposition and a full street address — in the
existing `ists_judgments` table with `county='Hamilton'`, `state='OH'`,
`window='W1'`.

## 4. Scope boundary

**In scope:** enumerate eviction cases over a hearing-date lookback, fetch each
case's disposition, keep `JUDGMENT FOR PLAINTIFF` whose judgment date is in the
W1 window, require a full address, cross-reference prior-work flags, upsert to
`ists_judgments`.

**Out of scope (deferred):** SearchBug enrichment; GHL/Bland/SMS; Window-2
(writ/set-out) sourcing — though Hamilton's `case_history_table` exposes the W2
signal (see §10); counties beyond Hamilton. **No enrichment, no outreach, no
spend.**

## 5. Key decisions (locked)

| # | Decision | Rationale |
|---|---|---|
| H1 | **Reuse `ists_judgments`** — no new migration | Same as A/A2. Hamilton case numbers (`26CV13398`) are a disjoint namespace from Harris/Franklin, so no PK collision. |
| H2 | **Tenant-lost = `{JUDGMENT FOR PLAINTIFF}`** | Verified against `case_history_table` (= restitution + writ). Single clean value; `DISMISSED`/`UNDISPOSED`/`NEW ASSIGNMENT`/`REFERRED TO MAGISTRATE` dropped. |
| H3 | **Window on the disposition (judgment) date**, `FLOOR=3 / CEILING=30` | Mirrors Harris/Franklin W1. The disposition date is in `case_summary_table`. |
| H4 | **Enumerate by hearing date over a wide lookback, window client-side** | The portal has no judgment-date index. Judgments lag hearings by up to ~6 weeks, so default `hearing_lookback_days=75` (≈ CEILING + max observed lag) to fully capture the W1 window. |
| H5 | **Reuse `scrapers/ohio/hamilton.py` read-only** | Import its schedule parser + address helpers; **do not modify** the filings scraper (it is a scheduled prod path). |
| H6 | **Throttle per-case fetches; accept a `skip_cases` set** | Hamilton went dark 27 days in 2026 from unthrottled per-case POSTs. Reuse `HAMILTON_REQUEST_DELAY`. The job passes already-stored Hamilton case numbers as `skip_cases` so terminal (already-captured) JFP cases aren't re-fetched. |
| H7 | **Plain `requests`, no browser** | Same as the filings scraper. |

## 6. Prod-safety contract

- **No new migration** (H1) — reuses isolated `ists_judgments`.
- **Zero edits to prod code paths** — `scrapers/ohio/hamilton.py` imported read-only.
- **Isolated writes** — writes only `ists_judgments`.
- **No SearchBug/Bland/GHL** → no spend, no messages.
- **Not wired into cron until smoke passes** — `--dry-run` first, then schedule
  near `ists_franklin` (mirroring A/A2 rollout).

## 7. Data model

No schema change. New rows in `ists_judgments`: `case_number` (PK),
`defendant_name`, `property_address`, `plaintiff_name`, `state='OH'`,
`county='Hamilton'`, `judgment_date` = disposition date, `disposition_desc` =
`JUDGMENT FOR PLAINTIFF`, `window='W1'`, `prior_phone`/`prior_bland_status` from
`services/ists_prior_work`, `source_url`. Reuse `models/judgment.py`
`JudgmentRecord` as-is.

## 8. Components & flow

| File | Purpose |
|---|---|
| `scrapers/ohio/hamilton_judgments.py` | Pure parsers (`parse_disposition`, `parse_parties`, `judgment_from_case`, `filter_by_judgment_window`) + `HamiltonJudgmentScraper`. Reuses `hamilton.py` enumeration + helpers read-only. |
| `jobs/run_ists_hamilton.py` | Orchestrator + `--dry-run`; `sys.path` bootstrap for the scheduler. Mirrors `run_ists_franklin.py`. |
| `tests/test_hamilton_judgments.py` | HTML-fixture parser tests (keep JFP / drop dismissed-undisposed / address+name gating / window). |

**Flow:** enumerate hearing dates over the lookback → unique case numbers (minus
`skip_cases`) → for each, GET summary, parse disposition; if `JUDGMENT FOR
PLAINTIFF` and judgment date ∈ `[today−CEILING, today−FLOOR]`, POST party →
build `JudgmentRecord` (gate name + address) → cross-reference prior work →
upsert (idempotent on `case_number`).

## 9. Metrics per run

Tenant-lost pool size; full-address rate (expect ~100%); judgment-date
distribution (tunes FLOOR/CEILING and `hearing_lookback_days`); prior-work
breakdown (phone-on-file / prior Bland); cases scanned vs. fetched (cost).

## 10. Window-2 opportunity (noted, not built)

`case_history_table` exposes `WRIT OF RESTITUTION ISSUED`, `NOTICE OF A WRIT OF
EXECUTION FOR PHYSICAL EVICTION`, and `PHYSICAL EVICTION EXECUTED` with dates —
the **W2** signal. Hamilton (like Franklin) is a candidate to feed both W1 and
W2; deferred to the W2 sub-project.

## 11. Testing & verification

- **Unit (HTML fixtures):** disposition parse (JFP vs DISMISSED vs UNDISPOSED);
  party parse (tenant/landlord name + address, incl. AKA-prefixed address cell);
  name gate (entity/placeholder drop); address gate (full vs partial); judgment
  window filter.
- **Live smoke:** `python -m jobs.run_ists_hamilton --dry-run` prints selected
  records + §9 metrics before any DB write.

## 12. Daily-cost note (for scheduling)

Unlike Franklin's single-GET monthly CSV, Hamilton is per-case. A 75-day hearing
lookback is ~2,600 summary GETs/run. `skip_cases` removes already-captured JFP
cases, and the throttle keeps the burst below the portal's threshold. If daily
cost proves too high, a follow-up can persist a "terminal-disposition" cache so
`DISMISSED`/decided cases aren't re-fetched. Confirm cost on the live smoke
before adding `ScheduledJob("ists_hamilton", …)`.
