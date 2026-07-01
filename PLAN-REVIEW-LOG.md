# Plan Review Log: Get the 4 businesses live on one clean, hands-off pipeline
Act 1 (grill) complete — plan locked with Zee. MAX_ROUNDS=5.

## Environment note
Codex's Windows read-only sandbox helper (`codex-windows-sandbox-setup.exe`) is not
installed, so `-s read-only` / `-c sandbox_mode=read-only` cannot launch a shell.
Workaround: run with `sandbox_mode=danger-full-access` but embed the full plan inline
and instruct Codex strictly to review-only (read, never write). Output watched.

## Round 1 — Codex (thread 019f0b02-0294-72f0-8c05-fdd2a88e5464)
**VERDICT: REVISE.** 12 findings, all legitimate and grounded in real files:
1. Four businesses have separate tables (filings/lead_contacts, ists_judgments,
   cosner_filings, garnishment_orders) — no single canonical row. → adapters not one table.
2. Canonical record incomplete/sloppy (omits case_number, deadlines, contact/DNC/outreach
   state; repeats state). → split into RawCourtRecord / LeadCandidate / OutreachState.
3. "De-inline Texas" understates blast radius — runner.run does ingest+enrich+GHL+
   Instantly+Bland in one loop. → split into ingest_only/enrich/stage/fire.
4. Quality floor must gate GHL/Bland, but GHL+Instantly fire BEFORE DNC gate today.
   → single shared pre-external-action guard.
5. DNC enum inconsistent (callable/dnc/unknown vs clear/blocked/unknown). → unify + mapper.
6. Daily cap unsafe — local SQLite cap that ISTS/GP/Cosner bypass. → atomic DB quota service.
7. Per-business scoring profiles don't exist (one tenant scorer). → build+test first.
8. Scheduling contradictory — first scrape starts 13:00, last 15:30. → schedule backward.
9. Health monitor lacks cosner_drake + separate-table coverage. → per-business + Pushover-as-code.
10. Upsert fix underspecified (nullable upserts; cd_store/gp_store blind upserts). → COALESCE rules.
11. Phase 0 risky — dirty worktree could bake half-built behavior. → split into focused commits.
12. GP importer mapping wrong vs garnishment_orders schema (Miami-Dade default,
    exemption_deadline, filing_date). → full spreadsheet mapping + source hash + Hillsborough.

### Claude's response
Accepted all 12 (every one verified against real files). Rewrote PLAN.md: reframed to
shared stages + per-business adapters over existing tables; three-layer contract; split
runner into ingest/enrich/stage/fire; single pre-external-action guard + atomic quota
service; DNC enum unification phase; per-business scoring-profile phase; data-loss fix
with composite dedup key + COALESCE upserts across lead_contacts/cd_store/gp_store;
schedule-backward-from-13:00-UTC; health monitor per business/table + Pushover-as-code;
Phase 0 worktree-split-first; fully specified GP importer into garnishment_orders.
Rejected nothing.

## Round 3 — Codex (same thread)
**VERDICT: APPROVED.** "Round 2 findings are addressed well enough to begin
implementation." Plan now handles composite-key migration as staged schema work,
resolves the hands-off spend model, splits pre/post enrichment guards, preserves
rejected-row observability, adds queue/fire adapters, defines quota idempotency,
accounts for portal timing, and adds the test bar. Two non-material cleanups requested:
fix duplicate Phase 3/4 step numbering, and align Goal/Key-Decisions wording to
"two-stage spend guard". Both applied. Converged in 3 rounds (cap was 5).

---
(Round 2 detail below.)

## Round 2 — Codex (same thread)
**VERDICT: REVISE.** 10 deeper findings, all legitimate:
1. Composite dedup is a staged SCHEMA migration — case_number is PK on all 4 tables;
   lead_contacts keys (case_number,track). Need lead_id/composite key + FK/view migration first.
2. DNC can't gate SearchBug (needs phone from SearchBug). → split guard pre/post enrichment.
3. Quality floor circular — score depends on enrichment-derived rent. → pre-paid score (free/static) + post-enrichment rescore.
4. Ingest-only "gate before persist" drops rejected rows + observability. → persist rejected rows/metrics.
5. Queue/dashboard/fire cover only Vantage+ISTS. → adapter registry + Cosner/GP surfaces.
6. Quota concurrency/idempotency unspecified. → reserve/commit/rollback + idempotency keys per (business,action,lead,day).
7. "Hands-off vs spend money" contradiction. → caps = standing pre-approval; hard stops only for cap changes/one-offs/git/go-live.
8. Earlier scheduling ignores portal availability (Harris maintenance). → per-portal earliest-safe-run constraints.
9. "Quota exhausted" as health FAIL = noisy. → budget-limit status unless paired with anomaly.
10. Verification omits migration-order + regression. → migration tests, stage tests, pytest -q.

### Claude's response
Accepted all 10. Added a Spend-authority model section (caps = standing pre-approval).
Reordered Phase 2 to dedup→upsert→gate and added persist-rejected-rows + a queue/fire
adapter registry for Cosner/GP. Reframed Phase 3 as a staged schema migration
(lead_id/composite key before dedup logic). Split Phase 5 into pre-enrichment
(floor+quota reservation) and post-enrichment (DNC+channel) guards; specified quota
state transitions + idempotency keys. Split Phase 6 into pre-paid vs post-enrichment
scoring. Added per-portal safe-run constraints to scheduling. Made cap exhaustion a
budget-limit status, not FAIL. Added migration/stage/quota tests + pytest -q to go-live.
Rejected nothing.
