-- 013_searchbug_status_and_run_metrics.sql
--
-- 1. Adds searchbug_status / searchbug_returned_name to lead_contacts so
--    review-stage routing decisions are auditable after the fact (which
--    leads were name_mismatch vs ambiguous vs no_records vs phone_found).
-- 2. Adds the run_metrics columns introduced over 2026-05-{20..28}:
--    captured, gate_* (9-gate filter), gate_llm_recovered (new LLM
--    recovery layer), ng_phones_pushed / ng_review_pushed (review-stage
--    routing), searchbug_calls / searchbug_daily_total (cost telemetry).
--
-- These columns were being silently dropped by dedup_service's
-- column-discovery cache. Apply this migration so tomorrow's cron run
-- persists complete telemetry.
--
-- All changes are additive — safe to apply on a live system. No data
-- backfill needed; existing rows just keep NULL for the new columns.

BEGIN;

-- lead_contacts: SearchBug response provenance for review-lane routing
ALTER TABLE lead_contacts
    ADD COLUMN IF NOT EXISTS searchbug_status TEXT,
    ADD COLUMN IF NOT EXISTS searchbug_returned_name TEXT;

-- run_metrics: per-run telemetry columns added since 003_run_metrics.sql
ALTER TABLE run_metrics
    ADD COLUMN IF NOT EXISTS captured                INTEGER NOT NULL DEFAULT 0,
    ADD COLUMN IF NOT EXISTS gate_out_of_window      INTEGER NOT NULL DEFAULT 0,
    ADD COLUMN IF NOT EXISTS gate_overdue            INTEGER NOT NULL DEFAULT 0,
    ADD COLUMN IF NOT EXISTS gate_invalid_address    INTEGER NOT NULL DEFAULT 0,
    ADD COLUMN IF NOT EXISTS gate_bad_name           INTEGER NOT NULL DEFAULT 0,
    ADD COLUMN IF NOT EXISTS gate_existing_phone     INTEGER NOT NULL DEFAULT 0,
    ADD COLUMN IF NOT EXISTS gate_duplicate_in_run   INTEGER NOT NULL DEFAULT 0,
    ADD COLUMN IF NOT EXISTS gate_llm_recovered      INTEGER NOT NULL DEFAULT 0,
    ADD COLUMN IF NOT EXISTS ng_phones_pushed        INTEGER NOT NULL DEFAULT 0,
    ADD COLUMN IF NOT EXISTS ng_review_pushed        INTEGER NOT NULL DEFAULT 0,
    ADD COLUMN IF NOT EXISTS searchbug_calls         INTEGER NOT NULL DEFAULT 0,
    ADD COLUMN IF NOT EXISTS searchbug_daily_total   INTEGER NOT NULL DEFAULT 0;

COMMIT;
