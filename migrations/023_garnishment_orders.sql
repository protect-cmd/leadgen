-- 023_garnishment_orders.sql
-- Garnish Proof vertical. Physically isolated from filings/lead_contacts.
-- The daily scheduler never reads this table. FL wage garnishment, debtor = lead.
CREATE TABLE IF NOT EXISTS garnishment_orders (
    case_number        TEXT PRIMARY KEY,
    debtor_name        TEXT NOT NULL,            -- the worker (the lead)
    debtor_address     TEXT NOT NULL,            -- debtor HOME address, never the garnishee's
    creditor_name      TEXT,                     -- plaintiff
    garnishee_name     TEXT,                     -- employer/bank; never the contact
    state              TEXT NOT NULL DEFAULT 'FL',
    county             TEXT NOT NULL DEFAULT 'Miami-Dade',
    filing_date        DATE,
    garnishment_type   TEXT NOT NULL DEFAULT 'wage',  -- 'wage' only is actionable
    exemption_deadline DATE,                     -- filing_date + claim-of-exemption window
    phone              TEXT,
    language_hint      TEXT,
    enriched_at        TIMESTAMPTZ,
    source_url         TEXT,
    selected_at        TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    confirmed_at       TIMESTAMPTZ
);

CREATE INDEX IF NOT EXISTS idx_garnishment_orders_filing_date
    ON garnishment_orders (filing_date);
