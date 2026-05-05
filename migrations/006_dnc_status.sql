ALTER TABLE filings
    ADD COLUMN IF NOT EXISTS dnc_status TEXT NOT NULL DEFAULT 'unknown',
    ADD COLUMN IF NOT EXISTS dnc_checked_at TIMESTAMPTZ,
    ADD COLUMN IF NOT EXISTS dnc_source TEXT,
    ADD COLUMN IF NOT EXISTS ng_dnc_status TEXT NOT NULL DEFAULT 'unknown',
    ADD COLUMN IF NOT EXISTS ng_dnc_checked_at TIMESTAMPTZ,
    ADD COLUMN IF NOT EXISTS ng_dnc_source TEXT;

CREATE INDEX IF NOT EXISTS idx_filings_dnc_status ON filings (dnc_status, bland_status);
CREATE INDEX IF NOT EXISTS idx_filings_ng_dnc_status ON filings (ng_dnc_status, ng_bland_status);
