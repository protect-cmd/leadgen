-- 024_garnishment_orders_outreach.sql
-- Adds the outreach-tracking columns to garnishment_orders so the Garnish Proof
-- vertical can reuse the ISTS enrich -> GHL -> Bland pipeline shape.
ALTER TABLE garnishment_orders
    ADD COLUMN IF NOT EXISTS ghl_contact_id     TEXT,
    ADD COLUMN IF NOT EXISTS ghl_pushed_at       TIMESTAMPTZ,
    ADD COLUMN IF NOT EXISTS bland_call_id       TEXT,
    ADD COLUMN IF NOT EXISTS bland_triggered_at  TIMESTAMPTZ;
