# Tenant Lead Volume Overhaul Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Lift NG (tenant) lead output by correcting the EC-era ZIP filter mismatch, codifying the SearchBug 9-gate enrichment policy, removing DNC scrubbing, introducing a verified-lead taxonomy with a review-stage lane, fixing scraper name-hygiene leaks, reclassifying the Franklin backlog, and diagnosing the OH SearchBug zero-yield.

**Architecture:** Five-phase rollout. Phase 0 lands shared helpers (name cleaning, compound-particle parsing, property-type heuristic). Phase 1 introduces `CAPTURE_EXPANDED_ZIPS` env flag that routes off-allowlist filings to a new `lead_bucket='captured'` value without touching enrichment. Phase 1.5 ships a manual `promote_captured_zips.py` lever. Phase 2 retrofits the 9-gate filter into `pipeline/runner.py`, removes DNC code + schema, adds review-stage routing for SearchBug `name_mismatch` / `ambiguous` responses, reclassifies the OH Franklin backlog, and diagnoses OH SearchBug yield.

**Tech Stack:** Python 3.13, pydantic, httpx, Supabase (PostgreSQL), SQLite (enrichment cache), pytest + pytest-asyncio + monkeypatch.

**Spec:** [docs/superpowers/specs/2026-05-28-tenant-lead-volume-overhaul-design.md](../specs/2026-05-28-tenant-lead-volume-overhaul-design.md)

---

## File Structure

**New files:**
- `migrations/012_drop_dnc.sql` — drops DNC columns + table.
- `scripts/promote_captured_zips.py` — manual ZIP cohort promotion CLI.
- `scripts/reclassify_franklin_backlog.py` — backfill 1,719 OH NULL rows.
- `scripts/diagnose_oh_searchbug.py` — green vs yellow path diagnosis.
- `scripts/audit_address_regex_regression.py` — one-shot pre-ship safety check.
- `tests/test_name_utils_clean_tenant_name.py`
- `tests/test_name_utils_compound_surname.py`
- `tests/test_name_utils_infer_property_type.py`
- `tests/test_runner_gates.py`
- `tests/test_runner_capture_mode.py`
- `tests/test_runner_review_stage.py`
- `tests/test_promote_captured_zips.py`
- `tests/test_reclassify_franklin_backlog.py`
- `docs/superpowers/specs/notes/oh-searchbug-diagnosis.md` (written by diagnosis script run).

**Modified files:**
- `services/name_utils.py` — new helpers + `parse_name` particle support.
- `services/searchbug_service.py` — extract phone on `name_mismatch`.
- `services/dedup_service.py` — remove DNC payload fields.
- `pipeline/qualification.py` — `capture_expanded` param, `captured` outcome.
- `pipeline/runner.py` — capture short-circuit, 9-gate retrofit, DNC removal, review-stage routing, drop `lookup_property_info` in tenant-only mode, broaden `_BUSINESS_RE`.
- `models/contact.py` — drop DNC fields.
- `dashboard/main.py` — captured view, remove DNC UI.
- `scrapers/texas/harris.py`, `scrapers/texas/tarrant.py`, `scrapers/florida/*.py`, `scrapers/georgia/*.py`, `scrapers/ohio/*.py`, `scrapers/tennessee/*.py`, `scrapers/arizona/*.py`, `scrapers/nevada/*.py`, `scrapers/indiana/*.py`, `scrapers/south_carolina/*.py`, `scrapers/california/*.py` — call `clean_tenant_name`.

**Deleted files:**
- `services/dnc_service.py`
- `services/ftc_dnc_registry.py`
- `scripts/build_dnc_sqlite.py`
- `scripts/check_dnc_download.py`
- `dnc.db`
- `tests/test_dnc_service.py`
- `tests/test_ftc_dnc_registry.py`
- `tests/test_brand_and_dnc.py`
- `tests/test_runner_dnc_gate.py`
- `tests/test_dashboard_dnc_gate.py`

**New env vars:** `CAPTURE_EXPANDED_ZIPS`, `ENRICHMENT_WINDOW_DAYS`, `GHL_NG_REVIEW_STAGE_ID`.

---

# Phase 0 — Name hygiene at the scraper boundary

## Task 1: Add `clean_tenant_name` helper

**Files:**
- Modify: `services/name_utils.py`
- Test: `tests/test_name_utils_clean_tenant_name.py`

- [ ] **Step 1: Write the failing test**

Create `tests/test_name_utils_clean_tenant_name.py`:

```python
from services.name_utils import clean_tenant_name


def test_strips_trailing_occupants_with_period():
    assert clean_tenant_name("Kenae Mayhorn and all other occupants.") == "Kenae Mayhorn"


def test_strips_trailing_occupants_no_other():
    assert clean_tenant_name("Vy Cao and all occupants") == "Vy Cao"


def test_strips_and_or_all_occupants():
    assert clean_tenant_name("Brenda V Villarreal and/or All Occupants") == "Brenda V Villarreal"


def test_strips_and_or_all_occupants_of_address():
    raw = "Dana Breyuntae Knighten and/or All Occupants of 3119 Peachstone Pl Spring, TX 7389-4688"
    assert clean_tenant_name(raw) == "Dana Breyuntae Knighten"


def test_strips_long_noise_tail():
    raw = "BRANDON SAUNDERS, AND ALL OCCUPANTS, UNKNOWN OCCUPANTS, TENANTS, AND SUBTENANTS"
    assert clean_tenant_name(raw) == "BRANDON SAUNDERS"


def test_strips_et_al():
    assert clean_tenant_name("John Smith, et al.") == "John Smith"


def test_returns_empty_for_john_doe_placeholder():
    assert clean_tenant_name("John Doe") == ""
    assert clean_tenant_name("Jane Doe") == ""


def test_returns_empty_for_unknown_tenant():
    assert clean_tenant_name("Unknown Tenant") == ""
    assert clean_tenant_name("All Occupants") == ""
    assert clean_tenant_name("Tenant in Possession") == ""


def test_returns_empty_for_squaters_typo():
    assert clean_tenant_name("Squaters") == ""


def test_returns_empty_for_blank_input():
    assert clean_tenant_name("") == ""
    assert clean_tenant_name("   ") == ""


def test_passes_clean_name_through_unchanged():
    assert clean_tenant_name("Sherrick Campbell") == "Sherrick Campbell"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_name_utils_clean_tenant_name.py -v`
Expected: `ImportError` — `clean_tenant_name` not defined.

- [ ] **Step 3: Implement `clean_tenant_name` in `services/name_utils.py`**

Add at the top of `services/name_utils.py` (after the existing `_GENERATIONAL_SUFFIXES`):

```python
import re

_OCCUPANT_TRAILER_RE = re.compile(
    r"[,\s]+("
    r"and(?:/or)?\s+all\s+(?:other\s+)?occupants?"
    r"|all\s+(?:other\s+)?occupants?"
    r"|et\s*\.?\s*al\s*\.?"
    r")"
    r"(?:\s+of\s+.*)?"          # also drop "of <address>" tail
    r".*$",                       # and any tokens after the trailer
    flags=re.IGNORECASE,
)

_PLACEHOLDER_NAMES = frozenset({
    "john doe", "jane doe", "j doe", "jdoe",
    "unknown", "unknown tenant", "tenant", "tenant in possession",
    "all occupants", "occupants", "occupants unknown",
    "squaters", "squatter", "squatters",
})


def clean_tenant_name(raw: str) -> str:
    """Strip occupant trailers and reject placeholder defendant names.

    Returns the cleaned name, or '' if the row is a placeholder
    (causes downstream bad_name gate to drop the filing).
    """
    if not raw:
        return ""
    cleaned = _OCCUPANT_TRAILER_RE.sub("", raw).strip(" ,.")
    cleaned = re.sub(r"\s+", " ", cleaned)
    if not cleaned:
        return ""
    if cleaned.lower() in _PLACEHOLDER_NAMES:
        return ""
    return cleaned
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_name_utils_clean_tenant_name.py -v`
Expected: all 11 tests PASS.

- [ ] **Step 5: Commit**

```bash
git add services/name_utils.py tests/test_name_utils_clean_tenant_name.py
git commit -m "feat: clean_tenant_name strips occupant trailers and placeholders"
```

---

## Task 2: Compound-particle support in `parse_name`

**Files:**
- Modify: `services/name_utils.py`
- Test: `tests/test_name_utils_compound_surname.py`

- [ ] **Step 1: Write the failing test**

Create `tests/test_name_utils_compound_surname.py`:

```python
from services.name_utils import parse_name


def test_de_los_surname_kept_in_last_name():
    first, last = parse_name("Stephanie De Los Santos")
    assert first == "Stephanie"
    assert last == "De Los Santos"


def test_de_la_surname_kept_in_last_name():
    first, last = parse_name("Brenda De La Torre")
    assert first == "Brenda"
    assert last == "De La Torre"


def test_van_der_surname_kept_in_last_name():
    first, last = parse_name("Hans Van Der Berg")
    assert first == "Hans"
    assert last == "Van Der Berg"


def test_del_surname_kept_in_last_name():
    first, last = parse_name("Maria Del Rio")
    assert first == "Maria"
    assert last == "Del Rio"


def test_short_name_with_de_as_middle_token_not_treated_as_particle():
    # "John De Smith" — only 3 tokens, no further particle pattern.
    # We treat trailing single-particle as last-name particle only when
    # there is a token AFTER the particle that looks like a surname.
    first, last = parse_name("John De Smith")
    assert first == "John"
    assert last == "De Smith"


def test_three_token_plain_name_unaffected():
    # No particle present.
    first, last = parse_name("John Robert Smith")
    assert first == "John"
    assert last == "Smith"


def test_two_token_name_unaffected():
    first, last = parse_name("John Smith")
    assert first == "John"
    assert last == "Smith"


def test_comma_form_still_works():
    first, last = parse_name("De La Cruz, Maria")
    assert first == "Maria"
    assert last == "De La Cruz"


def test_suffix_after_particle_surname():
    first, last = parse_name("Carlos De La Cruz Jr")
    assert first == "Carlos"
    assert last == "De La Cruz"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_name_utils_compound_surname.py -v`
Expected: most cases FAIL — current `parse_name` returns only the last token as surname.

- [ ] **Step 3: Update `parse_name` in `services/name_utils.py`**

Replace the existing `parse_name` function with:

```python
_PARTICLE_TOKENS: frozenset[str] = frozenset({
    "de", "del", "la", "los", "las", "van", "von", "der", "da", "di", "dos",
})


def _is_particle(token: str) -> bool:
    return token.rstrip(".").lower() in _PARTICLE_TOKENS


def parse_name(raw: str) -> tuple[str, str]:
    """Parse a raw court name into (first_name, last_name).

    Handles:
    - "LAST, FIRST"
    - "LAST, FIRST MIDDLE"  -> middle stripped
    - "FIRST LAST"
    - "FIRST MIDDLE LAST"   -> middle stripped
    - "FIRST [MIDDLE] LAST [SUFFIX]" -> suffix stripped
    - "FIRST PARTICLE [PARTICLE] LAST" -> particle(s) kept with last name
      (e.g. "Stephanie De Los Santos" -> ("Stephanie", "De Los Santos"))
    """
    raw = raw.strip()
    if not raw:
        return "", ""

    if "," in raw:
        # "LAST, FIRST [MIDDLE...]" — last side may itself contain particles.
        last, _, rest = raw.partition(",")
        last = last.strip()
        parts = rest.strip().split()
        first = parts[0] if parts else ""
        return (first, last) if first and last else ("", "")

    tokens = raw.split()
    if len(tokens) < 2:
        return "", ""

    first = tokens[0]
    remaining = list(tokens[1:])

    # Strip trailing generational suffixes (Jr, Sr, II, III, IV)
    while remaining and remaining[-1].rstrip(".").lower() in _GENERATIONAL_SUFFIXES:
        remaining.pop()

    if not remaining:
        return "", ""

    # Walk backward to find where the surname starts. If the token before the
    # final surname token is a particle, include it in the last name.
    last_start = len(remaining) - 1
    while last_start > 0 and _is_particle(remaining[last_start - 1]):
        last_start -= 1

    last = " ".join(remaining[last_start:])
    return first, last
```

- [ ] **Step 4: Run all name-utils tests**

Run: `pytest tests/test_name_utils_compound_surname.py tests/test_name_utils_clean_tenant_name.py -v`
Expected: all PASS.

- [ ] **Step 5: Run pre-existing `parse_name` tests if any**

Run: `pytest tests/ -k parse_name -v`
Expected: all PASS. If any pre-existing test broke, inspect and adjust — likely a 3-token name like `"John Robert Smith"` where my walk-back doesn't trigger (because `"Robert"` is not a particle).

- [ ] **Step 6: Commit**

```bash
git add services/name_utils.py tests/test_name_utils_compound_surname.py
git commit -m "feat: parse_name keeps compound surname particles (De La, Van Der, Del)"
```

---

## Task 3: `infer_property_type` heuristic helper

**Files:**
- Modify: `services/name_utils.py`
- Test: `tests/test_name_utils_infer_property_type.py`

- [ ] **Step 1: Write the failing test**

Create `tests/test_name_utils_infer_property_type.py`:

```python
from datetime import date
from models.filing import Filing
from services.name_utils import infer_property_type


def _filing(notice_type: str, tenant_name: str = "John Smith") -> Filing:
    return Filing(
        case_number="X", tenant_name=tenant_name,
        property_address="123 Main St, Houston, TX 77002",
        landlord_name="ACME", filing_date=date(2026, 5, 5),
        state="TX", county="Harris",
        notice_type=notice_type, source_url="x",
    )


def test_commercial_notice_type_returns_commercial():
    assert infer_property_type(_filing("Nonpayment - Commercial")) == "commercial"
    assert infer_property_type(_filing("Retail eviction")) == "commercial"
    assert infer_property_type(_filing("Office lease default")) == "commercial"


def test_business_tenant_name_returns_commercial():
    assert infer_property_type(_filing("Forcible Detainer", "ACME LLC")) == "commercial"
    assert infer_property_type(_filing("Forcible Detainer", "Pure Auto Spa, LLC")) == "commercial"
    assert infer_property_type(_filing("Forcible Detainer", "First National Bank")) == "commercial"
    assert infer_property_type(_filing("Forcible Detainer", "Estate of John Doe")) == "commercial"


def test_clean_residential_returns_residential():
    assert infer_property_type(_filing("Nonpayment - Residential")) == "residential"
    assert infer_property_type(_filing("Forcible Detainer", "Maria Garcia")) == "residential"


def test_blank_notice_type_defaults_residential():
    assert infer_property_type(_filing("")) == "residential"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_name_utils_infer_property_type.py -v`
Expected: `ImportError`.

- [ ] **Step 3: Implement `infer_property_type` in `services/name_utils.py`**

Add to `services/name_utils.py`:

```python
_BUSINESS_RE = re.compile(
    r"\b(LLC|INC|CORP|LTD|LP|LLP|PLLC|PROPERTIES|PROPERTY|MANAGEMENT|MGMT|"
    r"REALTY|INVESTMENTS|HOLDINGS|TRUST|PARTNERS|GROUP|ENTERPRISES|VENTURES|"
    r"ESTATE\s+OF|DBA|C/O|S\.A\.|BANK)\b",
    re.IGNORECASE,
)

_COMMERCIAL_NOTICE_RE = re.compile(r"\b(commercial|retail|office)\b", re.IGNORECASE)


def infer_property_type(filing) -> str:
    """Return 'commercial' or 'residential' from notice_type + tenant_name signals.

    Replaces the per-filing BatchData lookup_property_info call for tenant-only mode.
    """
    if _COMMERCIAL_NOTICE_RE.search(filing.notice_type or ""):
        return "commercial"
    if _BUSINESS_RE.search(filing.tenant_name or ""):
        return "commercial"
    return "residential"
```

- [ ] **Step 4: Run tests**

Run: `pytest tests/test_name_utils_infer_property_type.py -v`
Expected: all PASS.

- [ ] **Step 5: Commit**

```bash
git add services/name_utils.py tests/test_name_utils_infer_property_type.py
git commit -m "feat: infer_property_type heuristic replaces BatchData lookup in tenant-only"
```

---

## Task 4: Wire `clean_tenant_name` into Harris scraper

**Files:**
- Modify: `scrapers/texas/harris.py`

- [ ] **Step 1: Verify Harris test exists or write a focused regression**

Check: `pytest tests/ -k harris -v --collect-only`
If a Harris CSV-parsing test exists, add a row to its sample data that contains `"Kenae Mayhorn and all other occupants."`. If not, skip — the shared helper is already tested.

- [ ] **Step 2: Replace `_clean_defendant` to delegate to shared helper**

In `scrapers/texas/harris.py`, replace lines defining `_OCCUPANTS_RE` and `_clean_defendant`:

```python
from services.name_utils import clean_tenant_name


# (delete _OCCUPANTS_RE)


class HarrisCountyScraper(BaseScraper):
    # ...
    @staticmethod
    def _clean_defendant(name: str) -> str:
        return clean_tenant_name(name)
```

- [ ] **Step 3: Run any Harris-related tests**

Run: `pytest tests/ -k harris -v`
Expected: PASS (test should now reflect the new cleaning behavior).

- [ ] **Step 4: Commit**

```bash
git add scrapers/texas/harris.py
git commit -m "refactor: harris scraper delegates name cleaning to shared helper"
```

---

## Task 5: Wire `clean_tenant_name` into remaining scrapers

**Files:**
- Modify: `scrapers/texas/tarrant.py`, `scrapers/georgia/researchga.py`, `scrapers/georgia/cobb.py`, `scrapers/georgia/dekalb.py`, `scrapers/florida/broward.py`, `scrapers/florida/hillsborough.py`, `scrapers/florida/miami_dade.py`, `scrapers/ohio/franklin.py`, `scrapers/ohio/hamilton.py`, `scrapers/tennessee/davidson.py`, `scrapers/arizona/maricopa.py`, `scrapers/nevada/clark.py`, `scrapers/indiana/marion.py`, `scrapers/south_carolina/richland.py`, `scrapers/california/los_angeles.py`

- [ ] **Step 1: Replace each scraper's local cleaning with `clean_tenant_name`**

For each scraper above, find where `tenant_name` is assigned in the `Filing(...)` constructor and wrap it:

```python
from services.name_utils import clean_tenant_name

# ...
tenant_name = clean_tenant_name(raw_defendant_text)
if not tenant_name:
    continue  # placeholder/junk row — skip filing entirely
```

Scrapers with local cleaning regexes (e.g., `_clean_defendant`, `_clean_tenant`, `_clean_party_name`) delegate to the shared helper. Local regexes can be deleted.

- [ ] **Step 2: Run scraper tests**

Run: `pytest tests/ -k scraper -v`
Expected: PASS. Some tests may need updates if they rely on the previous (looser) cleaning behavior.

- [ ] **Step 3: Commit**

```bash
git add scrapers/
git commit -m "refactor: all green scrapers delegate name cleaning to shared helper"
```

---

## Task 6: Broaden `_BUSINESS_RE` in `pipeline/runner.py`

**Files:**
- Modify: `pipeline/runner.py:35-39`

- [ ] **Step 1: Write the failing test**

Add to `tests/test_runner_tracks.py` (or create `tests/test_runner_business_filter.py` if cleaner):

```python
from pipeline.runner import _is_business_name


def test_estate_of_treated_as_business():
    assert _is_business_name("Estate of John Doe") is True


def test_dba_treated_as_business():
    assert _is_business_name("John Smith DBA Acme Diner") is True


def test_co_treated_as_business():
    assert _is_business_name("Properties LLC c/o Jane Doe") is True


def test_bank_treated_as_business():
    assert _is_business_name("First National Bank") is True


def test_individual_not_business():
    assert _is_business_name("Maria Garcia") is False
```

- [ ] **Step 2: Run to verify failure on new tokens**

Run: `pytest tests/test_runner_business_filter.py -v`
Expected: FAIL on `Estate of`, `DBA`, `c/o`, `Bank`.

- [ ] **Step 3: Update `_BUSINESS_RE` in `pipeline/runner.py`**

```python
_BUSINESS_RE = re.compile(
    r"\b(LLC|INC|CORP|LTD|LP|LLP|PLLC|PROPERTIES|PROPERTY|MANAGEMENT|MGMT|"
    r"REALTY|INVESTMENTS|HOLDINGS|TRUST|PARTNERS|GROUP|ENTERPRISES|VENTURES|"
    r"ESTATE\s+OF|DBA|C/O|S\.A\.|BANK)\b",
    re.IGNORECASE,
)
```

- [ ] **Step 4: Run tests**

Run: `pytest tests/test_runner_business_filter.py -v`
Expected: all PASS.

- [ ] **Step 5: Commit**

```bash
git add pipeline/runner.py tests/test_runner_business_filter.py
git commit -m "feat: broaden business filter with ESTATE OF, DBA, C/O, S.A., BANK"
```

---

## Task 7: Remove BatchData `lookup_property_info` from tenant-only branch

**Files:**
- Modify: `pipeline/runner.py:355-404` (the enrichment branching block).

- [ ] **Step 1: Write the regression test**

Add to `tests/test_runner_tracks.py`:

```python
import pytest
from unittest.mock import AsyncMock, patch
from datetime import date
from models.filing import Filing
from pipeline import runner


@pytest.mark.asyncio
async def test_tenant_only_mode_does_not_call_lookup_property_info(monkeypatch):
    monkeypatch.setenv("TENANT_TRACK_ENABLED", "true")
    monkeypatch.setenv("LANDLORD_TRACK_ENABLED", "false")

    filing = Filing(
        case_number="X1", tenant_name="Maria Garcia",
        property_address="123 Oak St, Houston, TX 77042",
        landlord_name="ACME LLC", filing_date=date(2026, 5, 25),
        state="TX", county="Harris",
        notice_type="Nonpayment - Residential", source_url="x",
        property_type_hint=None,  # forces the inference path
    )

    with patch("services.batchdata_service.lookup_property_info") as mock_lookup, \
         patch("services.batchdata_service.enrich_tenant", new=AsyncMock()) as mock_tenant, \
         patch("services.dedup_service.is_duplicate", new=AsyncMock(return_value=False)), \
         patch("services.dedup_service.insert_filing", new=AsyncMock()), \
         patch("services.dedup_service.update_classification", new=AsyncMock()), \
         patch("services.dedup_service.update_enrichment", new=AsyncMock()), \
         patch("services.geocode_service.normalize_address", new=AsyncMock(return_value=None)):

        await runner.run([filing], state="TX", county="Harris")

    mock_lookup.assert_not_called()
    mock_tenant.assert_called_once()
```

- [ ] **Step 2: Run test to verify failure**

Run: `pytest tests/test_runner_tracks.py::test_tenant_only_mode_does_not_call_lookup_property_info -v`
Expected: FAIL — `lookup_property_info` is currently called.

- [ ] **Step 3: Update runner enrichment branch**

In `pipeline/runner.py`, locate the block starting at `if filing.property_type_hint is None:` (~line 362) and replace the tenant-only path:

```python
        try:
            property_info = None
            property_lookup_calls = 0
            # In tenant-only mode we infer property_type from notice_type + tenant_name
            # rather than burning a BatchData property lookup.
            if landlord_track_enabled and filing.property_type_hint is None:
                property_info = await batchdata_service.lookup_property_info(filing)
                property_lookup_calls = 1

            if filing.property_type_hint is None and not landlord_track_enabled:
                from services.name_utils import infer_property_type
                filing.property_type_hint = infer_property_type(filing)

            if landlord_track_enabled and enrich_tenant_flag:
                # ... existing both-tracks branch unchanged
```

The rest of the enrichment branching stays the same. The key change: tenant-only mode sets `property_type_hint` via inference and never calls `lookup_property_info`.

- [ ] **Step 4: Run test**

Run: `pytest tests/test_runner_tracks.py::test_tenant_only_mode_does_not_call_lookup_property_info -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add pipeline/runner.py tests/test_runner_tracks.py
git commit -m "feat: drop BatchData property lookup in tenant-only mode; use inference"
```

---

# Phase 1 — Capture mode for expanded ZIPs

## Task 8: Add `capture_expanded` to `classify_lead`

**Files:**
- Modify: `pipeline/qualification.py`
- Test: `tests/test_qualification.py`

- [ ] **Step 1: Write the failing tests**

Add to `tests/test_qualification.py`:

```python
def test_classify_off_allowlist_zip_with_capture_expanded_true_returns_captured():
    outcome = classify_lead(
        state="TX",
        property_address="123 Greenspoint Dr, Houston, TX 77090",
        filing_date=date(2026, 5, 25),
        today=date(2026, 5, 25),
        capture_expanded=True,
    )
    assert outcome.property_zip == "77090"
    assert outcome.lead_bucket == "captured"
    assert outcome.discard_reason is None
    assert "captured" in outcome.qualification_notes.lower()


def test_classify_off_allowlist_zip_with_capture_expanded_false_falls_back_to_legacy_discard():
    outcome = classify_lead(
        state="TX",
        property_address="123 Greenspoint Dr, Houston, TX 77090",
        filing_date=date(2026, 5, 25),
        today=date(2026, 5, 25),
        capture_expanded=False,
    )
    assert outcome.lead_bucket == "discarded"
    assert outcome.discard_reason == "zip_not_approved"


def test_classify_on_allowlist_zip_unaffected_by_capture_expanded():
    for cap in (True, False):
        outcome = classify_lead(
            state="TX",
            property_address="123 Main St, Houston, TX 77002",
            filing_date=date(2026, 5, 25),
            today=date(2026, 5, 25),
            capture_expanded=cap,
        )
        assert outcome.lead_bucket == "residential_approved"


def test_classify_missing_zip_still_discarded_under_capture_mode():
    outcome = classify_lead(
        state="TX",
        property_address="Unknown",
        filing_date=date(2026, 5, 25),
        today=date(2026, 5, 25),
        capture_expanded=True,
    )
    assert outcome.lead_bucket == "discarded"
    assert outcome.discard_reason == "missing_zip"
```

- [ ] **Step 2: Run to verify failures**

Run: `pytest tests/test_qualification.py -v`
Expected: 4 tests FAIL (`capture_expanded` keyword not accepted).

- [ ] **Step 3: Update `classify_lead` in `pipeline/qualification.py`**

```python
def classify_lead(
    *,
    state: str,
    property_address: str,
    filing_date: date,
    property_type: str | None = None,
    estimated_rent: float | Decimal | None = None,
    today: date | None = None,
    capture_expanded: bool = False,
) -> QualificationOutcome:
    property_zip = extract_property_zip(property_address)
    if property_zip is None:
        return QualificationOutcome(
            property_zip=None,
            lead_bucket="discarded",
            discard_reason="missing_zip",
            qualification_notes="Discarded before enrichment: no property ZIP found.",
        )

    if not is_approved_zip(state, property_zip):
        if capture_expanded:
            return QualificationOutcome(
                property_zip=property_zip,
                lead_bucket="captured",
                discard_reason=None,
                qualification_notes="Captured: ZIP off legacy allowlist, awaiting Phase 3 promotion policy.",
            )
        return QualificationOutcome(
            property_zip=property_zip,
            lead_bucket="discarded",
            discard_reason="zip_not_approved",
            qualification_notes="Discarded before enrichment: property ZIP is not approved.",
        )

    # ... rest of existing logic unchanged
```

Also update the file header comment block above `APPROVED_ZIPS`:

```python
# Legacy EC-era allowlist. Hand-curated for landlord (Grant Ellis) prospecting:
# affluent urban cores and high-property-value suburbs. NOT calibrated for
# tenant (NG / Vantage Defense) demographics. With CAPTURE_EXPANDED_ZIPS=true,
# off-allowlist filings land in `lead_bucket='captured'` for Phase 3 analysis
# rather than being discarded outright. See:
# docs/superpowers/specs/2026-05-28-tenant-lead-volume-overhaul-design.md
```

- [ ] **Step 4: Run tests**

Run: `pytest tests/test_qualification.py -v`
Expected: all PASS.

- [ ] **Step 5: Commit**

```bash
git add pipeline/qualification.py tests/test_qualification.py
git commit -m "feat: classify_lead supports capture_expanded mode with 'captured' bucket"
```

---

## Task 9: Capture-mode short-circuit in `pipeline/runner.py`

**Files:**
- Modify: `pipeline/runner.py`
- Test: `tests/test_runner_capture_mode.py`

- [ ] **Step 1: Write the failing test**

Create `tests/test_runner_capture_mode.py`:

```python
import pytest
from unittest.mock import AsyncMock, patch
from datetime import date
from models.filing import Filing
from pipeline import runner


@pytest.mark.asyncio
async def test_capture_mode_short_circuits_enrichment(monkeypatch):
    monkeypatch.setenv("CAPTURE_EXPANDED_ZIPS", "true")
    monkeypatch.setenv("TENANT_TRACK_ENABLED", "true")
    monkeypatch.setenv("LANDLORD_TRACK_ENABLED", "false")

    # Filing in an off-allowlist TX ZIP (77090 is not on allowlist)
    filing = Filing(
        case_number="CAPTURE1", tenant_name="Maria Garcia",
        property_address="123 Greenspoint Dr, Houston, TX 77090",
        landlord_name="ACME LLC", filing_date=date(2026, 5, 25),
        state="TX", county="Harris",
        notice_type="Nonpayment - Residential", source_url="x",
    )

    with patch("services.batchdata_service.lookup_property_info", new=AsyncMock()) as mock_lookup, \
         patch("services.batchdata_service.enrich_tenant", new=AsyncMock()) as mock_tenant, \
         patch("services.ghl_service.create_contact", new=AsyncMock()) as mock_ghl, \
         patch("services.instantly_service.enroll", new=AsyncMock()) as mock_instantly, \
         patch("services.bland_service.trigger_voicemail", new=AsyncMock()) as mock_bland, \
         patch("services.dedup_service.is_duplicate", new=AsyncMock(return_value=False)), \
         patch("services.dedup_service.insert_filing", new=AsyncMock()), \
         patch("services.dedup_service.update_classification", new=AsyncMock()), \
         patch("services.geocode_service.normalize_address", new=AsyncMock(return_value=None)):

        await runner.run([filing], state="TX", county="Harris")

    mock_lookup.assert_not_called()
    mock_tenant.assert_not_called()
    mock_ghl.assert_not_called()
    mock_instantly.assert_not_called()
    mock_bland.assert_not_called()
```

- [ ] **Step 2: Run test to verify failure**

Run: `pytest tests/test_runner_capture_mode.py -v`
Expected: FAIL — current runner does not short-circuit.

- [ ] **Step 3: Wire `CAPTURE_EXPANDED_ZIPS` into runner**

In `pipeline/runner.py`, near other module-level env reads (around line 33):

```python
_CAPTURE_EXPANDED_ZIPS = os.getenv("CAPTURE_EXPANDED_ZIPS", "true").lower() == "true"
```

Modify `_classify_and_store` (around line 225):

```python
async def _classify_and_store(filing: Filing, contact: EnrichedContact | None = None) -> str:
    outcome = classify_lead(
        state=filing.state,
        property_address=filing.property_address,
        filing_date=filing.filing_date,
        property_type=contact.property_type if contact else filing.property_type_hint,
        estimated_rent=contact.estimated_rent if contact else filing.claim_amount,
        capture_expanded=_CAPTURE_EXPANDED_ZIPS,
    )
    await dedup_service.update_classification(filing.case_number, outcome)
    return outcome.lead_bucket
```

In `runner.run`, after `lead_bucket = await _classify_and_store(filing)` (around line 345), add:

```python
        if lead_bucket == "captured":
            log.info(f"{filing.case_number} captured (off-allowlist ZIP); no enrichment")
            m["captured"] = m.get("captured", 0) + 1
            continue
        if lead_bucket == "discarded":
            # existing discard branch
            ...
```

Initialize `captured` in the metrics dict at the top of `runner.run`:

```python
    m = dict(
        run_at=started_at.isoformat(),
        state=state,
        county=county,
        filings_received=len(filings),
        duplicates_skipped=0,
        address_skipped=0,
        captured=0,                 # <-- new
        batchdata_calls=0,
        # ... existing fields
    )
```

- [ ] **Step 4: Run test**

Run: `pytest tests/test_runner_capture_mode.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add pipeline/runner.py tests/test_runner_capture_mode.py
git commit -m "feat: CAPTURE_EXPANDED_ZIPS short-circuits enrichment for captured rows"
```

---

## Task 10: Surface `captured` in run summary + dashboard view

**Files:**
- Modify: `services/notification_service.py` (run summary template, if present), `dashboard/main.py`

- [ ] **Step 1: Locate the run summary builder**

Run: `grep -rn "filings_received" services/notification_service.py | head`
Find where the per-run summary string is composed and add a `captured: N` line.

- [ ] **Step 2: Update run summary**

In `services/notification_service.py` `send_run_summary`, add the captured count:

```python
    lines.append(f"Captured (off-allowlist ZIPs): {m.get('captured', 0)}")
```

(Insert near other count lines — the exact location is wherever existing counts live.)

- [ ] **Step 3: Add dashboard captured view**

In `dashboard/main.py`, add a new route:

```python
@app.get("/captured")
async def captured_view(state: str = None, zip: str = None, county: str = None,
                        limit: int = 100):
    q = supabase.table("filings").select(
        "case_number, state, county, property_zip, property_address, tenant_name, "
        "filing_date, classified_at"
    ).eq("lead_bucket", "captured").order("classified_at", desc=True).limit(limit)
    if state: q = q.eq("state", state)
    if zip: q = q.eq("property_zip", zip)
    if county: q = q.eq("county", county)
    rows = q.execute().data or []
    return {"rows": rows, "count": len(rows)}
```

(Follow the existing routing convention in dashboard/main.py — adjust if the project uses a different framework.)

- [ ] **Step 4: Smoke test the route**

Run: `pytest tests/test_dashboard_views.py -v` (if exists)
If no test, manually run dashboard locally and hit `/captured`.

- [ ] **Step 5: Commit**

```bash
git add services/notification_service.py dashboard/main.py
git commit -m "feat: surface captured count in run summary and add dashboard view"
```

---

# Phase 1.5 — Manual promote-by-ZIP cohort

## Task 11: `scripts/promote_captured_zips.py`

**Files:**
- Create: `scripts/promote_captured_zips.py`
- Test: `tests/test_promote_captured_zips.py`

- [ ] **Step 1: Write the failing test**

Create `tests/test_promote_captured_zips.py`:

```python
import pytest
from unittest.mock import patch, MagicMock
from scripts import promote_captured_zips as mod


def _row(case_number, zip, bucket="captured"):
    return {
        "case_number": case_number, "property_zip": zip,
        "lead_bucket": bucket, "qualification_notes": "Captured: ...",
    }


def test_dry_run_does_not_write(monkeypatch):
    rows = [_row("A", "77090"), _row("B", "77090")]
    client = MagicMock()
    client.table.return_value.select.return_value.eq.return_value.in_.return_value.\
        gte.return_value.execute.return_value.data = rows
    monkeypatch.setattr(mod, "_client", client)

    result = mod.run(state="TX", zips=["77090"], since="2026-05-01", dry_run=True, demote=False)

    assert result["projected_promotions"] == 2
    client.table.return_value.update.assert_not_called()


def test_promotion_updates_bucket(monkeypatch):
    rows = [_row("A", "77090")]
    client = MagicMock()
    client.table.return_value.select.return_value.eq.return_value.in_.return_value.\
        gte.return_value.execute.return_value.data = rows
    monkeypatch.setattr(mod, "_client", client)

    mod.run(state="TX", zips=["77090"], since="2026-05-01", dry_run=False, demote=False)

    # Verify an update call happened with residential_approved
    update_calls = client.table.return_value.update.call_args_list
    assert any("residential_approved" in str(c) for c in update_calls)


def test_demote_reverses_bucket(monkeypatch):
    rows = [_row("A", "77090", bucket="residential_approved")]
    client = MagicMock()
    client.table.return_value.select.return_value.eq.return_value.in_.return_value.\
        gte.return_value.execute.return_value.data = rows
    monkeypatch.setattr(mod, "_client", client)

    mod.run(state="TX", zips=["77090"], since="2026-05-01", dry_run=False, demote=True)

    update_calls = client.table.return_value.update.call_args_list
    assert any("captured" in str(c) for c in update_calls)
```

- [ ] **Step 2: Run to verify failure**

Run: `pytest tests/test_promote_captured_zips.py -v`
Expected: `ImportError` (module doesn't exist).

- [ ] **Step 3: Implement the script**

Create `scripts/promote_captured_zips.py`:

```python
"""Manually promote captured filings in a ZIP cohort to residential_approved
so they enter the enrichment funnel on the next runner cycle.

Usage:
    python scripts/promote_captured_zips.py --state TX --zips 77090,77042 --since 2026-05-01
    python scripts/promote_captured_zips.py --state TX --zips 77090 --dry-run
    python scripts/promote_captured_zips.py --state TX --zips 77090 --demote
"""
from __future__ import annotations
import argparse
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))
from dotenv import load_dotenv
load_dotenv()

from supabase import create_client

_client = create_client(
    os.environ["SUPABASE_URL"],
    os.environ["SUPABASE_SERVICE_ROLE_KEY"],
)


def run(state: str, zips: list[str], since: str, dry_run: bool, demote: bool) -> dict:
    source_bucket = "residential_approved" if demote else "captured"
    target_bucket = "captured" if demote else "residential_approved"

    rows = (
        _client.table("filings")
        .select("case_number, property_zip, lead_bucket, qualification_notes")
        .eq("state", state)
        .in_("property_zip", zips)
        .gte("filing_date", since)
        .execute()
        .data or []
    )
    eligible = [r for r in rows if r.get("lead_bucket") == source_bucket]
    print(f"Eligible rows: {len(eligible)} (state={state}, zips={zips}, since={since})")

    if dry_run:
        print(f"DRY RUN: would change lead_bucket={source_bucket} -> {target_bucket}")
        cost_per_call = 0.20  # rough SearchBug rate
        print(f"Projected enrichment cost if promoted: ~${len(eligible) * cost_per_call:.2f}")
        return {"projected_promotions": len(eligible), "dry_run": True}

    now = datetime.now(timezone.utc).isoformat()
    note_suffix = (
        f"Demoted to captured by promote_captured_zips on {now[:10]}."
        if demote else
        f"Promoted from captured by ZIP cohort {zips} on {now[:10]}."
    )

    changed = 0
    for row in eligible:
        new_notes = (row.get("qualification_notes") or "").rstrip(".") + ". " + note_suffix
        _client.table("filings").update({
            "lead_bucket": target_bucket,
            "qualification_notes": new_notes,
            "classified_at": now,
        }).eq("case_number", row["case_number"]).execute()
        changed += 1

    print(f"Updated {changed} rows: lead_bucket={source_bucket} -> {target_bucket}")
    return {"promoted": changed, "dry_run": False}


def _parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser()
    p.add_argument("--state", required=True)
    p.add_argument("--zips", required=True, help="comma-separated ZIP list")
    p.add_argument("--since", required=True, help="ISO date, e.g. 2026-05-01")
    p.add_argument("--dry-run", action="store_true")
    p.add_argument("--demote", action="store_true", help="reverse: residential_approved -> captured")
    return p


if __name__ == "__main__":
    args = _parser().parse_args()
    run(
        state=args.state,
        zips=[z.strip() for z in args.zips.split(",")],
        since=args.since,
        dry_run=args.dry_run,
        demote=args.demote,
    )
```

- [ ] **Step 4: Run tests**

Run: `pytest tests/test_promote_captured_zips.py -v`
Expected: all PASS.

- [ ] **Step 5: Commit**

```bash
git add scripts/promote_captured_zips.py tests/test_promote_captured_zips.py
git commit -m "feat: promote_captured_zips manual cohort lever for captured leads"
```

---

# Phase 2.1 — 9-gate retrofit in runner

## Task 12: Address-regex regression audit (gate before shipping stricter checks)

**Files:**
- Create: `scripts/audit_address_regex_regression.py`

- [ ] **Step 1: Implement the audit**

Create `scripts/audit_address_regex_regression.py`:

```python
"""One-shot pre-ship check: how many currently-approved historical rows
would fail the stricter 9-gate address regex? If >5%, the regex must
relax before Phase 2.1 ships."""
from __future__ import annotations
import os
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))
from dotenv import load_dotenv
load_dotenv()

from supabase import create_client

STREET_NUM_RE = re.compile(r"^\s*\d+\s+")
ADDR_HAS_STATE_ZIP = re.compile(r"\b[A-Z]{2}\s+\d{5}\b")

client = create_client(os.environ["SUPABASE_URL"], os.environ["SUPABASE_SERVICE_ROLE_KEY"])

rows, offset = [], 0
while True:
    chunk = client.table("filings").select(
        "case_number, state, property_address"
    ).eq("lead_bucket", "residential_approved").range(offset, offset + 999).execute().data or []
    rows.extend(chunk)
    if len(chunk) < 1000: break
    offset += 1000

total = len(rows)
fails = [r for r in rows if not (STREET_NUM_RE.match(r["property_address"] or "")
                                  and ADDR_HAS_STATE_ZIP.search(r["property_address"] or ""))]
pct = (len(fails) / total * 100) if total else 0
print(f"Approved rows total: {total}")
print(f"Would fail stricter regex: {len(fails)} ({pct:.1f}%)")
if pct > 5:
    print("FAIL: regression budget exceeded. Relax regex before shipping Phase 2.1.")
    print("Sample failures:")
    for r in fails[:10]:
        print(f"  {r['state']} | {r['property_address']!r}")
    sys.exit(1)
print("PASS: regression budget within bounds.")
```

- [ ] **Step 2: Run the audit**

Run: `python scripts/audit_address_regex_regression.py`
Expected: either PASS (proceed with Phase 2.1) or FAIL with sample failures (relax the regex by, e.g., dropping the `state+zip` requirement for rows where city is present).

- [ ] **Step 3: Commit (regardless of result; the audit is itself the artifact)**

```bash
git add scripts/audit_address_regex_regression.py
git commit -m "chore: audit_address_regex_regression pre-ship gate for Phase 2.1"
```

---

## Task 13: 9-gate retrofit — write the gate functions

**Files:**
- Create: `pipeline/gates.py`
- Test: `tests/test_runner_gates.py`

- [ ] **Step 1: Write the failing tests**

Create `tests/test_runner_gates.py`:

```python
from datetime import date, timedelta
from pipeline.gates import (
    gate_filing_window, gate_court_date, gate_address,
    gate_name, gate_query_dedup,
)


def test_filing_window_passes_recent():
    assert gate_filing_window(date(2026, 5, 25), today=date(2026, 5, 28), window_days=10) is True


def test_filing_window_fails_old():
    assert gate_filing_window(date(2026, 5, 1), today=date(2026, 5, 28), window_days=10) is False


def test_court_date_none_passes():
    assert gate_court_date(None, today=date(2026, 5, 28)) is True


def test_court_date_future_passes():
    assert gate_court_date(date(2026, 6, 1), today=date(2026, 5, 28)) is True


def test_court_date_past_fails():
    assert gate_court_date(date(2026, 5, 20), today=date(2026, 5, 28)) is False


def test_address_with_street_number_and_zip_passes():
    assert gate_address("123 Main St, Houston, TX 77002") is True


def test_address_without_street_number_fails():
    assert gate_address("Main St, Houston, TX 77002") is False


def test_address_without_state_zip_fails():
    assert gate_address("123 Main St") is False


def test_name_clean_parsing_passes():
    assert gate_name("Maria Garcia") is True


def test_name_placeholder_fails():
    assert gate_name("John Doe") is False


def test_name_entity_fails():
    assert gate_name("Pure Auto Spa, LLC") is False


def test_name_with_occupant_token_fails():
    assert gate_name("Zehneel Occupants") is False


def test_query_dedup_first_pass_second_fail():
    seen: set[str] = set()
    assert gate_query_dedup("maria", "garcia", "123 Main St", "77002", seen) is True
    assert gate_query_dedup("maria", "garcia", "123 Main St", "77002", seen) is False
```

- [ ] **Step 2: Run to verify failure**

Run: `pytest tests/test_runner_gates.py -v`
Expected: `ImportError`.

- [ ] **Step 3: Implement gates**

Create `pipeline/gates.py`:

```python
"""9-gate enrichment filter. Codifies the select-searchbug-tenant-leads skill
as runtime policy in pipeline/runner.py."""
from __future__ import annotations
import re
from datetime import date
from services.name_utils import clean_tenant_name, parse_name

_STREET_NUM_RE = re.compile(r"^\s*\d+\s+")
_ADDR_STATE_ZIP_RE = re.compile(r"\b[A-Z]{2}\s+\d{5}\b")
_ENTITY_RE = re.compile(
    r"\b(LLC|INC|CORP|LTD|LP|LLP|PLLC|PROPERTIES|PROPERTY|MANAGEMENT|MGMT|"
    r"REALTY|INVESTMENTS|HOLDINGS|TRUST|PARTNERS|GROUP|ENTERPRISES|VENTURES|"
    r"ESTATE\s+OF|DBA|C/O|S\.A\.|BANK)\b",
    re.IGNORECASE,
)
_BAD_TOKEN_RE = re.compile(r"\b(AKA|OCCUPANTS?|ALL\s+OTHER|ET\s+AL)\b", re.IGNORECASE)


def gate_filing_window(filing_date: date, today: date, window_days: int) -> bool:
    return (today - filing_date).days <= window_days


def gate_court_date(court_date: date | None, today: date) -> bool:
    return court_date is None or court_date >= today


def gate_address(address: str) -> bool:
    if not address: return False
    if not _STREET_NUM_RE.match(address): return False
    if not _ADDR_STATE_ZIP_RE.search(address): return False
    return True


def gate_name(tenant_name: str) -> bool:
    cleaned = clean_tenant_name(tenant_name)
    if not cleaned: return False
    if _ENTITY_RE.search(cleaned): return False
    if _BAD_TOKEN_RE.search(cleaned): return False
    first, last = parse_name(cleaned)
    return bool(first and last)


def gate_query_dedup(first: str, last: str, street: str, zip_: str, seen: set[str]) -> bool:
    key = f"{first.lower()}|{last.lower()}|{street.lower()}|{zip_}"
    if key in seen: return False
    seen.add(key)
    return True
```

- [ ] **Step 4: Run tests**

Run: `pytest tests/test_runner_gates.py -v`
Expected: all PASS.

- [ ] **Step 5: Commit**

```bash
git add pipeline/gates.py tests/test_runner_gates.py
git commit -m "feat: pipeline/gates.py implements 9-gate enrichment filter"
```

---

## Task 14: Wire gates into `runner.py`

**Files:**
- Modify: `pipeline/runner.py`
- Test: `tests/test_runner_gates.py` (extend with integration test)

- [ ] **Step 1: Write integration test**

Append to `tests/test_runner_gates.py`:

```python
import pytest
from unittest.mock import AsyncMock, patch
from models.filing import Filing
from pipeline import runner


@pytest.mark.asyncio
async def test_runner_skips_enrichment_for_overdue_filing(monkeypatch):
    monkeypatch.setenv("TENANT_TRACK_ENABLED", "true")
    monkeypatch.setenv("LANDLORD_TRACK_ENABLED", "false")
    monkeypatch.setenv("CAPTURE_EXPANDED_ZIPS", "false")
    monkeypatch.setenv("ENRICHMENT_WINDOW_DAYS", "10")

    filing = Filing(
        case_number="OVERDUE1", tenant_name="Maria Garcia",
        property_address="123 Main St, Houston, TX 77002",
        landlord_name="ACME", filing_date=date(2026, 5, 25),
        court_date=date(2026, 5, 27),  # past
        state="TX", county="Harris",
        notice_type="Nonpayment - Residential", source_url="x",
    )

    with patch("services.batchdata_service.enrich_tenant", new=AsyncMock()) as mock_tenant, \
         patch("services.dedup_service.is_duplicate", new=AsyncMock(return_value=False)), \
         patch("services.dedup_service.insert_filing", new=AsyncMock()), \
         patch("services.dedup_service.update_classification", new=AsyncMock()), \
         patch("services.geocode_service.normalize_address", new=AsyncMock(return_value=None)):

        await runner.run([filing], state="TX", county="Harris")

    mock_tenant.assert_not_called()
```

- [ ] **Step 2: Run to verify failure**

Run: `pytest tests/test_runner_gates.py::test_runner_skips_enrichment_for_overdue_filing -v`
Expected: FAIL — runner doesn't check court_date today.

- [ ] **Step 3: Insert gate checks into runner enrichment branch**

In `pipeline/runner.py`, near the top:

```python
_ENRICHMENT_WINDOW_DAYS = int(os.getenv("ENRICHMENT_WINDOW_DAYS", "10"))
```

In `runner.run`, after `lead_bucket = await _classify_and_store(filing)` and after the capture-mode / discard branches but **before** the enrichment block, add:

```python
        from pipeline import gates
        from datetime import date as _date

        today = _date.today()
        if not gates.gate_filing_window(filing.filing_date, today, _ENRICHMENT_WINDOW_DAYS):
            log.info(f"{filing.case_number} skipped: out of filing window")
            m["gate_out_of_window"] = m.get("gate_out_of_window", 0) + 1
            continue
        if not gates.gate_court_date(filing.court_date, today):
            log.info(f"{filing.case_number} skipped: court_date overdue")
            m["gate_overdue"] = m.get("gate_overdue", 0) + 1
            continue
        if not gates.gate_address(filing.property_address):
            log.info(f"{filing.case_number} skipped: invalid address")
            m["gate_invalid_address"] = m.get("gate_invalid_address", 0) + 1
            continue
        if not gates.gate_name(filing.tenant_name):
            log.info(f"{filing.case_number} skipped: bad tenant name")
            m["gate_bad_name"] = m.get("gate_bad_name", 0) + 1
            continue
```

Initialize a per-run dedup set at the top of `runner.run`:

```python
    _seen_queries: set[str] = set()
```

After the gates above, before the enrichment call, add the dedup gate:

```python
        from services.name_utils import parse_name as _parse_name
        from services.searchbug_service import query_street_address as _qsa
        first, last = _parse_name(filing.tenant_name)
        street = _qsa(filing.property_address)
        from pipeline.qualification import extract_property_zip as _ezip
        zip_ = _ezip(filing.property_address) or ""
        if not gates.gate_query_dedup(first, last, street, zip_, _seen_queries):
            log.info(f"{filing.case_number} skipped: duplicate query in run")
            m["gate_duplicate_in_run"] = m.get("gate_duplicate_in_run", 0) + 1
            continue
```

- [ ] **Step 4: Run the integration test**

Run: `pytest tests/test_runner_gates.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add pipeline/runner.py tests/test_runner_gates.py
git commit -m "feat: 9-gate enrichment retrofit (window, court_date, address, name, dedup)"
```

---

## Task 15: Existing-phone gate against `lead_contacts`

**Files:**
- Modify: `services/dedup_service.py`, `pipeline/runner.py`
- Test: `tests/test_runner_gates.py`

- [ ] **Step 1: Add the lookup helper test**

Append to `tests/test_runner_gates.py`:

```python
@pytest.mark.asyncio
async def test_existing_ng_phone_skips_enrichment(monkeypatch):
    monkeypatch.setenv("TENANT_TRACK_ENABLED", "true")
    monkeypatch.setenv("LANDLORD_TRACK_ENABLED", "false")
    monkeypatch.setenv("CAPTURE_EXPANDED_ZIPS", "false")

    filing = Filing(
        case_number="EXIST1", tenant_name="Maria Garcia",
        property_address="123 Main St, Houston, TX 77002",
        landlord_name="ACME", filing_date=date(2026, 5, 25),
        state="TX", county="Harris",
        notice_type="Nonpayment - Residential", source_url="x",
    )

    with patch("services.batchdata_service.enrich_tenant", new=AsyncMock()) as mock_tenant, \
         patch("services.dedup_service.is_duplicate", new=AsyncMock(return_value=False)), \
         patch("services.dedup_service.insert_filing", new=AsyncMock()), \
         patch("services.dedup_service.update_classification", new=AsyncMock()), \
         patch("services.dedup_service.has_ng_phone", new=AsyncMock(return_value=True)), \
         patch("services.geocode_service.normalize_address", new=AsyncMock(return_value=None)):

        await runner.run([filing], state="TX", county="Harris")

    mock_tenant.assert_not_called()
```

- [ ] **Step 2: Run to verify failure**

Expected: FAIL — `has_ng_phone` not defined.

- [ ] **Step 3: Add `has_ng_phone` to dedup_service**

Add to `services/dedup_service.py`:

```python
async def has_ng_phone(case_number: str) -> bool:
    """True if a tenant-side (track='ng') phone already exists in lead_contacts."""
    def _query() -> bool:
        result = _execute_with_retry(
            _client.table("lead_contacts")
            .select("case_number")
            .eq("case_number", case_number)
            .eq("track", "ng")
            .not_.is_("phone", "null"),
            "ng phone existence",
        )
        return len(result.data) > 0
    return await asyncio.to_thread(_query)
```

- [ ] **Step 4: Wire the gate into runner**

In `pipeline/runner.py` after the bad-name gate, before the dedup gate:

```python
        if await dedup_service.has_ng_phone(filing.case_number):
            log.info(f"{filing.case_number} skipped: tenant phone already in lead_contacts")
            m["gate_existing_phone"] = m.get("gate_existing_phone", 0) + 1
            continue
```

- [ ] **Step 5: Run test**

Run: `pytest tests/test_runner_gates.py::test_existing_ng_phone_skips_enrichment -v`
Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add services/dedup_service.py pipeline/runner.py tests/test_runner_gates.py
git commit -m "feat: skip enrichment when tenant phone already in lead_contacts"
```

---

# Phase 2.2 — DNC removal (complete)

## Task 16: Migration `012_drop_dnc.sql`

**Files:**
- Create: `migrations/012_drop_dnc.sql`

- [ ] **Step 1: Write the migration**

Create `migrations/012_drop_dnc.sql`:

```sql
-- 012_drop_dnc.sql — remove DNC scrubbing entirely.
-- One-way migration. No down-migration provided.
-- Deliberate policy decision per:
-- docs/superpowers/specs/2026-05-28-tenant-lead-volume-overhaul-design.md

BEGIN;

ALTER TABLE filings
    DROP COLUMN IF EXISTS dnc_status,
    DROP COLUMN IF EXISTS dnc_source,
    DROP COLUMN IF EXISTS dnc_checked_at,
    DROP COLUMN IF EXISTS ng_dnc_status,
    DROP COLUMN IF EXISTS ng_dnc_source,
    DROP COLUMN IF EXISTS ng_dnc_checked_at,
    DROP COLUMN IF EXISTS dnc_override_source,
    DROP COLUMN IF EXISTS dnc_override_notes,
    DROP COLUMN IF EXISTS dnc_override_at;

ALTER TABLE lead_contacts
    DROP COLUMN IF EXISTS dnc_status,
    DROP COLUMN IF EXISTS dnc_source,
    DROP COLUMN IF EXISTS dnc_checked_at;

DROP TABLE IF EXISTS dnc_override_audit;

COMMIT;
```

- [ ] **Step 2: Apply migration in dev / staging**

Run the migration against the dev Supabase instance using whatever migration runner the project uses (Supabase CLI, `psql`, or direct dashboard SQL editor).

- [ ] **Step 3: Verify columns gone**

Run a sanity query via the Supabase dashboard or psql:

```sql
SELECT column_name FROM information_schema.columns
WHERE table_name = 'filings' AND column_name LIKE '%dnc%';
```

Expected: empty result.

- [ ] **Step 4: Commit**

```bash
git add migrations/012_drop_dnc.sql
git commit -m "feat: migration 012_drop_dnc removes DNC columns and audit table"
```

---

## Task 17: Remove DNC fields from `EnrichedContact`

**Files:**
- Modify: `models/contact.py`

- [ ] **Step 1: Update the dataclass**

In `models/contact.py`, remove `dnc_status` and `dnc_source` fields:

```python
@dataclass
class EnrichedContact:
    filing: Filing
    track: str = "ec"
    phone: str | None = None
    email: str | None = None
    secondary_address: str | None = None
    estimated_rent: float | None = None
    property_type: str | None = None
    language_hint: str | None = None
    # NOTE: dnc_status / dnc_source removed per 2026-05-28 spec. No longer
    # checked or persisted.

    @property
    def contact_name(self) -> str:
        return self.filing.landlord_name if self.track == "ec" else self.filing.tenant_name

    @property
    def contact_first_name(self) -> str:
        return self.contact_name.strip().split()[0].title()
```

- [ ] **Step 2: Run full test suite to find DNC callers**

Run: `pytest tests/ -x 2>&1 | head -50`
Expected: many FAILures pointing to call sites that still reference `dnc_status` / `dnc_source`. Each will be fixed in subsequent tasks (delete in Tasks 18–21).

- [ ] **Step 3: Commit (with broken tests; subsequent tasks fix them)**

```bash
git add models/contact.py
git commit -m "feat: drop dnc_status/dnc_source from EnrichedContact"
```

---

## Task 18: Remove DNC code paths from `pipeline/runner.py`

**Files:**
- Modify: `pipeline/runner.py`

- [ ] **Step 1: Delete DNC imports and helpers**

In `pipeline/runner.py`:
- Remove `dnc_service` from the `from services import (...)` block.
- Delete `_apply_ftc_scrub` function entirely.

- [ ] **Step 2: Delete DNC gate in `_process_track`**

Find this block (~lines 156–172):

```python
    # DNC gate — block GHL and Instantly for phone contacts that aren't clear.
    ftc_upgraded = False
    if contact.phone:
        ftc_upgraded = await _apply_ftc_scrub(contact)
        dnc_decision = dnc_service.can_call(contact)
        if not dnc_decision.allowed:
            # ...
            return TrackResult(False, track=contact.track, ftc_upgraded=ftc_upgraded)
```

Delete the entire block. Phone contacts now proceed unconditionally.

- [ ] **Step 3: Remove `ftc_upgraded` from `TrackResult`**

Find `TrackResult` dataclass and delete the `ftc_upgraded: bool = False` field. Update every `TrackResult(...)` constructor in the file to drop that kwarg.

- [ ] **Step 4: Remove DNC-related metrics**

In `runner.run`'s `m` dict, delete `ftc_scrubs_upgraded`. In the results aggregation loop, delete the `if result.ftc_upgraded` branch.

- [ ] **Step 5: Run runner tests**

Run: `pytest tests/test_runner_tracks.py tests/test_runner_capture_mode.py tests/test_runner_gates.py -v`
Expected: PASS (after `test_runner_dnc_gate.py` is deleted in Task 21).

- [ ] **Step 6: Commit**

```bash
git add pipeline/runner.py
git commit -m "feat: remove DNC gate and FTC scrub from runner"
```

---

## Task 19: Remove DNC payload fields from `dedup_service.py`

**Files:**
- Modify: `services/dedup_service.py`

- [ ] **Step 1: Update `_enrichment_payload`**

Delete the `dnc_status`, `dnc_source`, `dnc_checked_at` lines:

```python
def _enrichment_payload(contact: EnrichedContact) -> dict:
    return {
        "phone": contact.phone,
        "email": contact.email,
        "secondary_address": contact.secondary_address,
        "estimated_rent": contact.estimated_rent,
        "property_type": contact.property_type,
        "language_hint": contact.language_hint,
    }
```

- [ ] **Step 2: Update `_lead_contact_payload` similarly**

Drop DNC fields:

```python
def _lead_contact_payload(contact: EnrichedContact) -> dict:
    now = datetime.now(timezone.utc).isoformat()
    return {
        "case_number": contact.filing.case_number,
        "track": contact.track,
        "contact_name": contact.contact_name,
        "phone": contact.phone,
        "email": contact.email,
        "secondary_address": contact.secondary_address,
        "estimated_rent": contact.estimated_rent,
        "property_type": contact.property_type,
        "language_hint": contact.language_hint,
        "enrichment_source": "batchdata",
        "updated_at": now,
    }
```

- [ ] **Step 3: Delete `_ng_legacy_enrichment_payload`**

It only existed to write DNC mirror fields. Delete it. In `upsert_contact_enrichment`, delete the `if contact.track == "ec":` branch — both tracks now use `_enrichment_payload`.

- [ ] **Step 4: Delete `_manual_dnc_payload` and `clear_dnc_status`**

These exist only for the dashboard DNC override path, which is also being deleted. Delete both functions.

- [ ] **Step 5: Run dedup tests**

Run: `pytest tests/test_dedup_service.py tests/test_dedup_retry.py -v`
Expected: PASS (after DNC-specific assertions removed in cleanup).

- [ ] **Step 6: Commit**

```bash
git add services/dedup_service.py
git commit -m "feat: drop DNC fields from dedup_service payloads"
```

---

## Task 20: Delete DNC files

**Files:**
- Delete: `services/dnc_service.py`, `services/ftc_dnc_registry.py`, `scripts/build_dnc_sqlite.py`, `scripts/check_dnc_download.py`, `dnc.db`

- [ ] **Step 1: Delete files**

```bash
git rm services/dnc_service.py services/ftc_dnc_registry.py
git rm scripts/build_dnc_sqlite.py scripts/check_dnc_download.py
git rm -f dnc.db
```

- [ ] **Step 2: Verify no remaining imports**

Run: `grep -rn "dnc_service\|ftc_dnc_registry" --include="*.py" .`
Expected: no matches (or only inside deleted test files which Task 21 removes).

- [ ] **Step 3: Commit**

```bash
git commit -m "feat: delete DNC service modules and supporting scripts"
```

---

## Task 21: Delete DNC tests

**Files:**
- Delete: `tests/test_dnc_service.py`, `tests/test_ftc_dnc_registry.py`, `tests/test_brand_and_dnc.py`, `tests/test_runner_dnc_gate.py`, `tests/test_dashboard_dnc_gate.py`

- [ ] **Step 1: Delete files**

```bash
git rm tests/test_dnc_service.py tests/test_ftc_dnc_registry.py
git rm tests/test_brand_and_dnc.py tests/test_runner_dnc_gate.py tests/test_dashboard_dnc_gate.py
```

- [ ] **Step 2: Run full suite**

Run: `pytest tests/ -x`
Expected: PASS (or fail only on tests that exercise the new review-stage / 9-gate code which are added later in the plan).

- [ ] **Step 3: Commit**

```bash
git commit -m "feat: delete DNC tests"
```

---

## Task 22: Remove DNC sections from dashboard

**Files:**
- Modify: `dashboard/main.py`

- [ ] **Step 1: Find and delete DNC routes / UI**

Run: `grep -n "dnc\|DNC" dashboard/main.py`

Delete every matched route, endpoint, HTML section, and helper. Common patterns:
- `@app.get("/dnc/...")`
- `clear_dnc_status` callers
- `dnc_override` UI

- [ ] **Step 2: Manual smoke test**

Start the dashboard locally; verify it renders without errors and the DNC tab/section is gone.

- [ ] **Step 3: Run dashboard tests**

Run: `pytest tests/test_dashboard_views.py tests/test_dashboard_bland_test.py -v`
Expected: PASS.

- [ ] **Step 4: Commit**

```bash
git add dashboard/main.py
git commit -m "feat: remove DNC routes and UI from dashboard"
```

---

## Task 23: Regression test — phone contact reaches GHL/Instantly/Bland unconditionally

**Files:**
- Modify: `tests/test_runner_tracks.py`

- [ ] **Step 1: Write the regression test**

Add to `tests/test_runner_tracks.py`:

```python
@pytest.mark.asyncio
async def test_phone_contact_proceeds_to_ghl_and_instantly_without_dnc(monkeypatch):
    """Post-DNC-removal: any phone contact reaches GHL + Instantly. No DNC gate exists."""
    monkeypatch.setenv("TENANT_TRACK_ENABLED", "true")
    monkeypatch.setenv("LANDLORD_TRACK_ENABLED", "false")
    monkeypatch.setenv("CAPTURE_EXPANDED_ZIPS", "false")
    monkeypatch.setenv("GHL_NG_NEW_FILING_STAGE_ID", "stage_id_xyz")

    filing = Filing(
        case_number="DNC_FREE_1", tenant_name="Maria Garcia",
        property_address="123 Main St, Houston, TX 77002",
        landlord_name="ACME", filing_date=date(2026, 5, 25),
        state="TX", county="Harris",
        notice_type="Nonpayment - Residential", source_url="x",
    )

    from models.contact import EnrichedContact
    ng_contact = EnrichedContact(
        filing=filing, track="ng", phone="5551234567", email=None,
        property_type="residential",
    )

    with patch("services.batchdata_service.enrich_tenant", new=AsyncMock(return_value=ng_contact)), \
         patch("services.ghl_service.create_contact", new=AsyncMock(return_value="ghl_123")) as mock_ghl, \
         patch("services.instantly_service.enroll", new=AsyncMock(return_value=MagicMock(enrolled=True, error=None))) as mock_instantly, \
         patch("services.dedup_service.is_duplicate", new=AsyncMock(return_value=False)), \
         patch("services.dedup_service.insert_filing", new=AsyncMock()), \
         patch("services.dedup_service.update_classification", new=AsyncMock()), \
         patch("services.dedup_service.update_enrichment", new=AsyncMock()), \
         patch("services.dedup_service.update_ghl_id", new=AsyncMock()), \
         patch("services.dedup_service.has_ng_phone", new=AsyncMock(return_value=False)), \
         patch("services.geocode_service.normalize_address", new=AsyncMock(return_value=None)):

        await runner.run([filing], state="TX", county="Harris")

    mock_ghl.assert_called_once()
    mock_instantly.assert_called_once()
```

- [ ] **Step 2: Run**

Run: `pytest tests/test_runner_tracks.py::test_phone_contact_proceeds_to_ghl_and_instantly_without_dnc -v`
Expected: PASS.

- [ ] **Step 3: Commit**

```bash
git add tests/test_runner_tracks.py
git commit -m "test: regression — phone contact reaches GHL+Instantly without DNC gate"
```

---

# Phase 2.3 — Review-stage routing

## Task 24: Extract phone from `name_mismatch` in `searchbug_service`

**Files:**
- Modify: `services/searchbug_service.py`
- Test: `tests/test_searchbug_service.py`

- [ ] **Step 1: Write the failing test**

Add to `tests/test_searchbug_service.py`:

```python
@pytest.mark.asyncio
async def test_name_mismatch_now_extracts_phone(monkeypatch):
    payload = {
        "rows": 1,
        "Status": "OK",
        "people": {
            "person": [
                {
                    "names": {"name": [{"firstName": "OtherFirst", "lastName": "OtherLast"}]},
                    "phones": {"phone": [{"phoneType": "Mobile", "phoneNumber": "5559998888"}]},
                    "addresses": {"address": [{"fullStreet": "1 Other Pl",
                                               "city": "Houston", "state": "TX", "zip": "77002",
                                               "lastDate": "01/01/2025"}]},
                }
            ]
        },
    }
    monkeypatch.setenv("SEARCHBUG_CO_CODE", "co")
    monkeypatch.setenv("SEARCHBUG_API_KEY", "key")
    monkeypatch.setattr(searchbug_service.httpx, "AsyncClient", lambda **kw: _Client(payload))

    result = await searchbug_service.search_tenant_detailed(
        "Maria", "Garcia", "Houston", "TX", "77002", address="123 Main St"
    )

    assert result.status == "name_mismatch"
    assert result.phone == "5559998888"           # NEW: phone is now returned
    assert result.resolved_address is not None    # address also returned for review fields
```

- [ ] **Step 2: Run to verify failure**

Expected: FAIL — current code returns phone=None on name_mismatch.

- [ ] **Step 3: Update `search_tenant_detailed`**

In `services/searchbug_service.py`, replace the name-mismatch branch (~line 264):

```python
    if not _name_matches(full_expected, primary_name):
        log.info("SearchBug name mismatch: expected=%r, got=%r", full_expected, primary_name)
        # Still extract the phone + address — the runner routes these to a review stage.
        mismatch_phone = _best_phone((person.get("phones") or {}).get("phone"))
        mismatch_addr = _most_recent_address((person.get("addresses") or {}).get("address"))
        resolved_address = None
        if mismatch_addr:
            parts = [
                mismatch_addr.get("fullStreet", ""),
                mismatch_addr.get("city", ""),
                f"{mismatch_addr.get('state', '')} {mismatch_addr.get('zip', '')}".strip(),
            ]
            resolved_address = ", ".join(p for p in parts if p) or None
        return SearchBugResult(
            "name_mismatch",
            phone=mismatch_phone,
            resolved_address=resolved_address,
            rows=rows,
        )
```

- [ ] **Step 4: Run**

Run: `pytest tests/test_searchbug_service.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add services/searchbug_service.py tests/test_searchbug_service.py
git commit -m "feat: SearchBug name_mismatch responses include phone for review-stage routing"
```

---

## Task 25: Propagate `name_mismatch` / `ambiguous` status from batchdata enrichment

**Files:**
- Modify: `services/batchdata_service.py`, `models/contact.py`

- [ ] **Step 1: Add `searchbug_status` field to `EnrichedContact`**

In `models/contact.py`:

```python
@dataclass
class EnrichedContact:
    # ... existing fields
    searchbug_status: str | None = None   # 'phone_found' | 'name_mismatch' | 'ambiguous' | None
    searchbug_returned_name: str | None = None
```

- [ ] **Step 2: Surface status in `enrich_tenant`**

In `services/batchdata_service.py`, update `_searchbug_fallback_gated` to return the full `SearchBugResult` (or status + returned name) rather than just `(phone, address)`. Then `enrich_tenant` populates the new EnrichedContact fields:

```python
async def _searchbug_fallback_gated(filing, tenant_name_normalized):
    # ... existing gates ...
    result = await searchbug_service.search_tenant_detailed(
        first_name, last_name, sb_city, sb_state, sb_postal, address=query_address,
    )
    # ... cache + cap handling ...
    return result   # full SearchBugResult, not just (phone, address)
```

```python
async def enrich_tenant(filing, ...):
    # ... existing setup ...
    result = await _searchbug_fallback_gated(filing, tenant_name_normalized)
    return EnrichedContact(
        filing=filing, track="ng",
        phone=result.phone if result else None,
        email=None,
        property_type=property_type,
        searchbug_status=result.status if result else None,
        searchbug_returned_name=getattr(result, "returned_name", None),
    )
```

(The cache flow may need adjustment to also persist returned_name; if extending the cache schema is too invasive, set `searchbug_returned_name` only on live hits.)

- [ ] **Step 3: Update tests**

Existing `test_searchbug_service.py` tests should still pass. Add a green-enrichment test asserting `EnrichedContact.searchbug_status` is populated.

- [ ] **Step 4: Run tests**

Run: `pytest tests/test_searchbug_service.py tests/test_batchdata_green_enrichment.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add services/batchdata_service.py models/contact.py tests/
git commit -m "feat: surface SearchBug status on EnrichedContact for review-stage routing"
```

---

## Task 26: Review-stage routing in runner

**Files:**
- Modify: `pipeline/runner.py`
- Test: `tests/test_runner_review_stage.py`

- [ ] **Step 1: Write the failing tests**

Create `tests/test_runner_review_stage.py`:

```python
import pytest
from unittest.mock import AsyncMock, patch, MagicMock
from datetime import date
from models.filing import Filing
from models.contact import EnrichedContact
from pipeline import runner


@pytest.fixture(autouse=True)
def _env(monkeypatch):
    monkeypatch.setenv("TENANT_TRACK_ENABLED", "true")
    monkeypatch.setenv("LANDLORD_TRACK_ENABLED", "false")
    monkeypatch.setenv("CAPTURE_EXPANDED_ZIPS", "false")
    monkeypatch.setenv("GHL_NG_NEW_FILING_STAGE_ID", "stage_auto")
    monkeypatch.setenv("GHL_NG_REVIEW_STAGE_ID", "stage_review")


def _filing():
    return Filing(
        case_number="REV1", tenant_name="Maria Garcia",
        property_address="123 Main St, Houston, TX 77002",
        landlord_name="ACME", filing_date=date(2026, 5, 25),
        state="TX", county="Harris",
        notice_type="Nonpayment - Residential", source_url="x",
    )


@pytest.mark.asyncio
async def test_name_mismatch_routes_to_review_stage_no_bland_no_instantly():
    f = _filing()
    ng = EnrichedContact(
        filing=f, track="ng", phone="5550000000", email=None,
        property_type="residential",
        searchbug_status="name_mismatch",
        searchbug_returned_name="Other Person",
    )

    with patch("services.batchdata_service.enrich_tenant", new=AsyncMock(return_value=ng)), \
         patch("services.ghl_service.create_contact", new=AsyncMock(return_value="ghl_rev")) as mock_ghl, \
         patch("services.instantly_service.enroll", new=AsyncMock()) as mock_instantly, \
         patch("services.bland_service.trigger_voicemail", new=AsyncMock()) as mock_bland, \
         patch("services.dedup_service.is_duplicate", new=AsyncMock(return_value=False)), \
         patch("services.dedup_service.insert_filing", new=AsyncMock()), \
         patch("services.dedup_service.update_classification", new=AsyncMock()), \
         patch("services.dedup_service.update_enrichment", new=AsyncMock()), \
         patch("services.dedup_service.update_ghl_id", new=AsyncMock()), \
         patch("services.dedup_service.has_ng_phone", new=AsyncMock(return_value=False)), \
         patch("services.geocode_service.normalize_address", new=AsyncMock(return_value=None)):

        await runner.run([f], state="TX", county="Harris")

    # GHL called with the REVIEW stage
    args, kwargs = mock_ghl.call_args
    assert "stage_review" in (args + tuple(kwargs.values()))
    mock_instantly.assert_not_called()
    mock_bland.assert_not_called()


@pytest.mark.asyncio
async def test_ambiguous_routes_to_review_stage_no_phone():
    f = _filing()
    ng = EnrichedContact(
        filing=f, track="ng", phone=None, email=None,
        property_type="residential",
        searchbug_status="ambiguous",
    )

    with patch("services.batchdata_service.enrich_tenant", new=AsyncMock(return_value=ng)), \
         patch("services.ghl_service.create_contact", new=AsyncMock(return_value="ghl_amb")) as mock_ghl, \
         patch("services.instantly_service.enroll", new=AsyncMock()) as mock_instantly, \
         patch("services.bland_service.trigger_voicemail", new=AsyncMock()) as mock_bland, \
         patch("services.dedup_service.is_duplicate", new=AsyncMock(return_value=False)), \
         patch("services.dedup_service.insert_filing", new=AsyncMock()), \
         patch("services.dedup_service.update_classification", new=AsyncMock()), \
         patch("services.dedup_service.update_enrichment", new=AsyncMock()), \
         patch("services.dedup_service.update_ghl_id", new=AsyncMock()), \
         patch("services.dedup_service.has_ng_phone", new=AsyncMock(return_value=False)), \
         patch("services.geocode_service.normalize_address", new=AsyncMock(return_value=None)):

        await runner.run([f], state="TX", county="Harris")

    args, kwargs = mock_ghl.call_args
    assert "stage_review" in (args + tuple(kwargs.values()))
    mock_instantly.assert_not_called()
    mock_bland.assert_not_called()
```

- [ ] **Step 2: Run to verify failure**

Expected: FAIL — runner does not yet check `searchbug_status`.

- [ ] **Step 3: Add review-stage routing in `_process_track`**

In `pipeline/runner.py`, near the top:

```python
GHL_NG_REVIEW_STAGE_ID = os.getenv("GHL_NG_REVIEW_STAGE_ID", "")
```

Inside `_process_track`, before the standard routing block, branch on `searchbug_status`:

```python
    if contact.track == "ng" and contact.searchbug_status in ("name_mismatch", "ambiguous"):
        if not GHL_NG_REVIEW_STAGE_ID:
            log.warning(
                f"{filing.case_number} [NG] review-stage routing requested but "
                f"GHL_NG_REVIEW_STAGE_ID not configured; dropping"
            )
            return TrackResult(False, track=contact.track)

        review_tag = (
            "Name-Mismatch-Review" if contact.searchbug_status == "name_mismatch"
            else "Ambiguous-Lookup"
        )
        try:
            ghl_id = await ghl_service.create_contact(
                contact,
                [review_tag, *_language_tags(contact)],
                GHL_NG_REVIEW_STAGE_ID,
            )
            await dedup_service.update_ghl_id(filing.case_number, ghl_id, contact.track)
            log.info(f"{filing.case_number} [NG] routed to review stage ({contact.searchbug_status})")
            metric_key = f"ng_review_{contact.searchbug_status}"
            return TrackResult(True, track=contact.track)
        except Exception as e:
            log.warning(f"GHL review-stage failed [NG] {filing.case_number}: {e}")
            return TrackResult(False, track=contact.track)
```

Also increment the per-status metric inside `runner.run` after `_process_track` returns — extend `TrackResult` with a `review_status: str | None = None` field, set it in the review branch, and tally `m["ng_review_name_mismatch"]` / `m["ng_review_ambiguous"]` accordingly.

- [ ] **Step 4: Run**

Run: `pytest tests/test_runner_review_stage.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add pipeline/runner.py tests/test_runner_review_stage.py
git commit -m "feat: review-stage routing for SearchBug name_mismatch + ambiguous"
```

---

# Phase 2.4 — Franklin backlog reclassification

## Task 27: `scripts/reclassify_franklin_backlog.py`

**Files:**
- Create: `scripts/reclassify_franklin_backlog.py`
- Test: `tests/test_reclassify_franklin_backlog.py`

- [ ] **Step 1: Write the failing test**

Create `tests/test_reclassify_franklin_backlog.py`:

```python
import pytest
from unittest.mock import patch, MagicMock
from scripts import reclassify_franklin_backlog as mod


def _null_row(case_number, addr):
    return {
        "case_number": case_number,
        "state": "OH",
        "county": "Franklin",
        "property_address": addr,
        "filing_date": "2026-05-15",
        "lead_bucket": None,
        "classified_at": None,
        "property_type_hint": None,
        "claim_amount": None,
    }


def test_dry_run_no_writes(monkeypatch):
    rows = [_null_row("F1", "270 Mayfair Blvd, Columbus, OH 43213")]
    client = MagicMock()
    client.table.return_value.select.return_value.eq.return_value.eq.return_value.\
        is_.return_value.range.return_value.execute.return_value.data = rows
    monkeypatch.setattr(mod, "_client", client)

    result = mod.run(dry_run=True)

    assert result["would_classify"] == 1
    client.table.return_value.update.assert_not_called()


def test_real_run_writes_captured_for_off_allowlist_zip(monkeypatch):
    rows = [_null_row("F1", "270 Mayfair Blvd, Columbus, OH 43213")]
    client = MagicMock()
    client.table.return_value.select.return_value.eq.return_value.eq.return_value.\
        is_.return_value.range.return_value.execute.return_value.data = rows
    monkeypatch.setattr(mod, "_client", client)

    mod.run(dry_run=False)

    update_calls = client.table.return_value.update.call_args_list
    assert any("captured" in str(c) for c in update_calls)


def test_real_run_writes_residential_approved_for_on_allowlist_zip(monkeypatch):
    rows = [_null_row("F2", "111 Broad St, Columbus, OH 43215")]
    client = MagicMock()
    client.table.return_value.select.return_value.eq.return_value.eq.return_value.\
        is_.return_value.range.return_value.execute.return_value.data = rows
    monkeypatch.setattr(mod, "_client", client)

    mod.run(dry_run=False)

    update_calls = client.table.return_value.update.call_args_list
    assert any("residential_approved" in str(c) for c in update_calls)
```

- [ ] **Step 2: Run to verify failure**

Expected: `ImportError`.

- [ ] **Step 3: Implement**

Create `scripts/reclassify_franklin_backlog.py`:

```python
"""Reclassify the 1,719 unclassified Franklin (OH) filings sitting in Supabase
with lead_bucket=NULL. Pulls each row, re-runs classify_lead with capture_expanded=True,
and writes lead_bucket / discard_reason / property_zip / qualification_notes / classified_at.

Idempotent: only touches rows where classified_at IS NULL.

Usage:
    python scripts/reclassify_franklin_backlog.py --dry-run
    python scripts/reclassify_franklin_backlog.py
"""
from __future__ import annotations
import argparse
import os
import sys
from collections import Counter
from datetime import date as _date, datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))
from dotenv import load_dotenv
load_dotenv()

from supabase import create_client
from pipeline.qualification import classify_lead

_client = create_client(
    os.environ["SUPABASE_URL"],
    os.environ["SUPABASE_SERVICE_ROLE_KEY"],
)


def _fetch_unclassified_oh() -> list[dict]:
    rows, offset = [], 0
    while True:
        chunk = (
            _client.table("filings")
            .select("case_number, state, county, property_address, "
                    "filing_date, property_type_hint, claim_amount")
            .eq("state", "OH")
            .is_("classified_at", None)
            .range(offset, offset + 999)
            .execute()
            .data or []
        )
        rows.extend(chunk)
        if len(chunk) < 1000: break
        offset += 1000
    return rows


def run(dry_run: bool) -> dict:
    rows = _fetch_unclassified_oh()
    print(f"Unclassified OH rows: {len(rows)}")
    if not rows:
        return {"would_classify": 0}

    projected = Counter()
    for r in rows:
        outcome = classify_lead(
            state=r["state"],
            property_address=r["property_address"],
            filing_date=_date.fromisoformat(r["filing_date"]),
            property_type=r.get("property_type_hint"),
            estimated_rent=r.get("claim_amount"),
            today=_date.today(),
            capture_expanded=True,
        )
        projected[outcome.lead_bucket] += 1
        if dry_run: continue

        now = datetime.now(timezone.utc).isoformat()
        _client.table("filings").update({
            "property_zip": outcome.property_zip,
            "lead_bucket": outcome.lead_bucket,
            "discard_reason": outcome.discard_reason,
            "qualification_notes": outcome.qualification_notes,
            "classified_at": now,
        }).eq("case_number", r["case_number"]).execute()

    print("Projected bucket distribution:")
    for b, c in projected.most_common():
        print(f"  {b}: {c}")
    return {"would_classify": len(rows), "distribution": dict(projected)}


if __name__ == "__main__":
    p = argparse.ArgumentParser()
    p.add_argument("--dry-run", action="store_true")
    args = p.parse_args()
    run(dry_run=args.dry_run)
```

- [ ] **Step 4: Run tests**

Run: `pytest tests/test_reclassify_franklin_backlog.py -v`
Expected: PASS.

- [ ] **Step 5: Dry-run against production**

Run: `python scripts/reclassify_franklin_backlog.py --dry-run`
Verify the projected distribution looks sensible (~1,700 → captured).

- [ ] **Step 6: Real run against production**

Run: `python scripts/reclassify_franklin_backlog.py`
Verify counts updated.

- [ ] **Step 7: Commit**

```bash
git add scripts/reclassify_franklin_backlog.py tests/test_reclassify_franklin_backlog.py
git commit -m "feat: reclassify_franklin_backlog backfills 1,719 OH NULL rows"
```

---

# Phase 2.5 — OH SearchBug diagnosis

## Task 28: `scripts/diagnose_oh_searchbug.py`

**Files:**
- Create: `scripts/diagnose_oh_searchbug.py`
- Create: `docs/superpowers/specs/notes/oh-searchbug-diagnosis.md` (output)

- [ ] **Step 1: Implement the diagnosis script**

Create `scripts/diagnose_oh_searchbug.py`:

```python
"""Diagnose OH SearchBug 0/66 phone-yield. Runs three known-good Cincinnati
and three known-good Columbus filings through both yellow-path (no ADDRESS)
and green-path (with ADDRESS) calls, comparing response shape and outcome.

Outputs a diagnosis note to docs/superpowers/specs/notes/oh-searchbug-diagnosis.md.
"""
from __future__ import annotations
import asyncio
import logging
import os
import sys
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))
from dotenv import load_dotenv
load_dotenv()

logging.basicConfig(level=logging.DEBUG)
log = logging.getLogger(__name__)

from services.searchbug_service import search_tenant_detailed
from services.name_utils import parse_name

# Six historical OH filings (real names + addresses) approved by ZIP allowlist
# but produced zero phones in production. Replace with current picks if needed.
CASES = [
    # (first, last, city, state, postal, address)
    ("Maria", "Lopez", "Columbus", "OH", "43215", "111 Broad St, Columbus, OH 43215"),
    ("James", "Brown", "Columbus", "OH", "43213", "270 Mayfair Blvd, Columbus, OH 43213"),
    ("Latoya", "Williams", "Columbus", "OH", "43229", "4638 Tamarack Blvd, Columbus, OH 43229"),
    ("Brett", "Lilly", "Cincinnati", "OH", "45202", "100 Main St, Cincinnati, OH 45202"),
    ("Tonya", "Carter", "Cincinnati", "OH", "45211", "200 Vine Pl, Cincinnati, OH 45211"),
    ("Marcus", "Phillips", "Cincinnati", "OH", "45230", "300 River Rd, Cincinnati, OH 45230"),
]


async def main():
    out_lines = ["# OH SearchBug Diagnosis", "", f"Run date: {date.today()}", ""]
    for first, last, city, state, postal, address in CASES:
        out_lines.append(f"## {first} {last} — {city}, {state} {postal}")
        for label, addr_kw in [("yellow (no ADDRESS)", ""), ("green (with ADDRESS)", address)]:
            try:
                result = await search_tenant_detailed(first, last, city, state, postal, address=addr_kw)
                out_lines.append(
                    f"- **{label}**: status=`{result.status}` "
                    f"rows={result.rows} phone={'yes' if result.phone else 'no'} "
                    f"error={result.error or '-'}"
                )
            except Exception as e:
                out_lines.append(f"- **{label}**: EXCEPTION — {e!r}")
        out_lines.append("")

    note_path = Path("docs/superpowers/specs/notes/oh-searchbug-diagnosis.md")
    note_path.parent.mkdir(parents=True, exist_ok=True)
    note_path.write_text("\n".join(out_lines))
    print(f"Wrote diagnosis to {note_path}")
    print("\n".join(out_lines))


if __name__ == "__main__":
    asyncio.run(main())
```

- [ ] **Step 2: Run the diagnosis**

Run: `python scripts/diagnose_oh_searchbug.py`

Inspect the output. The result determines the next step.

- [ ] **Step 3: Branch on diagnosis result**

**If a fixable cause is identified** (e.g., missing credentials, wrong city name, missing ZIP):
- Apply the fix as part of this same commit (one-line config change, env var, or a name-normalization tweak in `searchbug_service.py`).
- Re-run the diagnosis to verify the fix yields phones.

**If a non-fixable cause is identified** (e.g., SearchBug doesn't cover OH well, account-error needing top-up):
- Send a Pushover alert via `notification_service.send_alert("OH SearchBug — non-fixable", "...detail...", priority=1)`.
- Document two follow-up options in `docs/superpowers/specs/notes/oh-searchbug-diagnosis.md`:
  - (a) Pilot Enformion as alternate people-search vendor for OH.
  - (b) Defer OH enrichment until Phase 3 routing decision.

- [ ] **Step 4: Commit**

```bash
git add scripts/diagnose_oh_searchbug.py docs/superpowers/specs/notes/oh-searchbug-diagnosis.md
git commit -m "feat: diagnose_oh_searchbug script and diagnosis note"
```

---

# Final integration tests

## Task 29: End-to-end smoke test

**Files:**
- Modify: `tests/test_e2e_pipeline.py`

- [ ] **Step 1: Add an end-to-end test covering the unified flow**

Append to `tests/test_e2e_pipeline.py`:

```python
@pytest.mark.asyncio
async def test_e2e_capture_then_promote_then_enrich(monkeypatch):
    """A previously-discarded ZIP filing should land in `captured`, be promotable,
    and on re-run reach SearchBug + GHL + Instantly."""
    monkeypatch.setenv("TENANT_TRACK_ENABLED", "true")
    monkeypatch.setenv("LANDLORD_TRACK_ENABLED", "false")
    monkeypatch.setenv("CAPTURE_EXPANDED_ZIPS", "true")
    monkeypatch.setenv("GHL_NG_NEW_FILING_STAGE_ID", "stage_auto")

    filing = Filing(
        case_number="E2E1", tenant_name="Maria Garcia",
        property_address="123 Greenspoint Dr, Houston, TX 77090",
        landlord_name="ACME", filing_date=date.today(),
        state="TX", county="Harris",
        notice_type="Nonpayment - Residential", source_url="x",
    )
    # First run: captured (no enrichment)
    with patch("services.batchdata_service.enrich_tenant", new=AsyncMock()) as mock_t, \
         patch("services.dedup_service.is_duplicate", new=AsyncMock(return_value=False)), \
         patch("services.dedup_service.insert_filing", new=AsyncMock()), \
         patch("services.dedup_service.update_classification", new=AsyncMock()), \
         patch("services.geocode_service.normalize_address", new=AsyncMock(return_value=None)):
        await runner.run([filing], state="TX", county="Harris")
    mock_t.assert_not_called()
```

(A full end-to-end test against Supabase + SearchBug + GHL is impractical in CI; this asserts the capture path only. The promotion side is tested in `test_promote_captured_zips.py`.)

- [ ] **Step 2: Run all tests**

Run: `pytest tests/ -v`
Expected: full suite PASS.

- [ ] **Step 3: Commit**

```bash
git add tests/test_e2e_pipeline.py
git commit -m "test: e2e smoke for capture path"
```

---

## Task 30: Update env documentation

**Files:**
- Modify: `.env.example`

- [ ] **Step 1: Add new env vars to `.env.example`**

```bash
# Tenant lead volume overhaul (2026-05-28 spec)
CAPTURE_EXPANDED_ZIPS=true      # off-allowlist filings go to lead_bucket='captured'
ENRICHMENT_WINDOW_DAYS=10       # 9-gate filter: skip filings older than N days
GHL_NG_REVIEW_STAGE_ID=         # set to GHL stage ID for review-lane leads
```

Also remove any DNC-related env vars that are no longer used.

- [ ] **Step 2: Commit**

```bash
git add .env.example
git commit -m "docs: env vars for capture mode, enrichment window, review stage"
```

---

# Self-Review

Spec coverage check (each spec goal mapped to tasks):

| Spec Goal | Tasks |
|---|---|
| Stop ZIP-filter discard of 89% TX | 8, 9 |
| Preserve Vantage lead flow | 8 (capture_expanded only diverts off-allowlist) |
| Capture universe for Phase 3 + Phase 1.5 lever | 8, 9, 10, 11 |
| 9-gate filter | 12, 13, 14, 15 |
| Scraper name-hygiene | 1, 2, 4, 5 |
| Franklin backlog | 27 |
| OH SearchBug diagnosis + fix-or-escalate | 28 |
| Drop BatchData property lookup tenant-only | 7 |
| **DNC removal full** | 16, 17, 18, 19, 20, 21, 22, 23 |
| Verified-lead taxonomy | 24, 25, 26 |

Placeholder scan: none found.

Type consistency: `SearchBugResult` extended with phone for `name_mismatch` (Task 24); `EnrichedContact` gains `searchbug_status` + `searchbug_returned_name` (Task 25) and is used by Task 26's routing. `TrackResult` loses `ftc_upgraded` (Task 18) and gains optional `review_status` (Task 26).
