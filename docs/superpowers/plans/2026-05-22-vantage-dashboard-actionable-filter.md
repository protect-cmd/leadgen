# Vantage Dashboard Actionable Filter Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Tighten the Vantage Residential dashboard view so it only shows actionable tenant leads (~14), and add a sibling "Already Called" tab (~66) for history. Eliminates the 541-row inflated count that includes leads already dialed by Bland or with no phone at all.

**Architecture:** Server-side filter applied in `services/dedup_service.py` against the pre-existing `lead_contacts` query that `_get_ng_dashboard_leads` already runs. Two pure-function predicates (`_is_ng_contact_actionable`, `_is_ng_contact_already_called`) make the filter testable in isolation. Dashboard counts (`get_dashboard_counts` + `_ng_counts_from_contact_rows`) updated to apply the same predicates so count tiles match table content. Frontend gets three small additions to `dashboard/index.html` configs — no new JS functions, no CSS.

**Tech Stack:** Python 3.13, FastAPI, Supabase (PostgREST), vanilla JS frontend, pytest.

**Reference:** [docs/superpowers/specs/2026-05-22-vantage-dashboard-actionable-filter-design.md](../specs/2026-05-22-vantage-dashboard-actionable-filter-design.md)

---

## File Structure

**Modify:**
- `services/dedup_service.py` — add predicates, filter pre-query in `_get_ng_dashboard_leads`, expand select in `get_dashboard_counts`, apply predicates in `_ng_counts_from_contact_rows`.
- `dashboard/index.html` — three config additions (1 array entry, 3 dict entries).

**Create:**
- `tests/test_ng_dashboard_filter.py` — new test file covering predicates, filter behavior, counts, and frontend config integrity.

**Lines of code affected:** ~50 LOC of production code, ~120 LOC of tests.

---

## Task 1: Add `NG_WORKED_BLAND_STATUSES` constant and predicate functions

**Files:**
- Modify: `services/dedup_service.py` (insert after line 532, just after `_track_for_dashboard_view`)
- Test: `tests/test_ng_dashboard_filter.py` (new)

- [ ] **Step 1: Create the test file with failing tests for the predicates**

Create `tests/test_ng_dashboard_filter.py`:

```python
"""Tests for the Vantage (NG) dashboard actionable filter and Already Called tab.

The main "Vantage Residential" view should only show leads the operator can
act on today — phone present, not yet dialed by Bland, not in compliance hold.
The new "Vantage Already Called" view shows leads where Bland already ran.
"""
from __future__ import annotations

from services import dedup_service


# ── predicate: _is_ng_contact_actionable ─────────────────────────────────────

def test_actionable_requires_phone():
    assert dedup_service._is_ng_contact_actionable(
        {"phone": "+15551112222", "bland_status": "pending"}
    ) is True
    assert dedup_service._is_ng_contact_actionable(
        {"phone": None, "bland_status": "pending"}
    ) is False
    assert dedup_service._is_ng_contact_actionable(
        {"phone": "", "bland_status": "pending"}
    ) is False


def test_actionable_excludes_worked_bland_statuses():
    for worked in ("triggered", "wrong_brand_review", "missing_contact_data", "blocked_dnc"):
        assert dedup_service._is_ng_contact_actionable(
            {"phone": "+15551112222", "bland_status": worked}
        ) is False, f"{worked} should be excluded"


def test_actionable_includes_visible_bland_statuses():
    for visible in ("pending", "pending_dnc_review", "skipped", None):
        assert dedup_service._is_ng_contact_actionable(
            {"phone": "+15551112222", "bland_status": visible}
        ) is True, f"{visible!r} should be visible"


def test_actionable_includes_both_dnc_clear_and_unknown():
    # Caller decides — both visible per operator preference
    for status in ("clear", "unknown"):
        assert dedup_service._is_ng_contact_actionable(
            {"phone": "+15551112222", "bland_status": "pending", "dnc_status": status}
        ) is True


# ── predicate: _is_ng_contact_already_called ────────────────────────────────

def test_already_called_matches_triggered_and_wrong_brand():
    assert dedup_service._is_ng_contact_already_called(
        {"bland_status": "triggered"}
    ) is True
    assert dedup_service._is_ng_contact_already_called(
        {"bland_status": "wrong_brand_review"}
    ) is True


def test_already_called_excludes_other_statuses():
    for not_called in (
        "pending", "pending_dnc_review", "skipped",
        "missing_contact_data", "blocked_dnc", None,
    ):
        assert dedup_service._is_ng_contact_already_called(
            {"bland_status": not_called}
        ) is False, f"{not_called!r} should not count as already-called"
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/test_ng_dashboard_filter.py -v`
Expected: AttributeError — `_is_ng_contact_actionable` and `_is_ng_contact_already_called` don't exist yet.

- [ ] **Step 3: Add the constant and predicate functions to `services/dedup_service.py`**

Insert after `_track_for_dashboard_view` (around line 533):

```python
# Bland statuses that indicate a tenant lead has already been worked. Such
# leads are hidden from the main "Vantage Residential" view and surface in
# the "Vantage Already Called" view instead.
NG_WORKED_BLAND_STATUSES: frozenset[str] = frozenset({
    "triggered",            # Bland successfully dialed
    "wrong_brand_review",   # post-push QA flagged
    "missing_contact_data", # enrichment returned nothing dialable
    "blocked_dnc",          # DNC registry block
})

# Subset of worked statuses that surface in the "Already Called" tab.
# missing_contact_data and blocked_dnc are excluded — those weren't called.
NG_ALREADY_CALLED_BLAND_STATUSES: frozenset[str] = frozenset({
    "triggered",
    "wrong_brand_review",
})


def _is_ng_contact_actionable(contact: dict) -> bool:
    """A tenant contact is actionable when it has a phone the operator can
    dial AND it hasn't already been worked. Both DNC clear and DNC unknown
    are considered actionable — the operator decides per-row using the DNC
    badge.
    """
    if not contact.get("phone"):
        return False
    return contact.get("bland_status") not in NG_WORKED_BLAND_STATUSES


def _is_ng_contact_already_called(contact: dict) -> bool:
    """A tenant contact is 'already called' when Bland completed a dial or
    post-push QA flagged it. Compliance holds (blocked_dnc) and never-dialed
    rows (missing_contact_data) are excluded — they belong elsewhere.
    """
    return contact.get("bland_status") in NG_ALREADY_CALLED_BLAND_STATUSES
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest tests/test_ng_dashboard_filter.py -v`
Expected: PASS — all 6 tests green.

- [ ] **Step 5: Commit**

```bash
git add services/dedup_service.py tests/test_ng_dashboard_filter.py
git commit -m "feat: add NG dashboard actionable/already-called predicates

Pure-function predicates that decide whether a tenant lead appears in
the main Vantage Residential view (actionable: phone present + not yet
worked) vs the new Vantage Already Called view (triggered or
wrong_brand_review). Constants spell out the bland_status sets so
future status additions surface visibly rather than silently changing
behavior.

Co-Authored-By: Claude Sonnet 4.6 <noreply@anthropic.com>"
```

---

## Task 2: Wire predicates into `_get_ng_dashboard_leads`

**Files:**
- Modify: `services/dedup_service.py:577-602` (`_get_ng_dashboard_leads`)
- Test: `tests/test_ng_dashboard_filter.py` (extend)

- [ ] **Step 1: Write failing tests for view-specific filtering**

Append to `tests/test_ng_dashboard_filter.py`:

```python
# ── _get_ng_dashboard_leads behavior ─────────────────────────────────────────

from unittest.mock import MagicMock, patch


def _fake_supabase_pair(ng_contact_rows: list[dict], filing_rows: list[dict]):
    """Build a MagicMock _client that returns the given contact/filing data."""
    client = MagicMock()

    contact_table = MagicMock()
    contact_table.select.return_value = contact_table
    contact_table.eq.return_value = contact_table
    contact_table.execute.return_value = MagicMock(data=ng_contact_rows)

    filing_table = MagicMock()
    # All chainable filings methods (select/eq/or_/in_/order/limit) return self;
    # only execute() returns data.
    for method in ("select", "eq", "or_", "in_", "order", "limit"):
        getattr(filing_table, method).return_value = filing_table
    filing_table.execute.return_value = MagicMock(data=filing_rows)

    def _table(name: str):
        return contact_table if name == "lead_contacts" else filing_table

    client.table.side_effect = _table
    return client


def test_ng_residential_returns_only_actionable():
    ng_contacts = [
        {"case_number": "A1", "track": "ng", "phone": "+15551110001",
         "bland_status": "pending", "dnc_status": "clear",
         "ghl_contact_id": "ghl-A1"},
        {"case_number": "A2", "track": "ng", "phone": "+15551110002",
         "bland_status": "triggered", "dnc_status": "clear",
         "ghl_contact_id": "ghl-A2"},
        {"case_number": "A3", "track": "ng", "phone": None,
         "bland_status": None, "dnc_status": "unknown",
         "ghl_contact_id": None},
        {"case_number": "A4", "track": "ng", "phone": "+15551110004",
         "bland_status": "pending_dnc_review", "dnc_status": "unknown",
         "ghl_contact_id": None},
    ]
    filings = [
        {"case_number": "A1", "tenant_name": "T1", "lead_bucket": "residential_approved"},
        {"case_number": "A4", "tenant_name": "T4", "lead_bucket": "residential_approved"},
    ]
    fake_client = _fake_supabase_pair(ng_contacts, filings)

    with patch.object(dedup_service, "_client", fake_client):
        rows = dedup_service._get_ng_dashboard_leads("ng_residential", 100)

    case_numbers = {r["case_number"] for r in rows}
    assert case_numbers == {"A1", "A4"}, (
        f"Expected actionable cases A1+A4, got {case_numbers}"
    )


def test_ng_already_called_returns_only_worked():
    ng_contacts = [
        {"case_number": "B1", "track": "ng", "phone": "+15552220001",
         "bland_status": "triggered", "dnc_status": "clear",
         "ghl_contact_id": "ghl-B1"},
        {"case_number": "B2", "track": "ng", "phone": "+15552220002",
         "bland_status": "pending", "dnc_status": "clear",
         "ghl_contact_id": "ghl-B2"},
        {"case_number": "B3", "track": "ng", "phone": "+15552220003",
         "bland_status": "wrong_brand_review", "dnc_status": "clear",
         "ghl_contact_id": "ghl-B3"},
    ]
    filings = [
        {"case_number": "B1", "tenant_name": "T1", "lead_bucket": "residential_approved"},
        {"case_number": "B3", "tenant_name": "T3", "lead_bucket": "residential_approved"},
    ]
    fake_client = _fake_supabase_pair(ng_contacts, filings)

    with patch.object(dedup_service, "_client", fake_client):
        rows = dedup_service._get_ng_dashboard_leads("ng_already_called", 100)

    case_numbers = {r["case_number"] for r in rows}
    assert case_numbers == {"B1", "B3"}


def test_ng_held_view_unchanged_by_actionable_filter():
    """Held / commercial / discarded views must keep their existing behavior."""
    ng_contacts = [
        {"case_number": "C1", "track": "ng", "phone": None,
         "bland_status": "missing_contact_data", "dnc_status": "unknown",
         "ghl_contact_id": None},
    ]
    filings = [
        {"case_number": "C1", "tenant_name": "T1", "lead_bucket": "held"},
    ]
    fake_client = _fake_supabase_pair(ng_contacts, filings)

    with patch.object(dedup_service, "_client", fake_client):
        rows = dedup_service._get_ng_dashboard_leads("ng_held", 100)

    # Held leads with no phone still appear — operator reviews them
    assert {r["case_number"] for r in rows} == {"C1"}
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/test_ng_dashboard_filter.py -v -k "test_ng_residential_returns or test_ng_already_called_returns or test_ng_held_view_unchanged"`
Expected: FAIL — current `_get_ng_dashboard_leads` returns all 3 cases for `ng_residential` (no filtering), and `ng_already_called` isn't a recognized view.

- [ ] **Step 3: Modify `_get_ng_dashboard_leads` to apply the predicates**

Replace the body of `_get_ng_dashboard_leads` (lines 577-602) with:

```python
def _get_ng_dashboard_leads(view: str, limit: int) -> list[dict]:
    ng_contacts = (
        _client.table("lead_contacts")
        .select(
            "case_number,track,phone,email,property_type,estimated_rent,"
            "dnc_status,dnc_source,language_hint,bland_status,ghl_contact_id"
        )
        .eq("track", "ng")
        .execute()
        .data
    )
    if not ng_contacts:
        return []

    # View-specific actionable filtering. Other views (commercial / held /
    # spanish_* / discarded) intentionally pass through unfiltered — they
    # still want everything in the bucket regardless of phone/bland state.
    if view == "ng_residential":
        ng_contacts = [c for c in ng_contacts if _is_ng_contact_actionable(c)]
    elif view == "ng_already_called":
        ng_contacts = [c for c in ng_contacts if _is_ng_contact_already_called(c)]
    if not ng_contacts:
        return []

    ng_case_numbers = [row["case_number"] for row in ng_contacts]
    query = _client.table("filings").select(_DASHBOARD_SELECT)
    query = _filter_dashboard_query(query, view)
    query = query.in_("case_number", ng_case_numbers)
    result = (
        query
        .order("court_date", desc=False, nullsfirst=False)
        .order("filing_date", desc=True)
        .limit(limit)
        .execute()
    )
    rows = _overlay_contact_rows(result.data, ng_contacts, clear_missing_contact=False)
    return _decorate_dashboard_rows(rows, "ng", view)
```

- [ ] **Step 4: Add `ng_already_called` branch to `_filter_dashboard_query`**

Modify `services/dedup_service.py:508-528` (`_filter_dashboard_query`). Insert a new branch BEFORE the legacy fallback at line 525:

```python
    if view == "ng_already_called":
        # Same filings-side filter as ng_residential — bucket and language.
        # The bland_status restriction is applied to the lead_contacts
        # pre-query in _get_ng_dashboard_leads.
        return query.eq("lead_bucket", "residential_approved").or_(
            "language_hint.is.null,language_hint.neq.spanish_likely"
        )
    # Legacy fallback — residential approved, non-Spanish
```

The full updated `_filter_dashboard_query` should look like:

```python
def _filter_dashboard_query(query, view: str):
    if view in ("ec_residential", "ng_residential"):
        return query.eq("lead_bucket", "residential_approved").or_(
            "language_hint.is.null,language_hint.neq.spanish_likely"
        )
    if view in ("ec_commercial", "ng_commercial"):
        return query.eq("lead_bucket", "commercial").or_(
            "language_hint.is.null,language_hint.neq.spanish_likely"
        )
    if view == "ng_spanish_residential":
        return query.eq("lead_bucket", "residential_approved").eq("language_hint", "spanish_likely")
    if view == "ng_spanish_commercial":
        return query.eq("lead_bucket", "commercial").eq("language_hint", "spanish_likely")
    if view in ("ec_held", "ng_held"):
        return query.eq("lead_bucket", "held")
    if view in ("ec_discarded", "ng_discarded"):
        return query.eq("lead_bucket", "discarded")
    if view == "ng_already_called":
        return query.eq("lead_bucket", "residential_approved").or_(
            "language_hint.is.null,language_hint.neq.spanish_likely"
        )
    # Legacy fallback — residential approved, non-Spanish
    return query.eq("lead_bucket", "residential_approved").or_(
        "language_hint.is.null,language_hint.neq.spanish_likely"
    )
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `python -m pytest tests/test_ng_dashboard_filter.py -v`
Expected: PASS — all 9 tests (6 predicate + 3 view) green.

- [ ] **Step 6: Run the existing dashboard view tests to make sure nothing broke**

Run: `python -m pytest tests/test_dashboard_views.py tests/test_dashboard_dnc_gate.py tests/test_dashboard_bland_test.py -v`
Expected: PASS — all existing tests still pass. (The predicate filter only affects new views; existing filter calls are unchanged.)

- [ ] **Step 7: Commit**

```bash
git add services/dedup_service.py tests/test_ng_dashboard_filter.py
git commit -m "feat: filter ng_residential to actionable + add ng_already_called view

_get_ng_dashboard_leads now applies the actionable predicate when
view='ng_residential' (phone present + not yet worked) and the
already-called predicate when view='ng_already_called' (Bland triggered
or wrong_brand_review). Other NG views (commercial/held/discarded/
spanish_*) pass through unfiltered — those buckets still want everything.

_filter_dashboard_query gets a new ng_already_called branch that mirrors
ng_residential's filings-side filter (the bland_status restriction is on
lead_contacts, not filings).

Co-Authored-By: Claude Sonnet 4.6 <noreply@anthropic.com>"
```

---

## Task 3: Update `_ng_counts_from_contact_rows` to apply the actionable filter

**Files:**
- Modify: `services/dedup_service.py:651-678` (`_ng_counts_from_contact_rows`)
- Modify: `services/dedup_service.py:694-711` (`get_dashboard_counts` — expand select to include phone + bland_status)
- Test: `tests/test_ng_dashboard_filter.py` (extend)

- [ ] **Step 1: Write failing tests for the new counting behavior**

Append to `tests/test_ng_dashboard_filter.py`:

```python
# ── _ng_counts_from_contact_rows behavior ────────────────────────────────────

def test_ng_counts_actionable_filter_applied_to_residential():
    """ng_residential count reflects the actionable predicate, not raw count."""
    rows = [
        # Actionable
        {"phone": "+15551111111", "bland_status": "pending",
         "filings": {"lead_bucket": "residential_approved", "language_hint": None}},
        {"phone": "+15551111112", "bland_status": None,
         "filings": {"lead_bucket": "residential_approved", "language_hint": None}},
        # Already called — counted separately, not in ng_residential
        {"phone": "+15551111113", "bland_status": "triggered",
         "filings": {"lead_bucket": "residential_approved", "language_hint": None}},
        # No phone — excluded everywhere
        {"phone": None, "bland_status": "missing_contact_data",
         "filings": {"lead_bucket": "residential_approved", "language_hint": None}},
    ]
    counts = dedup_service._ng_counts_from_contact_rows(rows)
    assert counts["ng_residential"] == 2
    assert counts["ng_already_called"] == 1


def test_ng_counts_already_called_excludes_other_buckets():
    """Triggered leads in held/discarded buckets don't leak into ng_already_called."""
    rows = [
        {"phone": "+15552220001", "bland_status": "triggered",
         "filings": {"lead_bucket": "residential_approved", "language_hint": None}},
        {"phone": "+15552220002", "bland_status": "triggered",
         "filings": {"lead_bucket": "held", "language_hint": None}},
        {"phone": "+15552220003", "bland_status": "triggered",
         "filings": {"lead_bucket": "discarded", "language_hint": None}},
    ]
    counts = dedup_service._ng_counts_from_contact_rows(rows)
    assert counts["ng_already_called"] == 1, (
        "Only the residential_approved triggered lead should count"
    )
    assert counts["ng_held"] == 1
    assert counts["ng_discarded"] == 1


def test_ng_counts_spanish_residential_not_affected_by_actionable_filter():
    """Spanish residential keeps its existing semantics (no phone filter)."""
    rows = [
        # Spanish + residential + no phone — still counted in spanish_residential
        {"phone": None, "bland_status": None,
         "filings": {"lead_bucket": "residential_approved",
                     "language_hint": "spanish_likely"}},
    ]
    counts = dedup_service._ng_counts_from_contact_rows(rows)
    assert counts["ng_spanish_residential"] == 1
    assert counts["ng_residential"] == 0
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/test_ng_dashboard_filter.py::test_ng_counts_actionable_filter_applied_to_residential tests/test_ng_dashboard_filter.py::test_ng_counts_already_called_excludes_other_buckets tests/test_ng_dashboard_filter.py::test_ng_counts_spanish_residential_not_affected_by_actionable_filter -v`
Expected:
- First test FAILs because `ng_residential` is currently 3 (raw count, no filter) and `ng_already_called` key doesn't exist
- Second test FAILs for the same reason
- Third test passes already (spanish path unchanged) — that's fine, it documents the invariant.

- [ ] **Step 3: Update `_ng_counts_from_contact_rows` to apply the predicates**

Replace lines 651-678 of `services/dedup_service.py`:

```python
def _ng_counts_from_contact_rows(rows: list[dict]) -> dict:
    """Tally NG-track dashboard counts. Applies the actionable predicate to
    residential_approved (so ng_residential matches the table), and adds
    ng_already_called for Bland-triggered / wrong_brand_review leads.

    Spanish, commercial, held, and discarded counts keep their original
    semantics (no phone/bland filtering) — those views still surface
    everything in their bucket.
    """
    counts = {
        "ng_residential": 0,
        "ng_commercial": 0,
        "ng_spanish_residential": 0,
        "ng_spanish_commercial": 0,
        "ng_held": 0,
        "ng_discarded": 0,
        "ng_already_called": 0,
    }
    for row in rows:
        filing = row.get("filings") or {}
        bucket = filing.get("lead_bucket")
        spanish = filing.get("language_hint") == "spanish_likely"
        if bucket == "residential_approved":
            if spanish:
                counts["ng_spanish_residential"] += 1
            elif _is_ng_contact_actionable(row):
                counts["ng_residential"] += 1
            if not spanish and _is_ng_contact_already_called(row):
                counts["ng_already_called"] += 1
        elif bucket == "commercial":
            if spanish:
                counts["ng_spanish_commercial"] += 1
            else:
                counts["ng_commercial"] += 1
        elif bucket == "held":
            counts["ng_held"] += 1
        elif bucket == "discarded":
            counts["ng_discarded"] += 1
    return counts
```

- [ ] **Step 4: Expand the lead_contacts select in `get_dashboard_counts`**

Modify lines 702-709 of `services/dedup_service.py`. The current `select` only fetches `case_number,filings(...)`; we need `phone` and `bland_status` too so the predicates have data to work with.

Replace:

```python
        ng_rows = (
            _client.table("lead_contacts")
            .select("case_number,filings(lead_bucket,language_hint)")
            .eq("track", "ng")
            .limit(10000)
            .execute()
            .data
        )
```

with:

```python
        ng_rows = (
            _client.table("lead_contacts")
            .select(
                "case_number,phone,bland_status,"
                "filings(lead_bucket,language_hint)"
            )
            .eq("track", "ng")
            .limit(10000)
            .execute()
            .data
        )
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `python -m pytest tests/test_ng_dashboard_filter.py -v`
Expected: PASS — all 12 tests green.

- [ ] **Step 6: Run the full existing dashboard test suite to catch regressions**

Run: `python -m pytest tests/test_dashboard_views.py tests/test_dashboard_dnc_gate.py tests/test_dashboard_bland_test.py tests/test_dedup_service.py tests/test_dedup_retry.py -v`
Expected: PASS — all existing tests still pass.

- [ ] **Step 7: Commit**

```bash
git add services/dedup_service.py tests/test_ng_dashboard_filter.py
git commit -m "feat: apply actionable filter to ng_residential count tile

_ng_counts_from_contact_rows now uses the same predicates as
_get_ng_dashboard_leads so the count tile matches the table row count.
Adds ng_already_called to the returned dict. Expands the lead_contacts
select in get_dashboard_counts so the predicates have phone +
bland_status to evaluate.

Spanish, commercial, held, and discarded counts keep their existing
(no-filter) semantics — those buckets still surface everything.

Co-Authored-By: Claude Sonnet 4.6 <noreply@anthropic.com>"
```

---

## Task 4: Add `ng_already_called` to the frontend config

**Files:**
- Modify: `dashboard/index.html` (three locations)
- Test: `tests/test_ng_dashboard_filter.py` (extend with static checks)

- [ ] **Step 1: Write a failing static-inspection test**

Append to `tests/test_ng_dashboard_filter.py`:

```python
# ── dashboard/index.html static config integrity ─────────────────────────────

from pathlib import Path

_INDEX_HTML = Path(__file__).resolve().parents[1] / "dashboard" / "index.html"


def test_index_html_brand_views_includes_ng_already_called():
    """ng_already_called must be in the brandViews.ng tab list."""
    src = _INDEX_HTML.read_text(encoding="utf-8")
    # We look for the literal string inside the ng array. The array spans
    # one or two lines depending on formatting; the substring check is
    # tolerant of either.
    assert "'ng_already_called'" in src or '"ng_already_called"' in src, (
        "ng_already_called is not registered in dashboard/index.html"
    )


def test_index_html_view_labels_full_and_short():
    """Both full and short labels for ng_already_called must be present."""
    src = _INDEX_HTML.read_text(encoding="utf-8")
    assert "Vantage Already Called" in src, "full label missing in viewLabels"
    assert "Already Called" in src, "short label missing in viewLabelsShort"


def test_index_html_already_called_listed_before_discarded():
    """Tab order intent: actionable → ... → held → already called → discarded."""
    src = _INDEX_HTML.read_text(encoding="utf-8")
    idx_already = src.find("'ng_already_called'")
    idx_discarded = src.find("'ng_discarded'")
    assert idx_already != -1 and idx_discarded != -1, (
        "Either ng_already_called or ng_discarded missing from brandViews"
    )
    assert idx_already < idx_discarded, (
        "ng_already_called should appear before ng_discarded in brandViews.ng "
        f"(found at {idx_already} vs {idx_discarded})"
    )
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/test_ng_dashboard_filter.py::test_index_html_brand_views_includes_ng_already_called tests/test_ng_dashboard_filter.py::test_index_html_view_labels_full_and_short tests/test_ng_dashboard_filter.py::test_index_html_already_called_listed_before_discarded -v`
Expected: FAIL — none of the strings are in `dashboard/index.html` yet.

- [ ] **Step 3: Add `ng_already_called` to `brandViews.ng`**

In `dashboard/index.html`, find the line beginning with `ng: ['ng_residential'` (around line 1018) and replace it with:

```js
      ng: ['ng_residential', 'ng_commercial', 'ng_spanish_residential', 'ng_spanish_commercial', 'ng_held', 'ng_already_called', 'ng_discarded'],
```

(One new entry added: `'ng_already_called'`, positioned before `'ng_discarded'`.)

- [ ] **Step 4: Add full label to `viewLabels`**

Find the `viewLabels` object (around line 1021) and add a new entry. The block currently ends with:

```js
      ng_held: 'Held',
      ng_discarded: 'Discarded',
    };
```

Replace with:

```js
      ng_held: 'Held',
      ng_already_called: 'Vantage Already Called',
      ng_discarded: 'Discarded',
    };
```

- [ ] **Step 5: Add short label to both `viewLabelsShort` maps**

There are two copies of `viewLabelsShort` (around lines 1046 and 1112). For each one, find:

```js
              ng_held: 'Held', ng_discarded: 'Discarded',
```

(May appear in slightly different formatting in the two locations — search for `ng_held:` to find each.) Replace with:

```js
              ng_held: 'Held', ng_already_called: 'Already Called', ng_discarded: 'Discarded',
```

- [ ] **Step 6: Run static tests to verify they pass**

Run: `python -m pytest tests/test_ng_dashboard_filter.py -v`
Expected: PASS — all 15 tests green (6 predicate + 3 view + 3 count + 3 static).

- [ ] **Step 7: Manual smoke test (optional but recommended)**

Start the dashboard locally:
```bash
uvicorn dashboard.main:app --reload --port 8000
```

Open `http://localhost:8000` in a browser. Click the Vantage brand button. Confirm the chip row shows: Residential, Commercial, Spanish Res, Spanish Com, Held, **Already Called**, Discarded. Click "Already Called" — it should load without errors (empty table is fine if local data has nothing matching).

- [ ] **Step 8: Commit**

```bash
git add dashboard/index.html tests/test_ng_dashboard_filter.py
git commit -m "feat: add Already Called tab to Vantage dashboard

Three config additions in dashboard/index.html: brandViews.ng gets
ng_already_called, viewLabels gets the full label, viewLabelsShort
(both copies) gets the chip text. Tab order is actionable → commercial
→ spanish → held → already called → discarded.

Static-inspection tests guard the three additions so future config
edits can't accidentally drop them.

Co-Authored-By: Claude Sonnet 4.6 <noreply@anthropic.com>"
```

---

## Task 5: Open PR + final test sweep + deploy

**Files:** none (orchestration)

- [ ] **Step 1: Run the full test suite to catch any regressions**

Run: `python -m pytest tests/ -q --ignore=tests/test_dekalb_scraper.py`
Expected: PASS — should be the prior baseline (425) + 15 new = **440 passing**. (Adjust if other tests landed between this plan and execution.)

- [ ] **Step 2: Push the branch**

```bash
git push -u origin feat/ng-dashboard-actionable-filter
```

(Branch name assumes execution started on a branch called `feat/ng-dashboard-actionable-filter`. Adjust if branched differently.)

- [ ] **Step 3: Open PR via gh CLI**

```bash
gh pr create --base main --head feat/ng-dashboard-actionable-filter --title "feat: Vantage dashboard actionable filter + Already Called tab" --body "$(cat <<'EOF'
## Summary
Tightens the Vantage Residential view so it only shows actionable tenant leads. Drops the count from 541 → ~14 by excluding leads with no phone or already worked by Bland. Adds a new "Already Called" tab (~66 leads today) so dialed history is still discoverable.

Per the approved spec: docs/superpowers/specs/2026-05-22-vantage-dashboard-actionable-filter-design.md

## What changed
- `services/dedup_service.py` — new predicates `_is_ng_contact_actionable` / `_is_ng_contact_already_called`. Applied in `_get_ng_dashboard_leads`, `_ng_counts_from_contact_rows`, and the `lead_contacts` select inside `get_dashboard_counts`.
- `dashboard/index.html` — three config additions for the new tab.
- `tests/test_ng_dashboard_filter.py` — 15 new tests across predicates, view filtering, count math, and frontend config integrity.

## Tab order (Vantage brand)
Residential → Commercial → Spanish Res → Spanish Com → Held → Already Called → Discarded

## Test plan
- [x] pytest tests/ — 440 passing
- [ ] After merge: open dashboard, switch to Vantage brand, confirm Residential shows actionable subset and Already Called shows triggered/wrong_brand_review leads
- [ ] Confirm count tiles match table row counts

## Scope notes
- EC (landlord) parity deferred — separate PR
- Pipeline-health checker script deferred — separate PR
- SQL migration for run_metrics columns still pending operator action — independent of this PR

🤖 Generated with [Claude Code](https://claude.com/claude-code)
EOF
)"
```

- [ ] **Step 4: Merge once reviewed**

```bash
gh pr merge --squash --delete-branch
```

- [ ] **Step 5: Sync local main**

```bash
git checkout main
git pull --ff-only
```

- [ ] **Step 6: Verify on Railway after auto-deploy**

Wait for Railway to redeploy (Dockerfile/nixpacks build). Once deployed, open the production dashboard URL, switch to Vantage brand. Check:
- Residential tab count chip matches the number of rows in the table
- Already Called tab shows triggered + wrong_brand_review leads
- Switching between tabs works without browser console errors

---

## Verification checklist (post-merge)

- [ ] Vantage Residential count tile now shows the actionable count (single-digit to low-double-digit range based on current data)
- [ ] Vantage Already Called tab shows ~66 leads
- [ ] Triggered leads no longer appear in the main Residential view
- [ ] Phone-less leads no longer appear in the main Residential view
- [ ] DNC-unknown leads with phones DO appear (DNC badge per row)
- [ ] Held / Discarded / Commercial / Spanish counts unchanged from before
- [ ] No Pushover errors fired by this change
