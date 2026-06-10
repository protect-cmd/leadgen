-- 019_dnc_status.sql
--
-- Persist the DNC verdict as a property of the enriched number, determined ONCE
-- at enrich-time (right after SearchBug returns the phone) via DNCScrub. The
-- To-Fire list then shows only dnc_status='callable' — DNC numbers never surface.
--
--   dnc_status     : 'callable' | 'dnc' | 'unknown'   (services.dnc_service.verdict)
--   dnc_checked_at : when it was scrubbed (TCPA record — scrub recency matters)
--
-- Additive + nullable + IF NOT EXISTS — safe on a live system.

BEGIN;

ALTER TABLE lead_contacts
    ADD COLUMN IF NOT EXISTS dnc_status TEXT;
ALTER TABLE lead_contacts
    ADD COLUMN IF NOT EXISTS dnc_checked_at TIMESTAMPTZ;

ALTER TABLE ists_judgments
    ADD COLUMN IF NOT EXISTS dnc_status TEXT;
ALTER TABLE ists_judgments
    ADD COLUMN IF NOT EXISTS dnc_checked_at TIMESTAMPTZ;

-- To-Fire filters on callable; partial index keeps that fast.
CREATE INDEX IF NOT EXISTS idx_lead_contacts_dnc_callable
    ON lead_contacts (dnc_status) WHERE dnc_status = 'callable';

COMMIT;
