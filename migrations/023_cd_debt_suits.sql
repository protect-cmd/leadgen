-- 023_cd_debt_suits.sql
-- Cosner Drake (CD) — debt-collection lawsuits just FILED, statewide Indiana
-- MyCase. Physically isolated from the prod filings/lead_contacts tables (same
-- pattern as ists_judgments). The eviction daily scheduler never reads this.
-- The target lead is the sued consumer (defendant), never the plaintiff/creditor.
CREATE TABLE IF NOT EXISTS cd_debt_suits (
    case_number        TEXT PRIMARY KEY,
    defendant_name     TEXT NOT NULL,          -- the lead (sued consumer)
    defendant_address  TEXT NOT NULL,          -- full home street address (gate_address-ready)
    plaintiff_name     TEXT,                   -- creditor — NEVER the target
    filing_date        DATE,
    case_type_code     TEXT NOT NULL DEFAULT 'CC',  -- CC = Civil Collection
    county             TEXT NOT NULL DEFAULT '',
    state              TEXT NOT NULL DEFAULT 'IN',
    court_code         TEXT,
    -- amount sued for is not a structured MyCase field (only the ~$157 filing
    -- fee is exposed); left NULL until complaint-PDF parsing is added.
    amount             NUMERIC,
    amount_kind        TEXT,                   -- 'debt_claim_total' when amount is populated
    case_status        TEXT,                   -- carried so Garnish Proof can later filter judgments
    source_url         TEXT,
    scraped_at         TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_cd_debt_suits_filing_date
    ON cd_debt_suits (filing_date);
CREATE INDEX IF NOT EXISTS idx_cd_debt_suits_county
    ON cd_debt_suits (county);

-- RLS: service_role only (mirrors 011_rls_policies.sql).
ALTER TABLE public.cd_debt_suits ENABLE ROW LEVEL SECURITY;
REVOKE ALL ON TABLE public.cd_debt_suits FROM anon, authenticated;
GRANT SELECT, INSERT, UPDATE, DELETE ON TABLE public.cd_debt_suits TO service_role;

DROP POLICY IF EXISTS "service_role_all_cd_debt_suits" ON public.cd_debt_suits;
CREATE POLICY "service_role_all_cd_debt_suits"
ON public.cd_debt_suits
FOR ALL
TO service_role
USING (true)
WITH CHECK (true);
