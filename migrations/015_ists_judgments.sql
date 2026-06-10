-- 015_ists_judgments.sql
-- ISTS ("I Stopped The Sheriff") sub-project A. Physically isolated from the
-- prod filings/lead_contacts tables. The daily scheduler never reads this table.
CREATE TABLE IF NOT EXISTS ists_judgments (
    case_number          TEXT PRIMARY KEY,
    defendant_name       TEXT NOT NULL,
    property_address     TEXT NOT NULL,          -- full street address (gate_address passed)
    plaintiff_name       TEXT,
    state                TEXT NOT NULL DEFAULT 'TX',
    county               TEXT NOT NULL DEFAULT 'Harris',
    judgment_date        DATE,
    judgment_in_favor_of TEXT,
    judgment_against     TEXT,
    disposition_desc     TEXT,
    disposition_date     DATE,
    window_tag           TEXT NOT NULL DEFAULT 'W1',  -- 'window' is a reserved word in Postgres
    prior_phone          BOOLEAN NOT NULL DEFAULT FALSE,
    prior_bland_status   TEXT,
    source_url           TEXT,
    selected_at          TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    confirmed_at         TIMESTAMPTZ
);

CREATE INDEX IF NOT EXISTS idx_ists_judgments_judgment_date
    ON ists_judgments (judgment_date);
