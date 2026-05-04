CREATE TABLE IF NOT EXISTS run_metrics (
    id                  BIGSERIAL PRIMARY KEY,
    run_at              TIMESTAMPTZ NOT NULL,
    state               TEXT,
    county              TEXT,
    filings_received    INTEGER NOT NULL DEFAULT 0,
    duplicates_skipped  INTEGER NOT NULL DEFAULT 0,
    address_skipped     INTEGER NOT NULL DEFAULT 0,
    batchdata_calls     INTEGER NOT NULL DEFAULT 0,
    phones_found        INTEGER NOT NULL DEFAULT 0,
    ghl_created         INTEGER NOT NULL DEFAULT 0,
    bland_triggered     INTEGER NOT NULL DEFAULT 0,
    elapsed_seconds     NUMERIC(8, 2)
);
