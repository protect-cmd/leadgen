-- 012_drop_dnc.sql — remove DNC scrubbing entirely.
-- One-way migration. No down-migration provided.
-- Deliberate policy decision per:
-- docs/superpowers/specs/2026-05-28-tenant-lead-volume-overhaul-design.md

BEGIN;

ALTER TABLE filings
    DROP COLUMN IF EXISTS dnc_status,
    DROP COLUMN IF EXISTS dnc_source,
    DROP COLUMN IF EXISTS dnc_checked_at,
    DROP COLUMN IF EXISTS ng_dnc_status,
    DROP COLUMN IF EXISTS ng_dnc_source,
    DROP COLUMN IF EXISTS ng_dnc_checked_at,
    DROP COLUMN IF EXISTS dnc_override_source,
    DROP COLUMN IF EXISTS dnc_override_notes,
    DROP COLUMN IF EXISTS dnc_override_at;

ALTER TABLE lead_contacts
    DROP COLUMN IF EXISTS dnc_status,
    DROP COLUMN IF EXISTS dnc_source,
    DROP COLUMN IF EXISTS dnc_checked_at;

DROP TABLE IF EXISTS dnc_override_audit;

COMMIT;
