from datetime import date

from pipeline.qualification import classify_lead, extract_property_zip, is_approved_zip


# ── ZIP extraction (unchanged data-quality helper) ────────────────────────────

def test_extract_property_zip_from_standard_and_zip4_addresses():
    assert extract_property_zip("123 Main St, Nashville, TN 37211") == "37211"
    assert extract_property_zip("123 Main St, Nashville, TN 37211-1234") == "37211"


def test_extract_property_zip_returns_none_when_missing():
    assert extract_property_zip("Unknown") is None
    assert extract_property_zip("") is None


def test_is_approved_zip_helper_still_exposed():
    # APPROVED_ZIPS is retained (used elsewhere / future priority signal) even
    # though classify_lead no longer gates on it.
    assert is_approved_zip("TN", "37211") is True
    assert is_approved_zip("TN", "37013") is False
    assert is_approved_zip("TX", "37211") is False


# ── Classification (Phase 1: ZIP allowlist + rent gate + held bucket removed) ──

def test_missing_zip_still_discarded():
    """Missing ZIP is a data-quality gate (until Phase 2 self-heal), not policy."""
    outcome = classify_lead(
        state="TN", property_address="Unknown", filing_date=date(2026, 5, 5),
        today=date(2026, 5, 5),
    )
    assert outcome.property_zip is None
    assert outcome.lead_bucket == "discarded"
    assert outcome.discard_reason == "missing_zip"


def test_any_residential_zip_approved_regardless_of_allowlist():
    """ZIP allowlist dropped — an off-allowlist Nashville ZIP now qualifies."""
    outcome = classify_lead(
        state="TN", property_address="123 Main St, Nashville, TN 37013",
        filing_date=date(2026, 5, 5), today=date(2026, 5, 5),
    )
    assert outcome.property_zip == "37013"
    assert outcome.lead_bucket == "residential_approved"
    assert outcome.discard_reason is None


def test_old_filing_no_longer_held():
    """7-day held bucket removed — freshness is enforced by good_leads_now, not here."""
    outcome = classify_lead(
        state="TN", property_address="123 Main St, Nashville, TN 37211",
        filing_date=date(2026, 4, 20), today=date(2026, 5, 5),
    )
    assert outcome.lead_bucket == "residential_approved"
    assert outcome.discard_reason is None


def test_low_rent_no_longer_discarded():
    """Rent gate dropped — rent is a priority signal now, never a discard."""
    outcome = classify_lead(
        state="TX", property_address="123 Main St, Houston, TX 77002",
        filing_date=date(2026, 5, 5), property_type="residential",
        estimated_rent=900, today=date(2026, 5, 5),
    )
    assert outcome.lead_bucket == "residential_approved"
    assert outcome.discard_reason is None


def test_commercial_routes_to_commercial():
    outcome = classify_lead(
        state="TN", property_address="100 Industrial Way, Nashville, TN 37013",
        filing_date=date(2026, 5, 5), property_type="Office",
        estimated_rent=1200, today=date(2026, 5, 5),
    )
    assert outcome.lead_bucket == "commercial"
    assert outcome.discard_reason is None


def test_off_allowlist_ohio_zip_approved():
    outcome = classify_lead(
        state="OH", property_address="123 Greenspoint Dr, Columbus, OH 43004",
        filing_date=date(2026, 5, 12), today=date(2026, 5, 14),
    )
    assert outcome.property_zip == "43004"
    assert outcome.lead_bucket == "residential_approved"


def test_legacy_flags_are_noops():
    """capture_expanded / bypass_zip_filter are retained for caller compat but
    have no effect now that the ZIP gate is gone — always residential_approved."""
    for cap in (True, False):
        for byp in (True, False):
            outcome = classify_lead(
                state="TX", property_address="123 Greenspoint Dr, Houston, TX 77090",
                filing_date=date(2026, 5, 25), today=date(2026, 5, 25),
                capture_expanded=cap, bypass_zip_filter=byp,
            )
            assert outcome.lead_bucket == "residential_approved"
            assert outcome.discard_reason is None


def test_legacy_flags_still_discard_missing_zip():
    outcome = classify_lead(
        state="TX", property_address="No ZIP here, Houston, TX",
        filing_date=date(2026, 5, 25), today=date(2026, 5, 25),
        capture_expanded=True, bypass_zip_filter=True,
    )
    assert outcome.lead_bucket == "discarded"
    assert outcome.discard_reason == "missing_zip"
