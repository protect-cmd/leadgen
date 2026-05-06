ALTER TABLE filings
    ADD COLUMN IF NOT EXISTS dnc_override_source TEXT,
    ADD COLUMN IF NOT EXISTS dnc_override_notes TEXT,
    ADD COLUMN IF NOT EXISTS dnc_override_at TIMESTAMPTZ;

CREATE INDEX IF NOT EXISTS idx_filings_dnc_override_at
    ON filings (dnc_override_at);
