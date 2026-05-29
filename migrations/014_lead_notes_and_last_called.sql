-- 014_lead_notes_and_last_called.sql
--
-- Adds support for the unified caller-lookup dashboard (Spec 4):
--   1. lead_notes — append-only timestamped notes per (case_number, track)
--   2. lead_contacts.last_called_at — single timestamp for the most-recent
--      outbound call, shown in the detail panel as "Last called: 2h ago"
--
-- Both additions are nullable / append-only — safe to apply on a live system.

BEGIN;

CREATE TABLE IF NOT EXISTS lead_notes (
    id           BIGSERIAL PRIMARY KEY,
    case_number  TEXT NOT NULL,
    track        TEXT NOT NULL,
    note_text    TEXT NOT NULL,
    author       TEXT NOT NULL DEFAULT 'caller',
    created_at   TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS lead_notes_case_track_idx
    ON lead_notes (case_number, track);

CREATE INDEX IF NOT EXISTS lead_notes_created_at_idx
    ON lead_notes (created_at DESC);

ALTER TABLE lead_contacts
    ADD COLUMN IF NOT EXISTS last_called_at TIMESTAMPTZ;

COMMIT;
