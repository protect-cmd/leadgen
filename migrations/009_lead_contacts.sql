CREATE TABLE IF NOT EXISTS lead_contacts (
    id                  UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    case_number         TEXT NOT NULL REFERENCES filings(case_number) ON DELETE CASCADE,
    track               TEXT NOT NULL CHECK (track IN ('ec', 'ng')),
    contact_name        TEXT NOT NULL,
    phone               TEXT,
    email               TEXT,
    secondary_address   TEXT,
    estimated_rent      NUMERIC(12,2),
    property_type       TEXT,
    dnc_status          TEXT NOT NULL DEFAULT 'unknown',
    dnc_source          TEXT,
    dnc_checked_at      TIMESTAMPTZ,
    language_hint       TEXT,
    enrichment_source   TEXT,
    ghl_contact_id      TEXT,
    bland_status        TEXT NOT NULL DEFAULT 'pending',
    bland_call_id       TEXT,
    created_at          TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at          TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE (case_number, track)
);

ALTER TABLE lead_contacts ENABLE ROW LEVEL SECURITY;

CREATE INDEX IF NOT EXISTS idx_lead_contacts_case_track
    ON lead_contacts (case_number, track);

CREATE INDEX IF NOT EXISTS idx_lead_contacts_pending
    ON lead_contacts (track, bland_status, ghl_contact_id)
    WHERE ghl_contact_id IS NOT NULL;

CREATE INDEX IF NOT EXISTS idx_lead_contacts_dnc
    ON lead_contacts (track, dnc_status, bland_status);
