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
