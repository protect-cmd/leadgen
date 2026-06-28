# Runbook (ops)

How the pipeline is operated, scheduled, and switched. See [[architecture]] for how it works,
[[decisions]] for why. Deploy: Railway `resourceful-endurance / production / leadgen`, from `main`.

## Daily schedule (UTC → PHT = +8)
Set BACKWARD from the deadline: **good leads must be ready by 9 PM PHT = 13:00 UTC**, so the
whole chain finishes before 13:00. `services/daily_scheduler.py::SCHEDULED_JOBS`:

```
10:30 texas (Harris)        11:35 ohio_barberton(Summit)   12:20 ists_harris
10:50 tennessee (Davidson)  11:40 florida_duval            12:25 ists_franklin
11:10 arizona (Maricopa)    11:50 ohio_franklin_raw        12:40 post_scrape_chain (+health/Pushover)
11:20 ohio_lorain           12:10 ohio_hamilton            12:50 cosner_drake
11:25 ohio_butler           12:15 ohio_montgomery
```
Test `test_daily_scheduler` asserts every job starts < 13:00 UTC. `post_scrape_chain` =
flag_enrichable → normalize_court_date → rent → **health check**. See [[scrapers]] for status.

## Operating model
**Hands-off scrape+score, MANUAL spend** (operator's standing choice — see [[decisions]]).
- Scheduled jobs scrape + ingest + score, then **stop** (`PIPELINE_INGEST_ONLY=true`).
- Operator triggers enrichment/firing (dashboard "Enrich selected" / `run_cd_enrich` /
  `run_gp_*`). Guardrails (budget + weekend) still apply.

## Spend budget (calendar tiers, per business)
`services/budget_schedule.py`. SearchBug bills **$1 per successful lookup** (no-hits free), so
caps count PAID hits. PDT day-of-month tiers (env-overridable):
- **GREEN 6–17 = $125** /business/day · **RED 18–28 = $35** · **YELLOW 29–05 = $75**
- **Weekend pause (PHT):** Sat/Sun → no enrich/GHL/Bland; scraping still runs.

## Railway env flags (the switches)
| Flag | Meaning | Prod value |
|---|---|---|
| `PIPELINE_INGEST_ONLY` | scheduled runs stop after ingest (manual spend) | `true` |
| `QUOTA_GUARD_ENABLED` | enforce per-business caps + weekend pause + pay-on-success | `true` |
| `WEEKEND_PAUSE_ENABLED` | hold paid actions on PHT weekends | `true` |
| `QUOTA_BUDGET_GREEN/YELLOW/RED` | tier caps | `125/75/35` |
| `AUTO_BLAND_CALLS_ENABLED` | auto-dial via Bland (else manual) | `false` |
| `PUSHOVER_ENABLED` | daily health pushes | `true` |
| `BRIGHTDATA_SB_WS` | Bright Data Scraping Browser endpoint (Hillsborough) | set (residential) |
| `TENANT_TRACK_ENABLED` / `LANDLORD_TRACK_ENABLED` | which Vantage track(s) run | `true` / `false` |

Rollback = flip the relevant flag (no redeploy needed for behaviour). Setting a Railway var
triggers a redeploy; use `--skip-deploys` if you must not restart (e.g. mid-SSH).

## Running things by hand
- One scraper in prod (residential IP): `railway ssh "python jobs/run_<state>.py [--counties X]"`.
  `railway run` injects env but runs LOCALLY (local IP); `railway ssh [cmd]` runs IN the container.
- Bright Data is a *remote* browser, so Bright Data scrapers can be tested from anywhere by
  setting `BRIGHTDATA_SB_WS` locally.
- GP fresh import: `python scripts/import_gp_garnishment_xlsx.py --path <xlsx> --yes-write-supabase`.
- Health check now: `python scripts/verify_pipeline_health.py`.

## Merging / PRs
`main` has a review-only ruleset (no CI). Owner merges with `gh pr merge --admin --squash`
(operator standing-authorized this). Migrations are NOT auto-applied — apply via Supabase/MCP
and verify columns exist before relying on them.
