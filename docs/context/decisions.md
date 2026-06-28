# Decisions (ADRs)

Why the system is the way it is — so you don't re-litigate settled calls. Newest first.
Each: decision, why, status.

## D-009 Hillsborough parked (per-case address fetch not viable) — 2026-06-28
HOVER is behind PerimeterX "Press & Hold" that re-triggers on every page. Bright Data solves it
for *search* but per-case detail fetches (where the address lives) succeed ~3%. **Decision:**
don't schedule Hillsborough; park it. Flip condition: if the address is on the results grid,
rebuild to read the grid. See [[scrapers]].

## D-008 Calendar enrichment budget + pay-on-success — 2026-06-28
Per-business daily caps by PDT day-of-month tier (green/yellow/red = $125/75/35), weekend (PHT)
pause. SearchBug bills **only on a successful lookup**, so the quota commits on a hit, rolls
back no-hits → caps count PAID leads, not attempts. **Why:** operator's trend analysis (lead
strength varies by day) + cost control. `services/budget_schedule.py`. See [[runbook]].

## D-007 Manual spend model (hands-off scrape+score, manual enrich/fire) — 2026-06-28
`PIPELINE_INGEST_ONLY=true`: scheduled runs stop after ingest+classify; operator triggers all
paid steps. **Why:** operator wants control over every dollar; quality over quantity; the
scored "ready to enrich" queue gives a reviewable list. Auto is one flag away if trust grows.

## D-006 Two-stage spend guard + atomic quota service — 2026-06-28
One `quota_ledger`-backed reserve/commit/rollback service, per-business, fails closed; DNC
gates the *post*-enrichment channels (can't DNC-check before you have a phone). Replaces the
old global SQLite cap. **Why:** make overspend/burn structurally impossible. `quota_service.py`.

## D-005 Defer the composite-key dedup migration (evidence-based) — 2026-06-28
Live-DB audit: every county uses a distinct `case_number` format, zero cross-county collisions,
1 FK. **Decision:** do NOT do the risky PK migration; the real lead-loss was the `enriched_at`
burn, fixed by the quota guard. **Why:** risk to prod with no current benefit. See [[data-model]].

## D-004 Shared stages + per-business adapters (not table unification) — 2026-06-28
`pipeline/contract.py` normalizes 4 separate tables into one `RawCourtRecord`; runner split into
ingest/enrich/stage/fire. **Why:** the businesses already have separate stores; unifying tables
is high-risk. Lower-risk, respects what works. See [[architecture]].

## D-003 Garnish Proof via manual spreadsheet import — 2026-06-28
GP garnishment writs aren't reliably scrapable (legal/WAF). **Decision:** feed GP from a manual
FL/Hillsborough xlsx through the same contract; automated scraper deferred. **Why:** gets the 4th
business live now; proves "any source plugs in". Needs periodic fresh exports. See [[businesses]].

## D-002 DNC enum unified to callable/dnc/unknown — 2026-06-28
Dashboard read `clear`/`blocked` while backend/DB write `callable`/`dnc`/`unknown` → APPROVE
never showed. Unified on backend's enum; legacy values accepted via a mapper. See [[data-model]].

## D-001 Clean-trunk consolidation — 2026-06-28
The feature branch had diverged badly (behind main, which had grown the infra). Rebuilt a clean
trunk off latest `main`, re-applied GP + Cosner, triaged ~25 branches / 11 PRs. Plan hardened
via grill + 3 Codex review rounds (`PLAN.md` / `PLAN-REVIEW-LOG.md`).

## Standing operator preferences
- Drive work to completion; batch questions; only stop at hard gates (spend money / destructive
  git / outward push). Admin-merge PRs (`gh pr merge --admin`) is authorized.
- No emojis / AI-status-lines in PR comments — plain prose.
- No Melissa for people-search; SearchBug is the vendor (Enformion under eval).
