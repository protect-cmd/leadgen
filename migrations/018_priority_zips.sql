-- 018_priority_zips.sql
--
-- Priority-ZIP queue: high-rent ZIPs by county, ranked in morning-queue order,
-- used by BOTH Vantage (good_leads_now) and ISTS (good_judgments_now, Phase 3).
-- These are a PRIORITY signal, not a gate — nothing is excluded, just ordered.
--
--   queue order: Harris(1) Tarrant(2) Davidson(3) Franklin(4) Hamilton(5)
--
-- Also adds filings.property_zip (backfilled from property_address by
-- scripts/backfill_property_zip.py) so the view can join priority_zips cleanly.
-- Additive + IF NOT EXISTS — safe on a live system.

BEGIN;

CREATE TABLE IF NOT EXISTS priority_zips (
    zip         TEXT PRIMARY KEY,
    state       TEXT NOT NULL,
    county      TEXT NOT NULL,
    metro       TEXT NOT NULL,
    queue_rank  INT  NOT NULL
);

INSERT INTO priority_zips (zip, state, county, metro, queue_rank) VALUES
    -- 1) Harris / Houston  ($1,800–3,500)
    ('77005','TX','Harris','Houston',1),
    ('77019','TX','Harris','Houston',1),
    ('77024','TX','Harris','Houston',1),
    ('77027','TX','Harris','Houston',1),
    ('77056','TX','Harris','Houston',1),
    ('77057','TX','Harris','Houston',1),
    -- 2) Tarrant / Fort Worth  ($1,400–1,900)  [scraper + 817/682 DNC pending]
    ('76109','TX','Tarrant','Fort Worth',2),
    ('76107','TX','Tarrant','Fort Worth',2),
    ('76132','TX','Tarrant','Fort Worth',2),
    ('76116','TX','Tarrant','Fort Worth',2),
    -- 3) Davidson / Nashville  ($1,600–2,800)
    ('37215','TN','Davidson','Nashville',3),
    ('37205','TN','Davidson','Nashville',3),
    ('37209','TN','Davidson','Nashville',3),
    ('37212','TN','Davidson','Nashville',3),
    -- 4) Franklin / Columbus  ($1,400–2,200)
    ('43221','OH','Franklin','Columbus',4),
    ('43235','OH','Franklin','Columbus',4),
    ('43054','OH','Franklin','Columbus',4),
    ('43082','OH','Franklin','Columbus',4),
    -- 5) Hamilton / Cincinnati  ($1,400–2,000)  [scraper unblock pending]
    ('45208','OH','Hamilton','Cincinnati',5),
    ('45209','OH','Hamilton','Cincinnati',5),
    ('45243','OH','Hamilton','Cincinnati',5),
    ('45202','OH','Hamilton','Cincinnati',5)
ON CONFLICT (zip) DO UPDATE
    SET state=EXCLUDED.state, county=EXCLUDED.county,
        metro=EXCLUDED.metro, queue_rank=EXCLUDED.queue_rank;

ALTER TABLE filings
    ADD COLUMN IF NOT EXISTS property_zip TEXT;

CREATE INDEX IF NOT EXISTS idx_filings_property_zip ON filings (property_zip);

-- Rebuild good_leads_now to expose priority_rank (NULL = not a priority ZIP)
-- and enforce the Vantage freshness GATE.
--
-- Freshness is now a single, centralized gate here (was scattered: a 7-day
-- classification 'held' cutoff + ad-hoc 6/14-day query filters):
--   * court_date >= today        -> hard "still actionable" gate
--   * filing_date >= today - 21d -> ceiling so SearchBug never burns on aging filings
-- A just-served tenant is most receptive, so callers order freshest-first:
--   ORDER BY priority_rank NULLS LAST, filing_date DESC
CREATE OR REPLACE VIEW good_leads_now AS
SELECT f.*, pz.queue_rank AS priority_rank, pz.metro AS priority_metro
FROM filings f
LEFT JOIN priority_zips pz ON pz.zip = f.property_zip
WHERE f.is_enrichable = TRUE
  AND (f.court_date IS NULL OR f.court_date >= CURRENT_DATE)
  AND f.filing_date >= CURRENT_DATE - INTERVAL '21 days'
  AND NOT EXISTS (
        SELECT 1 FROM lead_contacts lc
        WHERE lc.case_number = f.case_number
          AND lc.track = 'ng'
          AND lc.phone IS NOT NULL
  );

COMMIT;
