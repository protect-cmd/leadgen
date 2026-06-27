# models/garnishment.py
from __future__ import annotations
from dataclasses import dataclass
from datetime import date


@dataclass
class GarnishmentRecord:
    case_number: str
    debtor_name: str
    debtor_address: str
    creditor_name: str | None = None
    garnishee_name: str | None = None
    state: str = "FL"
    county: str = "Miami-Dade"
    filing_date: date | None = None
    garnishment_type: str = "wage"
    exemption_deadline: date | None = None
    source_url: str | None = None

    def to_row(self) -> dict:
        """Supabase-ready dict (dates as ISO strings)."""
        return {
            "case_number": self.case_number,
            "debtor_name": self.debtor_name,
            "debtor_address": self.debtor_address,
            "creditor_name": self.creditor_name,
            "garnishee_name": self.garnishee_name,
            "state": self.state,
            "county": self.county,
            "filing_date": self.filing_date.isoformat() if self.filing_date else None,
            "garnishment_type": self.garnishment_type,
            "exemption_deadline": self.exemption_deadline.isoformat() if self.exemption_deadline else None,
            "source_url": self.source_url,
        }
