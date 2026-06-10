-- 020_ists_rent.sql - market rent estimate on judgments for scoring.
BEGIN;
ALTER TABLE ists_judgments ADD COLUMN IF NOT EXISTS estimated_rent NUMERIC;
COMMIT;
