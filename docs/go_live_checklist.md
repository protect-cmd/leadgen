# Go-Live Checklist — 4-Business Pipeline

Plain-English steps to turn on everything built on `feat/four-business-pipeline`.
Nothing here is on in production yet **except** two safe data changes already applied
(see "Already done"). Work top to bottom.

---

## What this gets you
- One shared pipeline for all 4 businesses (Vantage, ISTS, Cosner Drake, Garnish Proof).
- **Spend safety:** SearchBug + Bland can never overspend or burn on a dry day
  (per-business daily caps; fails closed).
- **Reliable monitoring:** a daily Pushover summary so you stop babysitting.
- Garnish Proof producing leads from the Florida spreadsheet.

## Already done (no action needed)
- ✅ Migration `028_quota_ledger` applied to the live database (the quota engine).
- ✅ 80 Garnish Proof leads imported into `garnishment_orders` (100% with address).
- ✅ All code committed + pushed to `feat/four-business-pipeline`.
- ✅ Verified: all 4 businesses have data; test suite green (pre-existing failures only).

---

## Step 1 — Merge the branch to `main` (this is what ships the code)
**Why:** Railway deploys from `main`. Until the branch merges, none of the new pipeline
code (spend guard, monitoring, runner split, GP importer) is actually running in
production — it only lives on the branch.

- Open a PR from `feat/four-business-pipeline` into `main`, review, and merge
  (admin-merge, same as #41/#48 — `main` has a review-only ruleset, no CI).
- Merging triggers a Railway deploy. **Behavior won't change yet** — every new feature
  is behind an off-by-default flag (Steps 2–3). This is intentional: ship dark, enable
  deliberately.

## Step 2 — Turn on the spend guard (when you've picked caps)
Set these on Railway (`leadgen` service → Variables):

| Variable | What it does | Suggested start |
|---|---|---|
| `QUOTA_GUARD_ENABLED` | master switch for per-business spend caps | `true` |
| `QUOTA_CAP_VANTAGE_SEARCHBUG` | max SearchBug lookups/day for Vantage | e.g. `100` |
| `QUOTA_CAP_ISTS_SEARCHBUG` | … ISTS | e.g. `50` |
| `QUOTA_CAP_COSNER_SEARCHBUG` | … Cosner | e.g. `50` |
| `QUOTA_CAP_VANTAGE_BLAND` | max Bland dials/day for Vantage | e.g. `100` |
| `QUOTA_CAP_ISTS_BLAND` | … ISTS | e.g. `50` |
| `QUOTA_DEFAULT_CAP` | fallback for anything not set above | `100` |

Notes:
- A cap is your standing approval — within it the pipeline spends automatically,
  hands-off. Hit the cap and it **holds** the rest (no spend, leads retried later).
- It **fails closed**: if the quota database is ever unreachable, it denies spend
  rather than risk a burn.
- Start conservative; raise a number when you want more volume. Lowering/raising a cap
  is the only spend decision you ever have to make.

## Step 3 — Turn on health alerts
| Variable | What it does |
|---|---|
| `PUSHOVER_ENABLED` | `true` to receive the daily health summary + failure alerts |

(`PUSHOVER_APP_TOKEN` / `PUSHOVER_USER_KEYS` are already set.) You'll get one Pushover
each day after the scrape window: "Pipeline health: N OK / N FLAG / N FAIL" with any
problems listed. A dark scraper, stale data, or schema drift pages you automatically.

## Step 4 — (Optional) Garnish Proof outreach
GP leads are staged but not contacted. When you want to work them, run GP outreach
manually (`jobs/run_gp_outreach.py`) — it goes through the same enrichment/DNC/quota
path. Re-importing the spreadsheet later is safe (idempotent on case number).

---

## How to roll back
Everything new is flag-gated, so rollback is just flipping a switch — no redeploy:
- Spend guard misbehaving → set `QUOTA_GUARD_ENABLED=false` (reverts to the old global
  Bland cap; SearchBug runs as before).
- Too many alerts → set `PUSHOVER_ENABLED=false`.
- Need to undo the GP import → `DELETE FROM garnishment_orders WHERE source_url =
  'manual_import:florida_wage_garnishment_xlsx';`

## Still open (not blocking go-live)
- Per-business **scoring profiles** for Cosner/GP (Phase 6) — Vantage + ISTS already scored.
- **Schedule shift** so good leads finish before 9 PM PHT / 13:00 UTC (Phase 8).
- **DNC enum** cleanup (Phase 4).
- Scraper backlog (Volusia/Shelby/etc.) — all need rework; none production-ready (see
  PR comments).
