# models/judgment.py
from __future__ import annotations
from dataclasses import dataclass
from datetime import date


@dataclass
class JudgmentRecord:
    case_number: str
    defendant_name: str
    property_address: str
    plaintiff_name: str | None = None
    state: str = "TX"
    county: str = "Harris"
    judgment_date: date | None = None
    judgment_in_favor_of: str | None = None
    judgment_against: str | None = None
    disposition_desc: str | None = None
    disposition_date: date | None = None
    window: str = "W1"
    prior_phone: bool = False
    prior_bland_status: str | None = None
    source_url: str | None = None

    def to_row(self) -> dict:
        """Supabase-ready dict (dates as ISO strings)."""
        return {
            "case_number": self.case_number,
            "defendant_name": self.defendant_name,
            "property_address": self.property_address,
            "plaintiff_name": self.plaintiff_name,
            "state": self.state,
            "county": self.county,
            "judgment_date": self.judgment_date.isoformat() if self.judgment_date else None,
            "judgment_in_favor_of": self.judgment_in_favor_of,
            "judgment_against": self.judgment_against,
            "disposition_desc": self.disposition_desc,
            "disposition_date": self.disposition_date.isoformat() if self.disposition_date else None,
            "window_tag": self.window,  # DB column is window_tag ('window' is a Postgres reserved word)
            "prior_phone": self.prior_phone,
            "prior_bland_status": self.prior_bland_status,
            "source_url": self.source_url,
        }
