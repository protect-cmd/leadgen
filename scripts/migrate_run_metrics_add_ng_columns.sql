-- Add NG-track + FTC + SearchBug metric columns to run_metrics.
-- Required by PR #5 (Pushover alerts + run summary stats). Without these
-- columns, every run summary write fails with PGRST204 and fires a
-- "Leadgen job error" Pushover alert.
--
-- Run via Supabase Dashboard → SQL Editor → New query → paste → Run.
-- Safe to run multiple times (uses IF NOT EXISTS).

ALTER TABLE run_metrics
  ADD COLUMN IF NOT EXISTS ftc_scrubs_upgraded   INTEGER DEFAULT 0,
  ADD COLUMN IF NOT EXISTS ng_phones_pushed      INTEGER DEFAULT 0,
  ADD COLUMN IF NOT EXISTS searchbug_calls       INTEGER DEFAULT 0,
  ADD COLUMN IF NOT EXISTS searchbug_daily_total INTEGER DEFAULT 0;
