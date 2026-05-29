# Unified Caller-Lookup Dashboard — Spec 4

**Date:** 2026-05-29
**Author:** Zee
**Status:** Draft (brainstormed 2026-05-29)
**Promoted from Spec 6 to Spec 4 on 2026-05-29**

## Problem

The current dashboard at `dashboard/index.html` is a multi-tab Lead Queue
segmented by brand (GRANT / VANTAGE), view (Residential / Commercial /
Held / Discarded), and filter chips. A caller on a live RingCentral call
who needs to look up a specific client has to:

1. Pick the right brand tab
2. Pick the right view tab
3. Type into the filter input
4. Scan the rows of the filtered queue

That's three filter decisions before a name lookup. With a real caller
mid-conversation, the friction is real. The user described the current
UI as "messy" and wants "one general tab where they can look up the
name, number, case number, and etc."

## Scope

In scope (V1):
- A new search-first landing page replacing the default `/` route
- Live typeahead across name, phone, case number, and address
- Unified results across both brands (GRANT and VANTAGE) — no brand
  badge required
- Click result -> slide-in detail panel with full info + 3 light actions
- Light actions: Mark Called, Add Note, Skip
- Click-to-copy phone (for pasting into RingCentral)
- Notes persist with timestamp; multiple notes per lead

Out of scope (future specs):
- Bland triggers from the dashboard (callers are humans on RingCentral)
- User auth / per-user note attribution (notes show "caller" until then)
- Click-to-call integration with RingCentral
- Keyboard shortcuts beyond the basics (Ctrl+K, arrow nav, Enter, Esc)
- A separate "recent activity" / "my queue" view
- Removing the old queue UI — it stays accessible at `/queue` for
  triage workflows

## The five components

### Component 1 — Routing change

- `GET /` -> returns the new `dashboard/search.html`
- `GET /queue` -> returns the existing `dashboard/index.html`
- All existing API endpoints (`/api/leads`, `/api/lead-counts`,
  `/api/metrics`, `/api/leads/{case}/approve|skip`) stay unchanged

Existing triage-by-queue workflows keep working without re-training.
Callers landing on `/` see the search-first experience immediately.

### Component 2 — Search endpoint

**`GET /api/search?q=<text>&limit=20`**

Matching rules (all OR'd, case-insensitive substring):

| Source field | Match strategy |
|--------------|----------------|
| `lead_contacts.contact_name` | ILIKE `%q%` |
| `filings.tenant_name` | ILIKE `%q%` |
| `lead_contacts.phone` | Caller input normalized to digits-only, ILIKE on digit-stripped phone |
| `filings.case_number`, `lead_contacts.case_number` | ILIKE `%q%` (handles partial IDs) |
| `filings.property_address` | ILIKE `%q%` (covers street, city, ZIP) |

Source tables joined: `lead_contacts` (track-scoped) LEFT JOIN `filings`
on `case_number`. Returning both the contact-side fields (phone, GHL
ID, last_called_at, etc.) and the filing-side fields (court_date,
filing_date, property_address, etc.) in one row per match.

Returns up to 20 matches ordered by `filing_date DESC`. No fuzzy / typo
matching in V1 — exact substring only.

Empty result: returns `[]`. Frontend renders an empty-state message.

Indexes: existing indexes on `case_number` and `phone` are sufficient
for V1. If `tenant_name` / `property_address` ILIKE scans show >100ms
latency in production, add Postgres trigram indexes
(`CREATE INDEX ... USING gin(... gin_trgm_ops)`) as a follow-up.

### Component 3 — Detail panel actions

Three endpoints, all wrapped in `services/dedup_service.py` async helpers
to match existing patterns:

**`POST /api/leads/{case_number}/mark-called?track=ng`**
- UPDATE `lead_contacts` SET `last_called_at = now()` WHERE case_number = ? AND track = ?
- Returns `{"status": "ok", "last_called_at": "<ISO>"}`
- No body required

**`POST /api/leads/{case_number}/note?track=ng`**
- Body: `{"text": "<note>"}`
- INSERT INTO `lead_notes` (case_number, track, note_text, author='caller')
- Returns the inserted row including `id`, `created_at`
- 400 if text is empty or >2000 chars

**`POST /api/leads/{case_number}/skip?track=ng`**
- Reuses the existing skip handler at `dashboard/main.py:178`
- No new code unless we discover the existing handler doesn't accept the
  track query parameter cleanly

### Component 4 — `lead_notes` table

Stores append-only call notes. New table because notes are multi-per-lead
and time-ordered for retrieval — a JSON column on `lead_contacts` would
make the "show note history" query awkward.

```sql
CREATE TABLE lead_notes (
    id           BIGSERIAL PRIMARY KEY,
    case_number  TEXT NOT NULL,
    track        TEXT NOT NULL,         -- 'ec' | 'ng'
    note_text    TEXT NOT NULL,
    author       TEXT NOT NULL DEFAULT 'caller',
    created_at   TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX lead_notes_case_track_idx ON lead_notes (case_number, track);
CREATE INDEX lead_notes_created_at_idx ON lead_notes (created_at DESC);
```

The `author` column is forward-compatible with a later per-user auth
spec — for now it always equals `'caller'`.

### Component 5 — `lead_contacts.last_called_at` column

Single timestamp on `lead_contacts` for the most-recent call. "Called"
is a state, not an event log — the detail panel just shows
"Last called: 2h ago" or "Never called". Per-call outcomes belong in
`lead_notes`.

```sql
ALTER TABLE lead_contacts
    ADD COLUMN IF NOT EXISTS last_called_at TIMESTAMPTZ;
```

## Frontend layout (`dashboard/search.html`)

Single vanilla-HTML page matching the existing `dashboard/index.html`
stack (no framework, system fonts, dark theme, ~400-500 lines).

Three zones top-to-bottom:

1. **Header bar** — logo + page title "Search" + link to `/queue`
2. **Search input** — always-focused on page load, full-width, with
   subtle spinner indicator on the right during in-flight queries
3. **Results / detail split** — two-column layout:
   - Left ~60%: result rows (compact card per match)
   - Right ~40%: detail panel (empty by default until a row is clicked)

**Result row content** (per the brainstorm Q5):

```
Maria Garcia                             (713) 555-0142
123 Main St, Houston, TX 77002           TX/Harris
261100230644 · court 2026-06-15
```

**Detail panel content** (per Q6):

```
Maria Garcia                                  TX/Harris
261100230644 · Filed 2026-05-29 · Court 2026-06-15

(713) 555-0142          [copies on click]
123 Main St, Houston, TX 77002

──────────────────────────────────────
Stage:        Auto-pushed (residential)
Last called:  2 hours ago
GHL link:     [Open in GHL ↗]

──────────────────────────────────────
[Mark Called]   [Add Note]   [Skip]

──────────────────────────────────────
Notes (3)
  · "VM left, will call back tomorrow"
    2026-05-29 14:32 · caller
  · "Wrong number, removed phone"
    2026-05-28 09:15 · caller
```

**GHL link visibility:** the `Open in GHL` button is only rendered when
`lead_contacts.ghl_contact_id` is non-null. The href is constructed
from the contact ID + the location ID for the track.

**Click-to-copy phone:** clicking the phone number calls
`navigator.clipboard.writeText(...)` and shows a brief "Copied" toast.

**Keyboard shortcuts (V1):**
- `Cmd/Ctrl+K` from anywhere -> focus search input
- `↑` / `↓` navigate result rows
- `Enter` -> open detail panel for focused row
- `Esc` -> close detail panel (or re-focus search if no panel open)

**Search debouncing:** 250ms after last keystroke. In-flight request is
aborted via `AbortController` when a new keystroke arrives.

**Loading behavior:** results don't clear during reload — caller sees
the previous result set until the new one returns. Prevents flicker.

## Deliverables

Code:
- `migrations/014_lead_notes_and_last_called.sql` — new table + column
- `services/dedup_service.py` — new async helpers `add_lead_note`,
  `mark_lead_called`, `list_lead_notes`
- `dashboard/main.py` — new routes `/` (search.html), `/queue` (index.html),
  `/api/search`, `/api/leads/{case}/note`, `/api/leads/{case}/mark-called`
- `dashboard/search.html` — new single-page UI (~400-500 lines)
- `tests/test_dashboard_search.py` — search endpoint behavior tests
- `tests/test_lead_notes.py` — note insertion + retrieval tests
- `tests/test_dashboard_search_html.py` — minimal smoke test that the
  page loads and serves the static file

Out of scope:
- Migration backfill — both new columns are nullable and default safely
- Removing or renaming the old `dashboard/index.html` — it's now at `/queue`
- Re-styling the queue UI — Spec 4 doesn't touch its visual design

## Success criteria

Spec 4 is done when:

1. A caller can open the dashboard root (`/`), type a tenant name, and
   see matches within 300ms of the last keystroke
2. Clicking a match opens a detail panel showing the lead's full info
   + a single-click "Mark Called" button + a "Copy" action on the phone
3. Adding a note persists it; refreshing the page shows the note in
   the panel's history
4. The old queue UI remains accessible at `/queue` with no regressions
5. Migration 014 applied, no other production effects
6. All new tests pass; the existing dashboard test suite still passes
