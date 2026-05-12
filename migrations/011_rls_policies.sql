ALTER TABLE public.filings ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.lead_contacts ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.batchdata_cost_log ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.run_metrics ENABLE ROW LEVEL SECURITY;

REVOKE ALL ON TABLE public.filings FROM anon, authenticated;
REVOKE ALL ON TABLE public.lead_contacts FROM anon, authenticated;
REVOKE ALL ON TABLE public.batchdata_cost_log FROM anon, authenticated;
REVOKE ALL ON TABLE public.run_metrics FROM anon, authenticated;

GRANT SELECT, INSERT, UPDATE, DELETE ON TABLE public.filings TO service_role;
GRANT SELECT, INSERT, UPDATE, DELETE ON TABLE public.lead_contacts TO service_role;
GRANT SELECT, INSERT, UPDATE, DELETE ON TABLE public.batchdata_cost_log TO service_role;
GRANT SELECT, INSERT, UPDATE, DELETE ON TABLE public.run_metrics TO service_role;
GRANT USAGE, SELECT ON SEQUENCE public.run_metrics_id_seq TO service_role;

DROP POLICY IF EXISTS "service_role_all_filings" ON public.filings;
CREATE POLICY "service_role_all_filings"
ON public.filings
FOR ALL
TO service_role
USING (true)
WITH CHECK (true);

DROP POLICY IF EXISTS "service_role_all_lead_contacts" ON public.lead_contacts;
CREATE POLICY "service_role_all_lead_contacts"
ON public.lead_contacts
FOR ALL
TO service_role
USING (true)
WITH CHECK (true);

DROP POLICY IF EXISTS "service_role_all_batchdata_cost_log" ON public.batchdata_cost_log;
CREATE POLICY "service_role_all_batchdata_cost_log"
ON public.batchdata_cost_log
FOR ALL
TO service_role
USING (true)
WITH CHECK (true);

DROP POLICY IF EXISTS "service_role_all_run_metrics" ON public.run_metrics;
CREATE POLICY "service_role_all_run_metrics"
ON public.run_metrics
FOR ALL
TO service_role
USING (true)
WITH CHECK (true);
