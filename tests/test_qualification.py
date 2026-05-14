from datetime import date

from pipeline.qualification import classify_lead, extract_property_zip, is_approved_zip


def test_extract_property_zip_from_standard_and_zip4_addresses():
    assert extract_property_zip("123 Main St, Nashville, TN 37211") == "37211"
    assert extract_property_zip("123 Main St, Nashville, TN 37211-1234") == "37211"


def test_extract_property_zip_returns_none_when_missing():
    assert extract_property_zip("Unknown") is None
    assert extract_property_zip("") is None


def test_tennessee_whitelist_accepts_only_approved_nashville_zips():
    assert is_approved_zip("TN", "37211") is True
    assert is_approved_zip("TN", "37013") is False
    assert is_approved_zip("TX", "37211") is False


def test_classify_discards_missing_zip_without_touching_enriched_data():
    outcome = classify_lead(
        state="TN",
        property_address="Unknown",
        filing_date=date(2026, 5, 5),
        today=date(2026, 5, 5),
    )

    assert outcome.property_zip is None
    assert outcome.lead_bucket == "discarded"
    assert outcome.discard_reason == "missing_zip"
    assert outcome.qualification_notes == "Discarded before enrichment: no property ZIP found."


def test_classify_discards_non_whitelisted_tennessee_zip():
    outcome = classify_lead(
        state="TN",
        property_address="123 Main St, Nashville, TN 37013",
        filing_date=date(2026, 5, 5),
        today=date(2026, 5, 5),
    )

    assert outcome.property_zip == "37013"
    assert outcome.lead_bucket == "discarded"
    assert outcome.discard_reason == "zip_not_approved"


def test_classify_approved_tennessee_zip_as_residential_fallback_when_rent_missing():
    outcome = classify_lead(
        state="TN",
        property_address="123 Main St, Nashville, TN 37211",
        filing_date=date(2026, 5, 5),
        today=date(2026, 5, 5),
    )

    assert outcome.property_zip == "37211"
    assert outcome.lead_bucket == "residential_approved"
    assert outcome.discard_reason is None
    assert outcome.qualification_notes == "Approved by ZIP fallback; rent estimate unavailable."


def test_classify_approved_old_filing_as_held():
    outcome = classify_lead(
        state="TN",
        property_address="123 Main St, Nashville, TN 37211",
        filing_date=date(2026, 4, 20),
        today=date(2026, 5, 5),
    )

    assert outcome.lead_bucket == "held"
    assert outcome.discard_reason is None
    assert outcome.qualification_notes == "Held for Chris review: filing is 7+ days old."


def test_classify_commercial_as_high_priority_when_zip_approved():
    outcome = classify_lead(
        state="TN",
        property_address="123 Main St, Nashville, TN 37211",
        filing_date=date(2026, 5, 5),
        property_type="Office",
        estimated_rent=1200,
        today=date(2026, 5, 5),
    )

    assert outcome.lead_bucket == "commercial"
    assert outcome.discard_reason is None
    assert outcome.qualification_notes == "Commercial lead: high priority."


def test_classify_low_rent_residential_as_discarded_after_zip_approval():
    outcome = classify_lead(
        state="TN",
        property_address="123 Main St, Nashville, TN 37211",
        filing_date=date(2026, 5, 5),
        property_type="residential",
        estimated_rent=1200,
        today=date(2026, 5, 5),
    )

    assert outcome.lead_bucket == "discarded"
    assert outcome.discard_reason == "rent_below_threshold"


def test_classify_texas_uses_1500_rent_threshold():
    below = classify_lead(
        state="TX",
        property_address="123 Main St, Houston, TX 77002",
        filing_date=date(2026, 5, 5),
        property_type="residential",
        estimated_rent=1499,
        today=date(2026, 5, 5),
    )
    at_threshold = classify_lead(
        state="TX",
        property_address="123 Main St, Houston, TX 77002",
        filing_date=date(2026, 5, 5),
        property_type="residential",
        estimated_rent=1500,
        today=date(2026, 5, 5),
    )

    assert below.lead_bucket == "discarded"
    assert below.discard_reason == "rent_below_threshold"
    assert at_threshold.lead_bucket == "residential_approved"
    assert at_threshold.discard_reason is None


def test_classify_columbus_ohio_zip_as_residential_fallback_when_rent_missing():
    outcome = classify_lead(
        state="OH",
        property_address="123 Main St, Columbus, OH 43229",
        filing_date=date(2026, 5, 12),
        today=date(2026, 5, 14),
    )

    assert outcome.property_zip == "43229"
    assert outcome.lead_bucket == "residential_approved"
    assert outcome.discard_reason is None
