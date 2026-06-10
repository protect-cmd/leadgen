-- 016_ists_outreach.sql
-- Sub-project B: adds enrichment + outreach tracking columns to ists_judgments.
-- All columns nullable so existing rows are unaffected.
ALTER TABLE ists_judgments
    ADD COLUMN IF NOT EXISTS phone              TEXT,
    ADD COLUMN IF NOT EXISTS language_hint      TEXT,
    ADD COLUMN IF NOT EXISTS ghl_contact_id     TEXT,
    ADD COLUMN IF NOT EXISTS bland_call_id      TEXT,
    ADD COLUMN IF NOT EXISTS enriched_at        TIMESTAMPTZ,
    ADD COLUMN IF NOT EXISTS ghl_pushed_at      TIMESTAMPTZ,
    ADD COLUMN IF NOT EXISTS bland_triggered_at TIMESTAMPTZ;

CREATE INDEX IF NOT EXISTS idx_ists_judgments_phone
    ON ists_judgments (phone)
    WHERE phone IS NOT NULL;
