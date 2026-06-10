# Lead System — Continuous Implementation Plan

**Date:** 2026-06-10
**Branch:** `feat/lead-quality-flag` (6 commits)
**Goal:** revenue — quality leads, SearchBug spent on best-match callable numbers, both businesses (VDG + ISTS).

## Done (live)
- `is_enrichable` flag + `good_leads_now` view (gates: name + address + court + 21d fresh + not-phoned)
- Priority-ZIP queue (Houston→Tarrant→Davidson→Franklin→Hamilton) + court_date sentinel fix
- Phase 1: dropped ZIP/rent/held gates → **is_enrichable 3,119 → 10,109**, good_leads_now **2,958**
- `lead_score` (0-100: match 40 + coverage 35 + freshness 25) → SearchBug-optimal ordering
- `morning_queue` CLI + `/lists` dashboard (To-Enrich / To-Fire, select-first-N, CSV export)
- Loop closed: `enrich_stage_bland --from-queue` enriches in priority+score order

## Backlog (prioritized)

### P1 — Unblock ISTS (zero fresh leads right now)
ISTS judgments stop at 2026-06-02; the 7-day window is empty. Nothing downstream matters
until the scraper runs.
- **[scraper/user]** Run the Harris judgments scraper → fresh `ists_judgments`.
- **[me]** Phase 3 parity once data flows: `ists_judgments.is_enrichable` + `good_judgments_now`
  view (tenant-lost + name + address + 7-day + not-prior-worked), join `priority_zips`.
  ~30 min once data exists.

### P2 — "Fire" from the dashboard (action, not just export)
Today `/lists` is read+export. Add a **"Fire selected"** action that stages-to-GHL + dials-Bland
the selected To-Fire leads (reuse `stage_held_nolist`/runner logic), respecting the Bland rate
limit (batch + mark pending on 429). Makes the To-Fire tab operational, not just a worklist.

### P3 — Cross-track dedup (ISTS wins)
Person-key = normalized name + property ZIP. Before staging/dialing on either track, suppress
the other if the person is being worked on ISTS. Prevents a tenant getting both offers.

### P4 — Score feedback loop
Persist `lead_score` + log each enrichment outcome (`callable | held | on_dnc | no_record`).
Recompute coverage weights from real outcomes so the score self-tunes. Honest on-ramp before
any ML.

### P5 — Per-lead Rentometer rent in the score
Today rent is a priority-ZIP proxy only. Capture Rentometer median per lead (lazily for the top
of the queue) and fold it into the value weight so the rent tail orders by real rent.

### P6 — Self-heal ZIP (Phase 2)
Census geocoder to recover missing ZIPs (49 discarded today) before `gate_address`, so we stop
dropping otherwise-good leads and sharpen SearchBug matches.

### P7 — DNC coverage (the held problem)
Research the **DNCScrub.com API** the operator now has (covers local codes today — check extra
features). Goal: scrub every area code so out-of-market phones convert from `held` → callable
(~28% of found phones today). Highest lever on callable volume.

### P8 — Cleanup
Migrate the legacy `select_*` / `rentometer_rank` scripts to read `good_leads_now` so there's one
definition of "good lead." Open the PR for the branch.

## Infra (user-owned scrapers)
- **Tarrant/Fort Worth** (priority #2): new scraper + `817`/`682` DNC files — currently 0 filings.
- **Hamilton/Cincinnati** (priority #5): unblock `courtclerk.org` 403 (residential proxy) — 60 stale rows.
- **Davidson/Hamilton**: write `court_date = NULL` at source (stop copying filing_date); until then
  run `scripts/normalize_court_date.py` after each scrape.

## Suggested order
P1 (ISTS scraper) → P2 (Fire button) → P3 (dedup) → P7 (DNC research) → P4/P5 (score refinement)
→ P6 (self-heal) → P8 (cleanup + PR). P1 and the infra items are the revenue unblockers.
