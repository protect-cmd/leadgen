-- Add NG-track columns to the filings table.
-- Run this once in the Supabase SQL Editor:
-- https://supabase.com/dashboard/project/ctdypakgsqupuqtmgxqz/sql

ALTER TABLE filings
  ADD COLUMN IF NOT EXISTS ng_ghl_contact_id TEXT,
  ADD COLUMN IF NOT EXISTS ng_bland_triggered BOOLEAN DEFAULT FALSE;
