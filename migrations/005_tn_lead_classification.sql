CREATE TABLE IF NOT EXISTS filings_backup_20260505_tn_remediation AS
SELECT *
FROM filings;

ALTER TABLE filings
    ADD COLUMN IF NOT EXISTS property_zip TEXT,
    ADD COLUMN IF NOT EXISTS lead_bucket TEXT,
    ADD COLUMN IF NOT EXISTS discard_reason TEXT,
    ADD COLUMN IF NOT EXISTS qualification_notes TEXT,
    ADD COLUMN IF NOT EXISTS classified_at TIMESTAMPTZ;

CREATE INDEX IF NOT EXISTS idx_filings_lead_bucket ON filings (lead_bucket);
CREATE INDEX IF NOT EXISTS idx_filings_state_county_bucket ON filings (state, county, lead_bucket);
CREATE INDEX IF NOT EXISTS idx_filings_property_zip ON filings (property_zip);
