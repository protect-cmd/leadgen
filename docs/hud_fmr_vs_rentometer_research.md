# HUD FMR/SAFMR vs Rentometer — research before replacing the rent signal

Date: 2026-06-16
Context: Chris Movado's "Priority Zip Code Filter" email (Jun 10) proposes using free HUD
Fair Market Rent data to rank high-rent ZIPs to the top of the enrichment queue while
Rentometer is being built. Zee asked whether HUD can *replace* Rentometer, ideally behind
an on/off switch. This memo records what HUD actually provides and recommends an approach.

## TL;DR

- HUD data is real, free, national, ZIP-level, and trivially integrable. Keep it.
- But HUD rent is the **40th-percentile, subsidy-standard rent**, not market rent. It
  systematically understates high-end rent and **compresses the spread between rich and
  poor ZIPs** — which directly weakens the one thing the priority filter exists to do.
- Recommendation: add HUD as a **free, always-on default/fallback** rent signal behind the
  existing `RENT_PRECHECK_PROVIDER` toggle. Keep Rentometer (and evaluate RentCast) as the
  **precision layer** for top-of-queue leads. Do not retire Rentometer; layer them.

## What HUD actually publishes

Two different products — this distinction is the whole story:

1. **FMR (area-wide Fair Market Rent)** — one rent per **county / metro area**.
   - The file already downloaded at `.firecrawl/FMR_All.xlsx` is this one
     (`FMR_ALL_1983_2026`, fips-keyed, `fmr26_0`..`fmr26_4` = 0–4BR for FY2026).
   - **This is the wrong file for a ZIP filter** — every ZIP in Harris County gets the
     same number. The email's "rent data by zip code" is a misnomer for this file.

2. **SAFMR (Small Area FMR)** — one rent per **ZIP code**. This is what we want.
   - Full national file: `https://www.huduser.gov/portal/datasets/fmr/fmr2026/fy2026_safmrs_revised.xlsx`
     (cached at `.firecrawl/fy2026_safmrs.xlsx`, 4.4 MB, **51,895 ZIP rows**).
   - Published for **all** metropolitan areas (same metros as area-wide FMR), not just the
     24 metros mandated to use SAFMRs for vouchers — so all our markets are covered.
   - Columns: `ZIP Code`, `HUD Area Code`, area name, then SAFMR 0BR–4BR (plus 90%/110%
     payment-standard variants). 2BR is column J.

### Verified coverage of our priority ZIPs (FY2026 SAFMR 2BR)

| ZIP   | Area                         | SAFMR 2BR |
|-------|------------------------------|-----------|
| 77005 | Houston (West University)    | $2,360    |
| 77019 | Houston (River Oaks)         | $2,360    |
| 76109 | Fort Worth                   | $2,020    |
| 37215 | Nashville                    | $2,200    |
| 43221 | Columbus                     | $1,750    |
| 45208 | Cincinnati                   | $1,670    |

## The problem: SAFMR compresses the high end

The filter's value is ranking *genuinely* high-rent tenants to the top. Two findings show
SAFMR is a blunt instrument for that:

- **77005 (West University) and 77019 (River Oaks) both return $2,360 for 2BR — identical.**
  River Oaks is one of the most expensive neighborhoods in Texas; real 2BR market rent there
  is well north of $3,000. SAFMR cannot tell the two apart — the 40th-percentile floor
  collapses distinct high-end ZIPs onto the same number.
- HUD is a subsidy standard, recalculated annually from ACS, capped and smoothed — by design
  it tracks the low-to-middle of the market, not the top. The email's $1,800–$3,500 band for
  these ZIPs is wider than SAFMR's spread, which is exactly the resolution loss at issue.

Net: SAFMR will rank ZIPs in roughly the right order across a wide income range
(Cincinnati $1,670 < Columbus $1,750 < Nashville $2,200 < Houston $2,360), but it loses
resolution exactly where this product makes money — separating "nice" from "luxury."
Rentometer's per-address market estimate keeps that resolution.

## Access options (both free)

- **Bulk file (recommended):** download `fy2026_safmrs_revised.xlsx` once, load ZIP→2BR into
  a lookup table/dict. No keys, no rate limits, refresh ~annually (FY files publish each fall).
- **Official API:** `https://www.huduser.gov/hudapi/public/fmr` — free Bearer token from
  huduser.gov. `GET /fmr/data/{entityid}` returns ZIP-level `basicdata` for SAFMR metros.
  Keyed by HUD entity/metro id, not raw ZIP, so the bulk file is simpler for our use.
  Note: huduser.gov blocks default curl UA — needs a browser User-Agent header.

## How it fits our code (low effort)

- `services/rent_estimate_service.py` already has the switch: `RENT_PRECHECK_ENABLED` +
  `RENT_PRECHECK_PROVIDER` (default `rentometer`). Adding a `hud`/`safmr` provider that does
  a ZIP→rent dict lookup is a clean drop-in — no schema change required; it writes the same
  `filings.estimated_rent` column that `scripts/backfill_rent.py` already populates.
- Rent is a **priority/ranking signal, never a discard gate** (see
  `pipeline/qualification.py` — Phase 1 removed the rent threshold). So a coarser HUD number
  is safe: worst case it mis-ranks, it never drops a lead.

## Recommendation

1. Build a HUD SAFMR provider as the **free default**: every lead gets a ZIP-based rent
   estimate at zero marginal cost, replacing the manual "priority ZIP list" with a real
   national rent number.
2. Keep `RENT_PRECHECK_PROVIDER` as the toggle: `hud` (free, always-on) ⇄ `rentometer`
   (paid, precise). Optionally a `both` mode: HUD for the bulk ranking, Rentometer only on
   the top N to break ties at the high end.
3. Tag the rent source on the lead (`rent_source = hud_safmr | rentometer`) so we can audit
   which signal drove each ranking and compare yield.
4. Do **not** retire Rentometer. HUD removes the manual ZIP list and gives free baseline
   coverage; Rentometer (or RentCast — see `.firecrawl/search-rentcast-pricing.json`) stays
   the precision layer where the high-end compression actually costs us money.

## Open items

- Confirm bedroom assumption: backfill/Rentometer default to 2BR; HUD SAFMR has 0–4BR — keep
  2BR for apples-to-apples unless we capture unit size.
- Decide refresh cadence for the SAFMR file (annual; FY2026 already out, "revised" is latest).
