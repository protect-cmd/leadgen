from datetime import date
from models.filing import Filing
from models.contact import EnrichedContact
from pipeline.router import route_ec, route_ng


def _make_contact(**kwargs) -> EnrichedContact:
    filing = Filing(
        case_number="TEST-001",
        tenant_name="Jane Doe",
        property_address="123 Main St, Los Angeles, CA 90001",
        landlord_name="ACME Properties",
        filing_date=date(2026, 4, 30),
        state="CA",
        county="Los Angeles",
        notice_type="Unlawful Detainer",
        source_url="https://www.lacourt.ca.gov",
    )
    defaults = dict(
        phone="5550001234",
        email="jane@example.com",
        secondary_address=None,
        estimated_rent=None,
        property_type=None,
    )
    defaults.update(kwargs)
    return EnrichedContact(filing=filing, **defaults)


def _make_texas_contact(**kwargs) -> EnrichedContact:
    filing = Filing(
        case_number="TEST-TX-001",
        tenant_name="Jane Doe",
        property_address="123 Main St, Houston, TX 77002",
        landlord_name="ACME Properties",
        filing_date=date(2026, 5, 5),
        state="TX",
        county="Harris",
        notice_type="Eviction",
        source_url="https://jpwebsite.harriscountytx.gov",
    )
    defaults = dict(
        phone="5550001234",
        email="jane@example.com",
        secondary_address=None,
        estimated_rent=None,
        property_type=None,
    )
    defaults.update(kwargs)
    return EnrichedContact(filing=filing, **defaults)


def test_commercial_routes_to_ng():
    contact = _make_contact(property_type="commercial", estimated_rent=5000.0)
    outcome = route_ng(contact)
    assert outcome.action == "proceed"
    assert outcome.tag == "NG-New-Filing"
    assert outcome.pipeline == "commercial"


def test_commercial_routes_to_commercial_pipeline_for_ec():
    contact = _make_contact(property_type="commercial", estimated_rent=5000.0)
    outcome = route_ec(contact)
    assert outcome.action == "proceed"
    assert outcome.tag == "Commercial"
    assert outcome.pipeline == "commercial"


def test_residential_above_threshold_routes_to_ec():
    contact = _make_contact(property_type="residential", estimated_rent=2000.0)
    outcome = route_ec(contact)
    assert outcome.action == "proceed"
    assert outcome.tag == "EC-New-Filing"
    assert outcome.pipeline == "residential"


def test_residential_at_threshold_routes_to_ec():
    contact = _make_contact(property_type="residential", estimated_rent=1800.0)
    outcome = route_ec(contact)
    assert outcome.action == "proceed"
    assert outcome.tag == "EC-New-Filing"


def test_residential_below_threshold_skipped():
    contact = _make_contact(property_type="residential", estimated_rent=1200.0)
    outcome = route_ec(contact)
    assert outcome.action == "skip"
    assert outcome.tag == "Below-Threshold"


def test_missing_rent_uses_zip_fallback_after_qualification():
    contact = _make_contact(property_type="residential", estimated_rent=None)
    outcome = route_ec(contact)
    assert outcome.action == "proceed"
    assert outcome.tag == "EC-New-Filing"
    assert outcome.pipeline == "residential"


def test_missing_property_type_uses_zip_fallback_after_qualification():
    contact = _make_contact(property_type=None, estimated_rent=2000.0)
    outcome = route_ec(contact)
    assert outcome.action == "proceed"
    assert outcome.tag == "EC-New-Filing"
    assert outcome.pipeline == "residential"


def test_residential_routes_to_ng_residential_pipeline():
    contact = _make_contact(property_type="residential", estimated_rent=2000.0)
    outcome = route_ng(contact)
    assert outcome.action == "proceed"
    assert outcome.tag == "NG-New-Filing"
    assert outcome.pipeline == "residential"


def test_texas_residential_above_1500_routes_to_ec():
    contact = _make_texas_contact(property_type="residential", estimated_rent=1600.0)
    outcome = route_ec(contact)
    assert outcome.action == "proceed"
    assert outcome.tag == "EC-New-Filing"
    assert outcome.pipeline == "residential"
