ALTER TABLE filings
    ADD COLUMN IF NOT EXISTS language_hint TEXT;

CREATE INDEX IF NOT EXISTS idx_filings_language_hint_bucket
    ON filings (language_hint, lead_bucket);
