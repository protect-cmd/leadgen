-- 017_lead_quality.sql
--
-- Persists the STATIC half of the enrichment gates so a "good, enrichable lead"
-- is a single query instead of ~40 lines of re-derived gate logic per script.
--
--   filings.is_enrichable = residential_approved
--                           AND clean person name (gate_name)
--                           AND valid address (gate_address)
--
-- These inputs are immutable after ingest, so the flag never goes stale.
-- The TIME-VARYING gates (filing freshness, court date, not-yet-phoned) are NOT
-- stored — they live in the good_leads_now view (freshness left to the caller).
--
-- Additive + nullable + IF NOT EXISTS — safe to apply on a live system.

BEGIN;

ALTER TABLE filings
    ADD COLUMN IF NOT EXISTS is_enrichable BOOLEAN;

ALTER TABLE filings
    ADD COLUMN IF NOT EXISTS enrichable_checked_at TIMESTAMPTZ;

-- Partial index: selection only ever filters on TRUE rows.
CREATE INDEX IF NOT EXISTS idx_filings_enrichable
    ON filings (is_enrichable) WHERE is_enrichable;

-- Live "callable right now" list: static flag + court-not-passed + not-yet-phoned.
-- Freshness (filing_date >= today-N) is applied by the caller, since N varies.
CREATE OR REPLACE VIEW good_leads_now AS
SELECT f.*
FROM filings f
WHERE f.is_enrichable = TRUE
  AND (f.court_date IS NULL OR f.court_date >= CURRENT_DATE)
  AND NOT EXISTS (
        SELECT 1 FROM lead_contacts lc
        WHERE lc.case_number = f.case_number
          AND lc.track = 'ng'
          AND lc.phone IS NOT NULL
  );

COMMIT;
