from __future__ import annotations
from datetime import date
from pydantic import BaseModel


class Filing(BaseModel):
    case_number: str
    tenant_name: str
    property_address: str
    landlord_name: str
    filing_date: date
    court_date: date | None = None
    state: str
    county: str
    notice_type: str
    source_url: str
    # Optional fields pre-populated by scrapers that provide them directly,
    # avoiding unnecessary BatchData calls for data we already have.
    claim_amount: float | None = None       # rent from CSV (TX)
    property_type_hint: str | None = None   # "residential" | "commercial" from CSV (TX)
