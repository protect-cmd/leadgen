-- 030_cd_gp_search_actions.sql
-- Cosner Drake / Garnish Proof leads are now searchable from the caller search
-- page (services/dedup_service.search_leads). The page's Mark Called / Skip
-- actions need somewhere to write for these tracks — cosner_filings and
-- garnishment_orders had neither column since callers never touched them
-- directly before.
ALTER TABLE cosner_filings
    ADD COLUMN IF NOT EXISTS last_called_at TIMESTAMPTZ,
    ADD COLUMN IF NOT EXISTS skipped_at      TIMESTAMPTZ;

ALTER TABLE garnishment_orders
    ADD COLUMN IF NOT EXISTS last_called_at TIMESTAMPTZ,
    ADD COLUMN IF NOT EXISTS skipped_at      TIMESTAMPTZ;
