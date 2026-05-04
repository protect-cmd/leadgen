ALTER TABLE filings
    ADD COLUMN IF NOT EXISTS bland_status TEXT NOT NULL DEFAULT 'pending',
    ADD COLUMN IF NOT EXISTS bland_call_id TEXT,
    ADD COLUMN IF NOT EXISTS ng_bland_status TEXT NOT NULL DEFAULT 'pending',
    ADD COLUMN IF NOT EXISTS ng_bland_call_id TEXT;

-- Index for dashboard query: all EC leads awaiting approval
CREATE INDEX IF NOT EXISTS idx_filings_bland_status ON filings (bland_status, ghl_contact_id);
