CREATE TABLE IF NOT EXISTS filings (
    case_number        TEXT PRIMARY KEY,
    tenant_name        TEXT NOT NULL,
    property_address   TEXT NOT NULL,
    landlord_name      TEXT NOT NULL,
    filing_date        DATE NOT NULL,
    court_date         DATE,
    state              TEXT NOT NULL,
    county             TEXT NOT NULL,
    notice_type        TEXT NOT NULL,
    source_url         TEXT NOT NULL,
    scraped_at         TIMESTAMPTZ DEFAULT NOW(),
    enriched           BOOLEAN DEFAULT FALSE,
    enriched_at        TIMESTAMPTZ,
    routed             BOOLEAN DEFAULT FALSE,
    routing_outcome    TEXT,
    ghl_contact_id     TEXT,
    bland_triggered    BOOLEAN DEFAULT FALSE
);

CREATE TABLE IF NOT EXISTS batchdata_cost_log (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    case_number     TEXT NOT NULL,
    called_at       TIMESTAMPTZ DEFAULT NOW(),
    cost_usd        NUMERIC(10,4) DEFAULT 0.07,
    phone_returned  BOOLEAN,
    email_returned  BOOLEAN
);
