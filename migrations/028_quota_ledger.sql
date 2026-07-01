-- Phase 5: atomic per-business spend quota (PLAN.md).
-- Replaces the SQLite increment-only daily cap with a Postgres-backed,
-- per-business, reserve/commit/rollback quota that every paid path
-- (SearchBug, GHL, Bland) checks BEFORE acting. Additive: this table +
-- function are referenced by nothing existing, so applying it is a no-op for
-- current flows until services/quota_service.py is wired in.

CREATE TABLE IF NOT EXISTS quota_ledger (
    id          bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    business    text NOT NULL,                 -- 'vantage'|'ists'|'cosner'|'garnish_proof'
    action      text NOT NULL,                 -- 'searchbug'|'ghl'|'bland'
    lead_key    text NOT NULL,                 -- business-scoped lead id (case_number)
    day         date NOT NULL,
    status      text NOT NULL DEFAULT 'reserved'
                CHECK (status IN ('reserved', 'committed', 'rolled_back')),
    created_at  timestamptz NOT NULL DEFAULT now(),
    updated_at  timestamptz NOT NULL DEFAULT now()
);

-- Idempotency: at most one ACTIVE (reserved/committed) row per
-- (business, action, lead_key, day). A rolled_back row frees the slot, so a
-- retry after rollback can re-reserve. This makes try_reserve safe to replay.
CREATE UNIQUE INDEX IF NOT EXISTS quota_ledger_idem
    ON quota_ledger (business, action, lead_key, day)
    WHERE status IN ('reserved', 'committed');

-- Bucket counting index (the cap is per business+action+day).
CREATE INDEX IF NOT EXISTS quota_ledger_bucket
    ON quota_ledger (business, action, day, status);


-- Atomic reservation. Returns (granted, used, remaining).
--   * Idempotent: re-reserving an already-active lead returns granted=true
--     WITHOUT incrementing the count (no double-spend on retries/replays).
--   * Concurrency-safe: a per-bucket transaction advisory lock serializes the
--     count-then-insert so two parallel callers cannot both slip past the cap.
CREATE OR REPLACE FUNCTION quota_try_reserve(
    p_business text,
    p_action   text,
    p_lead_key text,
    p_day      date,
    p_cap      int
) RETURNS TABLE(granted boolean, used int, remaining int)
LANGUAGE plpgsql AS $$
DECLARE
    v_used int;
BEGIN
    -- Already reserved/committed for this exact lead today => idempotent grant.
    IF EXISTS (
        SELECT 1 FROM quota_ledger
        WHERE business = p_business AND action = p_action
          AND lead_key = p_lead_key AND day = p_day
          AND status IN ('reserved', 'committed')
    ) THEN
        SELECT count(*) INTO v_used FROM quota_ledger
        WHERE business = p_business AND action = p_action AND day = p_day
          AND status IN ('reserved', 'committed');
        RETURN QUERY SELECT true, v_used, greatest(p_cap - v_used, 0);
        RETURN;
    END IF;

    -- Serialize concurrent reservations for this (business, action, day) bucket.
    PERFORM pg_advisory_xact_lock(
        hashtextextended(p_business || '|' || p_action || '|' || p_day::text, 0)
    );

    SELECT count(*) INTO v_used FROM quota_ledger
    WHERE business = p_business AND action = p_action AND day = p_day
      AND status IN ('reserved', 'committed');

    IF v_used >= p_cap THEN
        RETURN QUERY SELECT false, v_used, 0;
        RETURN;
    END IF;

    INSERT INTO quota_ledger (business, action, lead_key, day, status)
    VALUES (p_business, p_action, p_lead_key, p_day, 'reserved');

    RETURN QUERY SELECT true, v_used + 1, greatest(p_cap - (v_used + 1), 0);
END;
$$;
