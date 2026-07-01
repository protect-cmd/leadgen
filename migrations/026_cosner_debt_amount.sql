-- 026_cosner_debt_amount.sql
-- Cosner Drake debt amount capture from Harris JP "Cases Filed / Debt Claim"
-- extract. Amount kind distinguishes debt totals from rent/judgment amounts.
ALTER TABLE cosner_filings
    ADD COLUMN IF NOT EXISTS debt_amount NUMERIC,
    ADD COLUMN IF NOT EXISTS amount_kind TEXT;
