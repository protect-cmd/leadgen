# Glossary

Project-specific terms. See [[index]] for the map.

- **The four businesses** — Vantage/VDG, ISTS, Cosner Drake, Garnish Proof. See [[businesses]].
- **EC / NG track** — `lead_contacts.track`. `ec` = landlord side (Grant Ellis Group);
  `ng` = tenant side (Vantage Defense Group). Legacy NG = "Nobles & Greyson" (do not use).
- **Quality floor** — the bar every lead must clear before any **paid** step: valid split
  first+last name + real **street** address + inside the freshness window + score ≥ the
  business threshold. Quality over quantity; a thin day costs less, never pads junk.
- **Pre-paid vs post-enrichment score** — pre-paid uses only free/scraped/static data (our ZIP
  rent medians, freshness, name/address quality) and gates the floor; post-enrichment rescore
  may use enrichment-derived fields, for ranking only. `pipeline/lead_score.py`.
- **Freshness window** — only enrich leads still actionable: Vantage filing window
  (`ENRICHMENT_WINDOW_DAYS`), ISTS judgment window, Cosner Answer window (`CD_FRESHNESS_DAYS`=30),
  GP exemption window (`GP_FRESHNESS_DAYS`=30).
- **`good_leads_now`** — Supabase view: Vantage leads ready to enrich (gated, fresh, not yet
  enriched). Basis of the "To Enrich" queue. See [[data-model]].
- **To Enrich / To Fire** — dashboard queues. To Enrich = scored, ready for a paid lookup.
  To Fire = enriched + `dnc_status='callable'`, ready to dial.
- **Quota guard / `quota_ledger`** — atomic per-business spend caps (reserve/commit/rollback).
  See [[architecture]], [[runbook]].
- **Pay-on-success** — SearchBug charges $1 only when it returns a number; no-hits are free, so
  the daily cap counts paid hits (commit on hit, rollback on no-hit).
- **DNC verdict** — `callable` | `dnc` | `unknown` (`services/dnc_service.py`, DNCScrub.com).
  Fire requires `callable`; fail closed. See [[data-model]].
- **Pay tiers (budget)** — green/yellow/red = $125/75/35 per business/day by PDT day-of-month.
  See [[runbook]], [[decisions]].
- **Weekend pause** — no paid actions on PHT Sat/Sun; scraping continues. `WEEKEND_PAUSE_ENABLED`.
- **Ingest-only** — `PIPELINE_INGEST_ONLY=true`: scheduled runs scrape+score then stop; spend is
  manual. The current production model. See [[decisions]] D-007.
- **Bright Data Scraping Browser** — residential-IP remote browser with auto CAPTCHA solving,
  used for WAF/PerimeterX-protected portals (Hillsborough). `BRIGHTDATA_SB_WS`. See [[scrapers]].
- **Press & Hold / PerimeterX / HUMAN** — the bot challenge on HOVER (Hillsborough). See [[scrapers]].
- **SearchBug** — the people-search/enrichment vendor (phone from name+address). NOT Melissa.
- **GHL** — GoHighLevel CRM (contacts/pipelines). **Bland** — Bland.ai voice dialer.
- **Grill / Codex review** — the plan-hardening workflow used in `PLAN.md` (you ↔ Claude
  interview, then Claude ↔ Codex adversarial review). See `.claude/skills/grill-me-codex`.
- **PHT / PDT / UTC** — operator is PHT (UTC+8); budget tiers are in PDT; schedule is UTC.
  9 PM PHT = 13:00 UTC (the good-leads deadline).
