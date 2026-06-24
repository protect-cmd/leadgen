-- 025_cosner_filings.sql
-- Cosner Drake vertical. Physically isolated from filings/lead_contacts and from
-- garnishment_orders. The daily eviction scheduler never reads this table.
-- Source: Harris JP "Cases Filed / Debt Claim" extract (pre-judgment lawsuits).
-- The defendant (consumer just sued by a debt collector) is the lead.
CREATE TABLE IF NOT EXISTS cosner_filings (
    case_number        TEXT PRIMARY KEY,
    defendant_name     TEXT NOT NULL,            -- the person sued (the lead)
    defendant_address  TEXT NOT NULL,            -- defendant HOME address
    creditor_name      TEXT,                     -- plaintiff / debt buyer
    state              TEXT NOT NULL DEFAULT 'TX',
    county             TEXT NOT NULL DEFAULT 'Harris',
    filing_date        DATE,
    answer_deadline    DATE,                     -- filing_date + 30d Answer window
    phone              TEXT,
    language_hint      TEXT,
    enriched_at        TIMESTAMPTZ,
    source_url         TEXT,
    selected_at        TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    confirmed_at       TIMESTAMPTZ,
    -- outreach tracking (reuses the ISTS/GP enrich -> GHL -> Bland pipeline shape)
    ghl_contact_id     TEXT,
    ghl_pushed_at      TIMESTAMPTZ,
    bland_call_id      TEXT,
    bland_triggered_at TIMESTAMPTZ
);

CREATE INDEX IF NOT EXISTS idx_cosner_filings_filing_date
    ON cosner_filings (filing_date);
