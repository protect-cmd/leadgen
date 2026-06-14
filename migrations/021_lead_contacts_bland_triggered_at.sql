-- migrations/021_lead_contacts_bland_triggered_at.sql
-- Per-fire timestamp so the ops dashboard can chart fired/day for the NG track.
-- Additive + nullable + IF NOT EXISTS — safe to apply live, any time.
ALTER TABLE lead_contacts
    ADD COLUMN IF NOT EXISTS bland_triggered_at TIMESTAMPTZ;
