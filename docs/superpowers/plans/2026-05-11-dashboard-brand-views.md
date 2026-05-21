# Dashboard Brand Views Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add GRANT / VANTAGE brand tabs to the dashboard, show per-brand contact data and columns, surface a Ready to Call metric, and sort by court date by default.

**Architecture:** Backend view keys gain `ec_` / `ng_` prefixes so track is encoded in the view name, eliminating the need for a separate `track` query param. Frontend adds a brand tab toggle that rewires the view chips, column headers, and row rendering. All new columns are computed client-side — no schema migrations required.

**Tech Stack:** FastAPI + Supabase (postgrest-py), vanilla JS, pytest + pytest-asyncio

---

## File Map

| File | Change |
|---|---|
| `services/dedup_service.py` | New view keys, per-track counts, `scraped_at` in select, `court_date` sort |
| `dashboard/main.py` | No logic change — endpoints already pass `view` through |
| `dashboard/index.html` | Brand tabs, column swap, corporate badge, email dot, Ready to Call, NEW badge |
| `tests/test_dashboard_views.py` | Replace old view key tests, add new ones |

---

## Task 1 — Backend: new view keys + `scraped_at` + sort

**Files:**
- Modify: `services/dedup_service.py`
- Test: `tests/test_dashboard_views.py`

- [ ] **Step 1.1 — Write failing tests for new view key routing**

Replace the entire contents of `tests/test_dashboard_views.py`:

```python
from services import dedup_service


class Query:
    def __init__(self):
        self.calls: list[tuple[str, str, str | None]] = []

    def eq(self, column: str, value: str):
        self.calls.append(("eq", column, value))
        return self

    def or_(self, value: str):
        self.calls.append(("or", value, None))
        return self


# ── _filter_dashboard_query ──────────────────────────────────────────────────

def test_ec_residential_excludes_spanish():
    q = Query()
    dedup_service._filter_dashboard_query(q, "ec_residential")
    assert ("eq", "lead_bucket", "residential_approved") in q.calls
    assert any(c[0] == "or" for c in q.calls)


def test_ec_commercial_excludes_spanish():
    q = Query()
    dedup_service._filter_dashboard_query(q, "ec_commercial")
    assert ("eq", "lead_bucket", "commercial") in q.calls
    assert any(c[0] == "or" for c in q.calls)


def test_ec_held():
    q = Query()
    dedup_service._filter_dashboard_query(q, "ec_held")
    assert q.calls == [("eq", "lead_bucket", "held")]


def test_ec_discarded():
    q = Query()
    dedup_service._filter_dashboard_query(q, "ec_discarded")
    assert q.calls == [("eq", "lead_bucket", "discarded")]


def test_ng_residential_excludes_spanish():
    q = Query()
    dedup_service._filter_dashboard_query(q, "ng_residential")
    assert ("eq", "lead_bucket", "residential_approved") in q.calls
    assert any(c[0] == "or" for c in q.calls)


def test_ng_commercial_excludes_spanish():
    q = Query()
    dedup_service._filter_dashboard_query(q, "ng_commercial")
    assert ("eq", "lead_bucket", "commercial") in q.calls
    assert any(c[0] == "or" for c in q.calls)


def test_ng_spanish_residential():
    q = Query()
    dedup_service._filter_dashboard_query(q, "ng_spanish_residential")
    assert q.calls == [
        ("eq", "lead_bucket", "residential_approved"),
        ("eq", "language_hint", "spanish_likely"),
    ]


def test_ng_spanish_commercial():
    q = Query()
    dedup_service._filter_dashboard_query(q, "ng_spanish_commercial")
    assert q.calls == [
        ("eq", "lead_bucket", "commercial"),
        ("eq", "language_hint", "spanish_likely"),
    ]


def test_ng_held():
    q = Query()
    dedup_service._filter_dashboard_query(q, "ng_held")
    assert q.calls == [("eq", "lead_bucket", "held")]


def test_ng_discarded():
    q = Query()
    dedup_service._filter_dashboard_query(q, "ng_discarded")
    assert q.calls == [("eq", "lead_bucket", "discarded")]


# ── _track_for_dashboard_view ────────────────────────────────────────────────

def test_ec_views_return_ec_track():
    for view in ("ec_residential", "ec_commercial", "ec_held", "ec_discarded"):
        assert dedup_service._track_for_dashboard_view(view) == "ec", view


def test_ng_views_return_ng_track():
    for view in (
        "ng_residential", "ng_commercial",
        "ng_spanish_residential", "ng_spanish_commercial",
        "ng_held", "ng_discarded",
    ):
        assert dedup_service._track_for_dashboard_view(view) == "ng", view


# ── _ec_counts_from_rows ─────────────────────────────────────────────────────

def test_ec_counts_split_by_bucket():
    rows = [
        {"lead_bucket": "residential_approved", "language_hint": None},
        {"lead_bucket": "residential_approved", "language_hint": "spanish_likely"},  # spanish excluded from ec_residential
        {"lead_bucket": "commercial", "language_hint": None},
        {"lead_bucket": "held", "language_hint": None},
        {"lead_bucket": "discarded", "language_hint": None},
    ]
    counts = dedup_service._ec_counts_from_rows(rows)
    assert counts["ec_residential"] == 1
    assert counts["ec_commercial"] == 1
    assert counts["ec_held"] == 1
    assert counts["ec_discarded"] == 1


def test_ec_counts_spanish_residential_not_counted_in_ec_residential():
    rows = [
        {"lead_bucket": "residential_approved", "language_hint": "spanish_likely"},
        {"lead_bucket": "commercial", "language_hint": "spanish_likely"},
    ]
    counts = dedup_service._ec_counts_from_rows(rows)
    assert counts["ec_residential"] == 0
    assert counts["ec_commercial"] == 0


# ── _ng_counts_from_contact_rows ─────────────────────────────────────────────

def test_ng_counts_split_by_bucket_and_language():
    rows = [
        {"filings": {"lead_bucket": "residential_approved", "language_hint": None}},
        {"filings": {"lead_bucket": "residential_approved", "language_hint": "spanish_likely"}},
        {"filings": {"lead_bucket": "commercial", "language_hint": None}},
        {"filings": {"lead_bucket": "commercial", "language_hint": "spanish_likely"}},
        {"filings": {"lead_bucket": "held", "language_hint": None}},
        {"filings": {"lead_bucket": "discarded", "language_hint": None}},
    ]
    counts = dedup_service._ng_counts_from_contact_rows(rows)
    assert counts["ng_residential"] == 1
    assert counts["ng_spanish_residential"] == 1
    assert counts["ng_commercial"] == 1
    assert counts["ng_spanish_commercial"] == 1
    assert counts["ng_held"] == 1
    assert counts["ng_discarded"] == 1


def test_ng_counts_null_filings_skipped():
    rows = [{"filings": None}, {"filings": {}}]
    counts = dedup_service._ng_counts_from_contact_rows(rows)
    assert sum(counts.values()) == 0
```

- [ ] **Step 1.2 — Run tests to confirm they fail**

```
pytest tests/test_dashboard_views.py -v
```

Expected: all new tests FAIL with AttributeError or AssertionError.

- [ ] **Step 1.3 — Update `_DASHBOARD_SELECT` to include `scraped_at`**

In `services/dedup_service.py`, replace:

```python
_DASHBOARD_SELECT = (
    "case_number,tenant_name,landlord_name,property_address,"
    "state,county,filing_date,court_date,phone,email,"
    "property_type,estimated_rent,property_zip,lead_bucket,"
    "discard_reason,qualification_notes,dnc_status,dnc_source,language_hint,"
    "bland_status,ghl_contact_id"
)
```

With:

```python
_DASHBOARD_SELECT = (
    "case_number,tenant_name,landlord_name,property_address,"
    "state,county,filing_date,court_date,scraped_at,phone,email,"
    "property_type,estimated_rent,property_zip,lead_bucket,"
    "discard_reason,qualification_notes,dnc_status,dnc_source,language_hint,"
    "bland_status,ghl_contact_id"
)
```

- [ ] **Step 1.4 — Replace `_filter_dashboard_query` and `_track_for_dashboard_view`**

In `services/dedup_service.py`, replace the two functions:

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
    # Legacy fallback — residential approved, non-Spanish
    return query.eq("lead_bucket", "residential_approved").or_(
        "language_hint.is.null,language_hint.neq.spanish_likely"
    )


def _track_for_dashboard_view(view: str) -> str:
    return "ng" if view.startswith("ng_") else "ec"
```

- [ ] **Step 1.5 — Update `_target_metadata` for new Spanish view keys**

Replace the `_target_metadata` function:

```python
def _target_metadata(track: str, view: str) -> dict:
    is_spanish = view in {"ng_spanish_residential", "ng_spanish_commercial"}
    if track == "ng":
        return {
            "target_track": "ng",
            "target_brand": "Vantage Defense Group",
            "target_role": "Spanish tenant" if is_spanish else "Tenant",
            "target_phone_label": "Tenant Phone",
            "missing_phone_label": "NO TENANT PHONE",
        }
    return {
        "target_track": "ec",
        "target_brand": "Grant Ellis Group",
        "target_role": "Landlord / owner",
        "target_phone_label": "Landlord Phone",
        "missing_phone_label": "NO LANDLORD PHONE",
    }
```

- [ ] **Step 1.6 — Change default sort in `get_dashboard_leads` to `court_date ASC`**

In `get_dashboard_leads`, replace:

```python
        result = (
            query
            .order("filing_date", desc=True)
            .limit(limit)
            .execute()
        )
```

With:

```python
        result = (
            query
            .order("court_date", desc=False, nullsfirst=False)
            .order("filing_date", desc=True)
            .limit(limit)
            .execute()
        )
```

- [ ] **Step 1.7 — Run tests — expect routing and track tests to pass, count tests still failing**

```
pytest tests/test_dashboard_views.py -v
```

Expected: view routing tests PASS, count tests FAIL (functions don't exist yet).

---

## Task 2 — Backend: per-track counts

**Files:**
- Modify: `services/dedup_service.py`

- [ ] **Step 2.1 — Replace `_dashboard_counts_from_rows` with two focused functions**

In `services/dedup_service.py`, replace the existing `_dashboard_counts_from_rows` function with:

```python
def _ec_counts_from_rows(rows: list[dict]) -> dict:
    counts = {
        "ec_residential": 0,
        "ec_commercial": 0,
        "ec_held": 0,
        "ec_discarded": 0,
    }
    for row in rows:
        bucket = row.get("lead_bucket")
        spanish = row.get("language_hint") == "spanish_likely"
        if bucket == "residential_approved" and not spanish:
            counts["ec_residential"] += 1
        elif bucket == "commercial" and not spanish:
            counts["ec_commercial"] += 1
        elif bucket == "held":
            counts["ec_held"] += 1
        elif bucket == "discarded":
            counts["ec_discarded"] += 1
    return counts


def _ng_counts_from_contact_rows(rows: list[dict]) -> dict:
    counts = {
        "ng_residential": 0,
        "ng_commercial": 0,
        "ng_spanish_residential": 0,
        "ng_spanish_commercial": 0,
        "ng_held": 0,
        "ng_discarded": 0,
    }
    for row in rows:
        filing = row.get("filings") or {}
        bucket = filing.get("lead_bucket")
        spanish = filing.get("language_hint") == "spanish_likely"
        if bucket == "residential_approved":
            if spanish:
                counts["ng_spanish_residential"] += 1
            else:
                counts["ng_residential"] += 1
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

- [ ] **Step 2.2 — Update `get_dashboard_counts` to use both functions**

Replace `get_dashboard_counts`:

```python
async def get_dashboard_counts() -> dict:
    def _query() -> dict:
        ec_rows = (
            _client.table("filings")
            .select("lead_bucket,language_hint")
            .execute()
            .data
        )
        ng_rows = (
            _client.table("lead_contacts")
            .select("case_number,filings(lead_bucket,language_hint)")
            .eq("track", "ng")
            .execute()
            .data
        )
        return {**_ec_counts_from_rows(ec_rows), **_ng_counts_from_contact_rows(ng_rows)}
    return await asyncio.to_thread(_query)
```

- [ ] **Step 2.3 — Run all count tests**

```
pytest tests/test_dashboard_views.py -v
```

Expected: all tests PASS.

- [ ] **Step 2.4 — Run full test suite to check for regressions**

```
pytest -q
```

Expected: no new failures. Note: any test importing `_dashboard_counts_from_rows` directly will break — fix those by updating to `_ec_counts_from_rows`.

- [ ] **Step 2.5 — Commit backend changes**

```
git add services/dedup_service.py tests/test_dashboard_views.py
git commit -m "feat: add ec/ng prefixed view keys and per-track dashboard counts"
```

---

## Task 3 — Frontend: brand tabs + view chip structure

**Files:**
- Modify: `dashboard/index.html`

- [ ] **Step 3.1 — Add brand tab CSS**

In `dashboard/index.html`, after the `.chip.active-red` block, add:

```css
    .chip.active-blue {
      background: #1a2d3a;
      border-color: #3a6b8f;
      color: #4a9bbf;
    }
```

- [ ] **Step 3.2 — Add brand tabs to the toolbar HTML**

Replace the toolbar div contents:

```html
    <!-- TOOLBAR -->
    <div class="toolbar">
      <span class="toolbar-label">Brand</span>
      <button class="chip active" id="brand-ec" onclick="setBrand('ec')">GRANT</button>
      <button class="chip" id="brand-ng" onclick="setBrand('ng')">VANTAGE</button>
      <div class="toolbar-sep"></div>
      <span class="toolbar-label">View</span>
      <button class="chip active" id="view-ec_residential" onclick="setView('ec_residential')">Residential</button>
      <button class="chip" id="view-ec_commercial" onclick="setView('ec_commercial')">Commercial</button>
      <button class="chip" id="view-ec_held" onclick="setView('ec_held')">Held</button>
      <button class="chip" id="view-ec_discarded" onclick="setView('ec_discarded')">Discarded</button>
      <div class="toolbar-sep"></div>
      <span class="toolbar-label">Filter</span>
      <input class="filter-input" id="filter-input" type="text" placeholder="case, name, address…"
        oninput="applyFilters()">
      <div class="toolbar-sep"></div>
      <button class="chip" id="chip-phone" onclick="toggleChip('phone')">Has Phone</button>
      <button class="chip" id="chip-dnc" onclick="toggleChip('dnc')">DNC Clear</button>
      <button class="chip" id="chip-residential" onclick="toggleChip('residential')">Residential</button>
      <button class="chip" id="chip-commercial" onclick="toggleChip('commercial')">Commercial</button>
      <button class="chip" id="chip-overdue" onclick="toggleChip('overdue')">Overdue</button>
      <div class="queue-count" id="queue-count"></div>
    </div>
```

- [ ] **Step 3.3 — Update JS state and view config**

Replace the STATE block in `<script>`:

```javascript
    // ── STATE ──
    let allLeads = [];
    let visibleLeads = [];
    let selectedCases = new Set();
    let sortKey = null;
    let sortDir = 1;
    let chips = { phone: false, dnc: false, residential: false, commercial: false, overdue: false };
    let kbIndex = -1;
    let activeBrand = 'ec';
    let activeView = 'ec_residential';
    let lastRunAt = null;

    const brandViews = {
      ec: ['ec_residential', 'ec_commercial', 'ec_held', 'ec_discarded'],
      ng: ['ng_residential', 'ng_commercial', 'ng_spanish_residential', 'ng_spanish_commercial', 'ng_held', 'ng_discarded'],
    };

    const viewLabels = {
      ec_residential: 'English Residential Approved',
      ec_commercial: 'Commercial High Priority',
      ec_held: 'Held',
      ec_discarded: 'Discarded',
      ng_residential: 'Vantage Residential',
      ng_commercial: 'Vantage Commercial',
      ng_spanish_residential: 'Spanish Residential Approved',
      ng_spanish_commercial: 'Spanish Commercial',
      ng_held: 'Held',
      ng_discarded: 'Discarded',
    };
```

- [ ] **Step 3.4 — Add `setBrand` and update `setView`**

Replace the existing `setView` function and add `setBrand` after it:

```javascript
    function setBrand(brand) {
      activeBrand = brand;
      selectedCases.clear();
      kbIndex = -1;

      document.getElementById('brand-ec').classList.remove('active', 'active-blue');
      document.getElementById('brand-ng').classList.remove('active', 'active-blue');
      document.getElementById(`brand-${brand}`).classList.add(brand === 'ng' ? 'active-blue' : 'active');

      _rebuildViewChips(brand);
      activeView = brandViews[brand][0];
      loadLeadCounts();
      loadLeads();
    }

    function _rebuildViewChips(brand) {
      const viewLabelsShort = {
        ec_residential: 'Residential', ec_commercial: 'Commercial',
        ec_held: 'Held', ec_discarded: 'Discarded',
        ng_residential: 'Residential', ng_commercial: 'Commercial',
        ng_spanish_residential: 'Spanish Res', ng_spanish_commercial: 'Spanish Com',
        ng_held: 'Held', ng_discarded: 'Discarded',
      };
      // Remove all existing view chips
      document.querySelectorAll('[id^="view-"]').forEach(el => el.remove());

      // Insert new chips before the filter separator
      const sep = document.querySelector('.toolbar .toolbar-sep:nth-of-type(2)');
      brandViews[brand].forEach((viewKey, i) => {
        const btn = document.createElement('button');
        btn.className = 'chip' + (i === 0 ? ' active' : '');
        btn.id = `view-${viewKey}`;
        btn.textContent = viewLabelsShort[viewKey];
        btn.onclick = () => setView(viewKey);
        sep.parentNode.insertBefore(btn, sep);
      });
    }

    function setView(name) {
      activeView = name;
      selectedCases.clear();
      kbIndex = -1;
      document.querySelectorAll('[id^="view-"]').forEach(el => {
        el.classList.remove('active', 'active-green', 'active-red', 'active-blue');
      });
      const isDiscard = name.endsWith('_discarded');
      const isCommercial = name.endsWith('_commercial');
      const isSpanish = name.includes('spanish');
      const cls = isDiscard ? 'active-red' : isCommercial ? 'active-green' : isSpanish ? 'active-blue' : 'active';
      document.getElementById(`view-${name}`).classList.add(cls);
      loadLeads();
    }
```

- [ ] **Step 3.5 — Update `loadLeadCounts` for new key names**

Replace `loadLeadCounts`:

```javascript
    async function loadLeadCounts() {
      try {
        const res = await fetch('/api/lead-counts');
        const counts = await res.json();
        brandViews[activeBrand].forEach(viewKey => {
          const el = document.getElementById(`view-${viewKey}`);
          if (el) {
            const shortLabels = {
              ec_residential: 'Residential', ec_commercial: 'Commercial',
              ec_held: 'Held', ec_discarded: 'Discarded',
              ng_residential: 'Residential', ng_commercial: 'Commercial',
              ng_spanish_residential: 'Spanish Res', ng_spanish_commercial: 'Spanish Com',
              ng_held: 'Held', ng_discarded: 'Discarded',
            };
            el.textContent = `${shortLabels[viewKey]} (${counts[viewKey] ?? 0})`;
          }
        });
      } catch (e) { console.error('lead counts error', e); }
    }
```

- [ ] **Step 3.6 — Open browser at `http://127.0.0.1:8000` and verify**

- GRANT tab is active (amber), VANTAGE tab is dim
- View chips show: Residential, Commercial, Held, Discarded
- Clicking VANTAGE switches to blue active, view chips become: Residential, Commercial, Spanish Res, Spanish Com, Held, Discarded
- Counts load on each brand switch

- [ ] **Step 3.7 — Commit**

```
git add dashboard/index.html
git commit -m "feat: add brand tabs to dashboard toolbar with ec/ng view chip sets"
```

---

## Task 4 — Frontend: column layout per brand

**Files:**
- Modify: `dashboard/index.html`

- [ ] **Step 4.1 — Add CSS for corporate badge and email dot**

After `.badge-blocked` CSS block, add:

```css
    .badge-corp {
      background: #2a1a00;
      color: #c8861e;
      border: 1px solid #7a5210;
      font-size: 9px;
      padding: 1px 5px;
      margin-left: 4px;
      vertical-align: middle;
    }

    .email-dot {
      display: inline-block;
      font-size: 10px;
      color: #3a8f5e;
      margin-left: 4px;
      vertical-align: middle;
    }
```

- [ ] **Step 4.2 — Add `isCorpName` helper to JS**

Add after the `esc()` utility function:

```javascript
    const _CORP_RE = /\b(LLC|INC|CORP|LTD|LP|LLP|PLLC|PROPERTIES|PROPERTY|MANAGEMENT|MGMT|REALTY|INVESTMENTS|HOLDINGS|TRUST|PARTNERS|GROUP|ENTERPRISES|VENTURES)\b/i;

    function isCorpName(name) {
      return _CORP_RE.test(name || '');
    }
```

- [ ] **Step 4.3 — Update `<thead>` to be dynamic per brand**

Replace the static `<thead>` block:

```html
      <thead id="leads-thead">
        <tr>
          <th class="col-check"><input type="checkbox" class="row-check" id="check-all"
              onchange="toggleAll(this.checked)" title="Select all visible"></th>
          <th class="col-case">Case #</th>
          <th class="col-tenant">Tenant</th>
          <th class="col-landlord">Landlord</th>
          <th class="col-address">Address</th>
          <th class="col-phone">Phone</th>
          <th class="col-dnc">DNC</th>
          <th class="col-extra" id="th-extra">Est. Rent</th>
          <th class="col-type">Type</th>
          <th class="col-date sortable" id="th-court" onclick="setSort('court_date')">Court Date</th>
          <th class="col-date sortable" id="th-filing" onclick="setSort('filing_date')">Filed</th>
          <th class="col-action">Action</th>
        </tr>
      </thead>
```

Add `col-extra` to the CSS (after `col-action`):

```css
    th.col-extra {
      width: 100px;
    }
```

- [ ] **Step 4.4 — Update `renderLeads` to use 12 cols and render swap column**

In `renderLeads`, change the two `colspan="11"` references to `colspan="12"`.

Then replace the row template in `tbody.innerHTML = leads.map(...)`:

```javascript
      tbody.innerHTML = leads.map((lead, i) => {
        const hasPhone = lead.phone && lead.phone !== 'None';
        const phone = hasPhone ? formatPhone(lead.phone) : '—';
        const dncStatus = lead.dnc_status || 'unknown';
        const dncClear = dncStatus === 'clear';
        const canManualClearDnc = hasPhone && !dncClear && dncStatus !== 'blocked' && !activeView.endsWith('_discarded');
        const type = lead.property_type || lead.lead_bucket || 'unknown';
        const isNg = activeBrand === 'ng';
        const language = lead.language_hint === 'spanish_likely' ? 'ES' : 'EN';
        const reason = lead.discard_reason || lead.qualification_notes || lead.lead_bucket || '';
        const courtDate = lead.court_date ? formatDate(lead.court_date) : '—';
        const filedDate = lead.filing_date ? formatDate(lead.filing_date) : '—';
        const isOverdue = lead.court_date && new Date(lead.court_date + 'T00:00:00') < today;
        const checked = selectedCases.has(lead.case_number) ? 'checked' : '';
        const selClass = selectedCases.has(lead.case_number) ? ' selected' : '';
        const isNew = lastRunAt && lead.scraped_at && lead.scraped_at >= lastRunAt;

        // Swap column: Est. Rent (EC) or Language badge (NG)
        const extraCell = isNg
          ? `<span class="badge ${language === 'ES' ? 'badge-commercial' : 'badge-unknown'}">${language}</span>`
          : (lead.estimated_rent ? `$${Math.round(lead.estimated_rent).toLocaleString()}` : '—');

        // Corporate badge for EC landlord
        const corpBadge = !isNg && isCorpName(lead.landlord_name)
          ? `<span class="badge-corp">CORP</span>`
          : '';

        // Email dot
        const emailDot = lead.email ? `<span class="email-dot" title="Has email">✉</span>` : '';

        return `<tr data-case="${esc(lead.case_number)}" data-idx="${i}" class="${selClass}">
        <td><input type="checkbox" class="row-check" ${checked} onchange="toggleRow('${esc(lead.case_number)}', this.checked)"></td>
        <td class="td-case">${esc(lead.case_number)}</td>
        <td>
          <div class="td-name">${esc(lead.tenant_name || '—')}${isNew ? ' <span style="background:#1a2d3a;color:#4a9bbf;font-size:9px;padding:1px 5px;vertical-align:middle;font-family:\'Barlow Condensed\',sans-serif;letter-spacing:.06em;">NEW</span>' : ''}</div>
          <div class="td-sub">${esc(lead.county || '')}, ${esc(lead.state || '')}</div>
        </td>
        <td>
          <div class="td-name">${esc(lead.landlord_name || '—')}${corpBadge}</div>
        </td>
        <td class="td-address">${esc(lead.property_address || '—')}
          <div class="td-sub">${esc(lead.property_zip || '')}${lead.property_zip ? ' - ' : ''}${esc(reason)}</div>
        </td>
        <td class="td-phone ${hasPhone ? 'has-phone' : 'no-phone'}">${phone}${emailDot}</td>
        <td><span class="badge badge-${dncClear ? 'residential' : dncStatus === 'blocked' ? 'blocked' : 'unknown'}">${esc(dncStatus)}</span></td>
        <td>${extraCell}</td>
        <td><span class="badge badge-${type}">${type}</span></td>
        <td class="td-date ${isOverdue ? 'overdue' : ''}">${courtDate}</td>
        <td class="td-date">${filedDate}</td>
        <td>
          <div class="actions" id="actions-${esc(lead.case_number)}">
            ${activeView.endsWith('_discarded')
            ? `<span class="action-status skipped">NO CALL</span>`
            : hasPhone && dncClear
              ? `<button class="btn-approve" onclick="approve('${esc(lead.case_number)}')">APPROVE</button>`
              : canManualClearDnc
                ? `<button class="btn-dnc" onclick="clearDnc('${esc(lead.case_number)}')">DNC CLEAR</button>`
                : `<span class="action-status skipped">${!hasPhone ? 'NO PHONE' : dncStatus === 'blocked' ? 'DNC BLOCKED' : 'DNC REVIEW'}</span>`
          }
            ${activeView.endsWith('_discarded')
            ? ''
            : `<button class="btn-skip" onclick="skip('${esc(lead.case_number)}')">SKIP</button>`
          }
          </div>
        </td>
      </tr>`;
      }).join('');
```

- [ ] **Step 4.5 — Update `th-extra` header when brand switches**

Add this line inside `setBrand`, after `_rebuildViewChips(brand)`:

```javascript
      const thExtra = document.getElementById('th-extra');
      if (thExtra) thExtra.textContent = brand === 'ng' ? 'Language' : 'Est. Rent';
```

- [ ] **Step 4.6 — Verify in browser**

- Grant tab: Landlord column shows CORP badge for business names (e.g. "WE TRAILS OWNER, LLC DBA THE TRAILS"), Est. Rent column header, email ✉ dot when lead has email
- Vantage tab: Language column shows ES (blue) or EN (gray) badge, no CORP badge, no Est. Rent
- NEW badge appears on leads scraped in the most recent run (may not appear yet until `lastRunAt` logic is added in Task 5)

- [ ] **Step 4.7 — Commit**

```
git add dashboard/index.html
git commit -m "feat: add per-brand column layout, corporate badge, email dot, language badge"
```

---

## Task 5 — Frontend: Ready to Call metric + NEW badge

**Files:**
- Modify: `dashboard/index.html`

- [ ] **Step 5.1 — Add Ready to Call metric element in metrics strip HTML**

In the metrics strip, insert a new metric div after the `m-pending` div:

```html
      <div class="metric" style="background: #080f0b;">
        <div class="metric-value green" id="m-ready">—</div>
        <div class="metric-label">Ready to Call</div>
      </div>
```

- [ ] **Step 5.2 — Add `updateReadyCount` function**

Add this function after `updateBulkBar`:

```javascript
    function updateReadyCount() {
      const n = visibleLeads.filter(l => l.phone && l.phone !== 'None' && l.dnc_status === 'clear').length;
      document.getElementById('m-ready').textContent = n;
    }
```

- [ ] **Step 5.3 — Call `updateReadyCount` from `applyFilters`**

At the end of `applyFilters`, add:

```javascript
      updateReadyCount();
```

- [ ] **Step 5.4 — Capture `lastRunAt` in `loadMetrics`**

In `loadMetrics`, after `document.getElementById('m-run-time').textContent = ...`, add:

```javascript
        lastRunAt = m.run_at || null;
```

- [ ] **Step 5.5 — Verify in browser**

- Ready to Call metric shows green count — should match the number of APPROVE buttons visible in the current view
- Clicking "DNC Clear" filter chip should drop the Ready count to the same number as the filtered rows with clear DNC
- After a refresh, NEW badges appear on rows whose `scraped_at >= lastRunAt` from the most recent metrics row

- [ ] **Step 5.6 — Commit**

```
git add dashboard/index.html
git commit -m "feat: add Ready to Call metric and NEW badge for latest scrape leads"
```

---

## Task 6 — Wiring check + bulk/keyboard updates

**Files:**
- Modify: `dashboard/index.html`

- [ ] **Step 6.1 — Fix bulk approve track param to use active brand**

In `bulkApprove`, replace the hardcoded `track=ec`:

```javascript
      const res = await fetch(`/api/leads/${encodeURIComponent(cn)}/approve?track=${activeBrand}`, { method: 'POST' });
```

In `bulkSkip`:
```javascript
      await fetch(`/api/leads/${encodeURIComponent(cn)}/skip?track=${activeBrand}`, { method: 'POST' });
```

- [ ] **Step 6.2 — Fix single approve/skip/clearDnc track params**

In `approve`:
```javascript
      const res = await fetch(`/api/leads/${encodeURIComponent(caseNumber)}/approve?track=${activeBrand}`, { method: 'POST' });
```

In `skip`:
```javascript
      await fetch(`/api/leads/${encodeURIComponent(caseNumber)}/skip?track=${activeBrand}`, { method: 'POST' });
```

In `clearDnc`:
```javascript
      const res = await fetch(`/api/leads/${encodeURIComponent(caseNumber)}/dnc-clear?track=${activeBrand}`, {
```

- [ ] **Step 6.3 — Fix `loadLeads` URL to drop legacy `view` values**

In `loadLeads`, verify the fetch already uses `activeView` which now holds `ec_residential` etc.:

```javascript
        const res = await fetch(`/api/leads?view=${encodeURIComponent(activeView)}`);
```

No change needed if it already uses `activeView`.

- [ ] **Step 6.4 — Run full pytest suite**

```
pytest -q
```

Expected: all tests pass.

- [ ] **Step 6.5 — Manual smoke test in browser**

Checklist:
- [ ] Load Grant > Residential — leads show with landlord phones, Est. Rent column, CORP badges on LLC names
- [ ] Switch to Vantage > Residential — leads reload, Language column shows, no CORP badges
- [ ] Switch to Vantage > Spanish Res — leads reload with Spanish-Likely language hint leads only
- [ ] Ready to Call count matches visible APPROVE buttons
- [ ] NEW badge appears on leads from last scrape run
- [ ] DNC Clear filter chip filters to clear-DNC leads only
- [ ] Overdue filter shows past-court-date leads only
- [ ] Approve a lead on Grant tab — confirm `track=ec` in network request
- [ ] Approve a lead on Vantage tab — confirm `track=ng` in network request

- [ ] **Step 6.6 — Final commit**

```
git add dashboard/index.html
git commit -m "feat: wire brand track to approve/skip/dnc-clear API calls"
```
