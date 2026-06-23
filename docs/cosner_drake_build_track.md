# Cosner Drake — Build Track

**Status:** starting. This is the sibling brand to Garnish Proof; see `docs/garnish_proof_build_status.md` for the pipeline pattern we reuse.

## What Cosner Drake is

Document-prep brand for consumers who were **just sued by a debt collector** and have a hard **30-day window to file a written Answer** before the court enters a **default judgment** against them. We prepare that Answer. The client is the **defendant** (the consumer being sued), same defendant-side model as Vantage / ISTS / Garnish Proof.

- **Stage:** fresh filings, **pre-judgment**. Reach them the same day the case hits the docket, before they're served (same as how we reach tenants pre-service in evictions). Earlier = better close rate.
- **Volume:** ~4–5M debt-collection lawsuits filed annually nationwide — much higher than evictions.
- Cosner Drake is the **upstream** half of the same debt lifecycle Garnish Proof sits at the downstream end of (lawsuit filed → [30-day Answer window: Cosner Drake] → default judgment → [Garnish Proof] → garnishment).

## The source (already verified)

**Same Harris JP extract system we already scrape.** Cosner Drake = the **"Cases Filed" extract × "Debt Claim" case type** (the lawsuit *filings*, pre-judgment), which carries the **defendant's full home address** (verified during the Garnish Proof audit — the Harris extract's Debt Claim type is address-complete). This is the exact precursor stage to the Garnish Proof judgment feed.

Difference from Garnish Proof's source: GP uses **"Judgments Entered"** + a default-judgment filter; Cosner Drake uses **"Cases Filed"** with **no disposition filter** (there's no judgment yet — these are brand-new filings).

## Reuse plan (mirror Garnish Proof, which mirrors ISTS)

Almost everything reuses. The pipeline is: scrape → store → enrich (SearchBug) → DNC (shared `dnc_service`) → GHL push (CD tag) → Bland (CD script). Only config + the source stage differ.

**Architectural opportunity:** `scrapers/texas/harris_judgments.py` is already parameterized by `casetype`. To serve Cosner Drake cleanly, also parameterize the **extract** ("Cases Filed" vs "Judgments Entered"). One Harris extract-downloader could then serve all four products:
- Vantage = Cases Filed × Eviction
- ISTS = Judgments Entered × Eviction
- Cosner Drake = Cases Filed × Debt Claim
- Garnish Proof = Judgments Entered × Debt Claim

## Build steps (each mirrors the GP equivalent)

1. **Table:** `migrations/0XX_cosner_filings.sql` — mirror garnishment_orders shape (defendant name + address, filing_date, case_number, creditor/plaintiff, language_hint, phone, enriched_at, ghl_contact_id, ghl_pushed_at, bland_call_id, bland_triggered_at). Add an `answer_deadline` (filing_date + 30d) as the urgency clock.
2. **Model:** a `CosnerFiling` dataclass (or reuse `models/filing.py` `Filing` since these ARE filings, not judgments) + `to_row()`.
3. **Scraper:** `scrapers/texas/harris_debt_claims.py` — Cases Filed × Debt Claim; keep individual defendants with a home address (reuse `gate_name` / `gate_address`); NO defendant-lost filter. Parameterize the extract on `HarrisJudgmentScraper` or fork the downloader.
4. **Ingest job:** `jobs/run_cd_harris.py` (mirror `run_gp_harris`).
5. **Store/enrich:** `services/cd_store.py`, `services/cd_enrich.py` (mirror gp_store/gp_enrich; 30-day Answer-window freshness).
6. **Outreach:** `services/cd_ghl.py` (tag `cosner-drake-lead`, CD subaccount config), `services/cd_bland.py` (CD "Answer deadline" script, BLAND_CD_* config, reuse shared dnc_service + ISTS call helpers), `jobs/run_cd_outreach.py`.
7. **Env:** add `GHL_CD_*` and `BLAND_CD_*` to `.env.example`.

## Differences from Garnish Proof (what actually changes)

| | Garnish Proof | Cosner Drake |
|---|---|---|
| Source stage | Judgments Entered (post-judgment) | **Cases Filed (pre-judgment)** |
| Disposition filter | default judgments only | **none** (new filings) |
| Urgency clock | vacate window (judgment + ~30d) | **Answer window (filing + 30d)** |
| Pitch / script | "judgment entered, Motion to Vacate + exemption" | **"you've been sued, file an Answer in 30 days or you'll default"** |
| Brand routing | `garnish-proof-lead` tag, GP subaccount, "Alex" agent | **`cosner-drake-lead` tag, CD subaccount, CD agent** |
| Extra scope (per Chris) | none | **needs a full website build** (more complex than GP) |

## Dependencies (same shape as GP)

- **Jonas:** Cosner Drake GHL subaccount + `GHL_CD_*` config + custom-field UUIDs.
- **Chris:** the "you've been sued / file your Answer" Bland + SMS script; the website; Stripe links.

## Reference

- Pipeline pattern + reuse details: `docs/garnish_proof_build_status.md`
- Source audit + Harris extract facts: memory `project_court_source_audit`, `project_garnish_proof_feasibility`, `project_ists_harris_civil_extract`
