# The Four Businesses

A 2×2 of **case type × stage**. All share the [[architecture]] pipeline; they differ in
*source*, *table*, *who the lead is*, and *scoring*. Brands: Grant Ellis Group (landlord
side) and Vantage Defense Group (tenant side) — never use legacy names in production copy
(see `AGENTS.md`).

|  | **Filed** | **Judgment / post-judgment** |
|---|---|---|
| **Eviction** | **Vantage / VDG** | **ISTS** |
| **Debt** | **Cosner Drake** | **Garnish Proof** |

The "lead person" differs but is always *who we contact*: tenant / defendant / debtor.
The contract ([[architecture]]) normalizes this. See [[data-model]] for columns, [[glossary]]
for EC/NG tracks.

## Vantage / VDG — eviction × filed
- **Lead:** the tenant just sued for eviction. **Source:** county eviction filings (many
  counties — see [[scrapers]]). **Table:** `filings` (+ `lead_contacts`, tracks `ec`/`ng`).
- **Value signal:** estimated rent. **Freshness:** `filing_date`. **Scoring profile:** `vantage`.
- **Status:** the core, highest-volume business. Multiple scrapers scheduled.

## ISTS — eviction × judgment
- **Lead:** the tenant who **lost** the eviction (judgment entered). **Source:** Harris JP
  judgments + Franklin OH FCMC dispositions. **Table:** `ists_judgments`.
- **Value signal:** estimated rent. **Freshness:** `judgment_date` (tight window). **Profile:** `ists`.
- **Status:** live; scrapers scheduled (`ists_harris`, `ists_franklin`).

## Cosner Drake — debt × filed
- **Lead:** the defendant just sued on a debt claim (the ~30-day Answer window before default).
  **Source:** Harris JP "Cases Filed / Debt Claim". **Table:** `cosner_filings`.
- **Value signal:** `debt_amount`. **Freshness:** `filing_date`. **Profile:** `cosner`
  (enrich largest debts first). **Deadline:** `answer_deadline`.
- **Status:** live; `cosner_drake` scheduled (ingest-only); enrichment manual (`run_cd_enrich`).

## Garnish Proof — debt × judgment (wage garnishment)
- **Lead:** the debtor with a garnishment writ. **Source:** **manual** Florida/Hillsborough
  garnishment-writ spreadsheet import (no automated scraper yet — sourcing is hard/blocked).
  **Table:** `garnishment_orders`.
- **Value signal:** none (amount usually absent) → score on name + **writ freshness**.
  **Freshness:** writ filed date (stored in `filing_date`). **Profile:** `garnish_proof`.
  **Deadline:** `exemption_deadline`.
- **Status:** live *only* via manual import (`scripts/import_gp_garnishment_xlsx.py`). The
  imported batch ages out of the 30-day enrichment window → **needs periodic fresh exports**.
  See [[decisions]] for the legal/sourcing background.

## Cross-business rules
- **ISTS wins over Vantage** for the same person (cross-track dedup in `queue_builder`).
- Each business is a separate GHL subaccount where applicable; firing respects the
  Bland 100/day-style limits per [[runbook]].
- Quality floor (valid split name + real street address + in freshness window + score ≥
  threshold) gates every paid step for every business. See [[glossary]].
