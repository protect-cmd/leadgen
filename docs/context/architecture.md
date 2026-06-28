# Architecture

How a court filing becomes a dialed lead. See [[businesses]] for per-business specifics,
[[data-model]] for tables, [[runbook]] for how it's operated, [[glossary]] for terms.

## The pipeline, as stages
The pipeline is **shared stages over per-business adapters** — not one table, not one
hardcoded flow. `pipeline/contract.py` normalizes every source into a `RawCourtRecord`
(three layers: `RawCourtRecord` → `LeadCandidate` → `OutreachState`) so the stages are
business-agnostic.

```
scrape → ingest → (gate + score) → [manual] enrich → stage(GHL) → fire(Bland)
  free        free        free          $ SearchBug    $/limits     $/limits
```

1. **Scrape** — `scrapers/<state>/<county>.py`, class `XScraper.scrape() -> list[Filing]`.
   Date-enumerable, address-complete sources only. See [[scrapers]].
2. **Ingest** — `pipeline/runner.py::_ingest_one`: dedup → insert → classify (lead_bucket).
   `services/dedup_service.py` does the Supabase writes.
3. **Gate + score** — `pipeline/gates.py` (address/name/freshness) + `pipeline/lead_score.py`
   (per-business profile). The scored, floor-passing, unenriched set = the "ready to enrich"
   queue (`pipeline/queue_builder.py` → `good_leads_now` view for Vantage).
4. **Enrich** — `pipeline/runner.py::_enrich_one` + `services/searchbug_service.py` (Vantage),
   `services/cd_enrich.py` (Cosner), `services/gp_enrich.py` (GP). **Pay-on-success**: SearchBug
   bills only on a returned phone.
5. **Stage** — `services/ghl_service.py` / `cd_ghl.py` / `gp_ghl.py` / `ists_ghl.py`: create
   GHL contact in the right subaccount/stage.
6. **Fire** — `services/fire_service.py` → `bland_service.py` / `ists_bland.py`: dial.
   DNC-gated at dial-time; Bland auto-call OFF by default (`AUTO_BLAND_CALLS_ENABLED`).

## Manual vs auto (current production model)
**Hands-off scrape+score, manual spend.** `PIPELINE_INGEST_ONLY=true` makes scheduled
`runner.run` STOP after ingest+classify — nothing auto-enriches. Enrichment + firing are
operator-triggered. The cap/weekend guardrails still apply to manual triggers. See [[decisions]]
(why) and [[runbook]] (switches).

## The spend guard (money safety)
`services/quota_service.py` + `quota_ledger` table + `quota_try_reserve` SQL fn. Every paid
path **reserves before acting, commits on a paid hit, rolls back a no-hit** (atomic,
per-business, fails closed). Caps come from the calendar budget ([[runbook]], [[decisions]]).
Wired into: runner (Vantage searchbug), `fire_service` (Bland), `cd_enrich`, `gp_enrich`.
Opt-in via `QUOTA_GUARD_ENABLED`.

## Monitoring
`scripts/verify_pipeline_health.py::notify_health` runs daily as the last step of
`scripts/post_scrape_chain.py` and pushes a Pushover summary (heartbeat + FAIL escalation).
Covers env/schema/scraper-freshness for all businesses + quota budget readout.

## Key files (so you don't have to grep)
- `pipeline/runner.py` — the orchestrator (ingest/enrich/stage+fire stage helpers).
- `pipeline/contract.py` — the shared lead contract + per-business adapters.
- `services/dedup_service.py` — all Supabase reads/writes for filings + lead_contacts + dashboard.
- `services/daily_scheduler.py` — the cron (`SCHEDULED_JOBS`). See [[runbook]].
- `services/quota_service.py` + `services/budget_schedule.py` — spend caps + weekend pause.
- `dashboard/main.py` + `dashboard/index.html` — the review/approval UI.

## Deploy
Railway service `resourceful-endurance / production / leadgen`, deploys from `main`.
`railway.toml` startCommand runs the dashboard + in-process scheduler. See [[runbook]].
