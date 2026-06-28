# Scrapers — status & rules

The contract: `scrapers/<state>/<county>.py`, class `XScraper(lookback_days=...).scrape()
-> list[Filing]`, date-enumerable by **filing date**, eviction-case-type filtered,
**address-complete** (street address), sets `last_error` on total failure, headless,
registered in the matching `jobs/run_<state>.py`. "Green" on a tracking sheet is a *claim* —
verify with a live `scrape()` before scheduling. See `.claude/skills/reviewing-scraper-prs`.

## Scheduled & working (in `SCHEDULED_JOBS` — see [[runbook]])
Texas/Harris, Tennessee/Davidson, Arizona/Maricopa, OH Franklin (raw push), OH Hamilton,
OH Montgomery, **OH Lorain, OH Butler, OH Barberton(Summit), FL Duval** (added 2026-06-28,
live-verified address-complete: Butler 100%, Lorain 90%, Barberton 100%, Duval 100%),
plus ISTS Harris + ISTS Franklin, and Cosner Drake (Harris debt-claim, ingest-only).

## On `main` but NOT scheduled / parked
- **FL Hillsborough (HOVER)** — best-quality source but behind a **PerimeterX "Press & Hold"**
  challenge that 403s datacenter IPs *and re-triggers on every page*. Routed via **Bright Data
  Scraping Browser** (`BRIGHTDATA_SB_WS`). Search works; **per-case address fetch is ~3% reliable**
  (the address lives only on each case's detail page, each re-challenged). Verdict: **parked** —
  not viable for address-complete leads via per-case fetch. Cap+retry hardening exists on branch
  `feat/hillsborough-retry`. OPEN QUESTION that would flip it: is the address on the search
  *results grid*? If yes, rebuild to read the grid (fast, 100%). See [[decisions]].
- **FL Miami-Dade / Broward** — dormant; `run_florida --counties` lets the scheduler skip them.

## Descheduled (broken, documented)
Tarrant TX (Bright Data tunnel failing), Cobb GA (geocoder/4% gate pass) — see
`docs/superpowers/specs/2026-05-29-*`.

## Open scraper PRs — reviewed, NONE production-ready (2026-06-28)
#9 Shelby TN (Cloudflare-blocked + silent failure), #49 Volusia FL (0% address — name-only),
#21 Fort Bend / #15 Galveston (wrong interface + standalone runner), #11 Montgomery TX
(exploratory script, no eviction filter), #44 (mislabeled Hillsborough dup — closed),
#51 (mixed Sarasota+Indiana — split needed). Each has a rework comment on the PR.

## Lessons (recurring failure modes)
- `0 filings` + `last_error=None` = **silent block**, not a quiet day. Set `last_error`.
- Datacenter/VPN IPs get 403'd by `.gov`/Cloudflare/PerimeterX portals → need residential
  (Bright Data) or a US IP; verify egress with `ipinfo.io/json`.
- Name-only sources (address only on detail / not exposed) are low-value — the pipeline needs
  street addresses ([[glossary]] quality floor).
