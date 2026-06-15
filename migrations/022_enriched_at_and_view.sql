-- 022_enriched_at_and_view.sql
--
-- One timestamp, two jobs:
--   * presence  => a SearchBug attempt was made (phone-found OR no-record)
--                  => suppress from To Enrich (good_leads_now), so we never
--                     re-spend on a dead lookup.
--   * value     => for phone-found rows, the enrichment time that anchors
--                  To Fire's 7-day "phone is fresh" window.
-- Set on every enrichment write (see services/dedup_service.py). Mirrors the
-- existing ists_judgments.enriched_at semantics so both tracks behave alike.
--
-- Additive + nullable + IF NOT EXISTS — safe to apply on a live system.

BEGIN;

ALTER TABLE lead_contacts
    ADD COLUMN IF NOT EXISTS enriched_at TIMESTAMPTZ;

-- To Fire orders by recency of enrichment within track 'ng'.
CREATE INDEX IF NOT EXISTS idx_lead_contacts_enriched_at
    ON lead_contacts (track, enriched_at)
    WHERE enriched_at IS NOT NULL;

-- good_leads_now = "to enrich right now":
--   static enrichable flag + court-not-passed + NOT already enriched/attempted.
-- The 14-day filing-freshness cutoff is applied by the caller (build_to_enrich),
-- consistent with the existing "freshness left to the caller" convention.
CREATE OR REPLACE VIEW good_leads_now AS
SELECT f.*
FROM filings f
WHERE f.is_enrichable = TRUE
  AND (f.court_date IS NULL OR f.court_date >= CURRENT_DATE)
  AND NOT EXISTS (
        SELECT 1 FROM lead_contacts lc
        WHERE lc.case_number = f.case_number
          AND lc.track = 'ng'
          AND (lc.phone IS NOT NULL OR lc.enriched_at IS NOT NULL)
  );

COMMIT;
