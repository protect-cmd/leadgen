# models/cosner.py
"""Cosner Drake storage model. Mirrors GarnishmentRecord's isolated shape, but
consumer-of-a-fresh-debt-suit framing: the defendant is the lead, the deadline
is the ~30-day window to file an Answer before a default judgment is entered."""
from __future__ import annotations
from dataclasses import dataclass
from datetime import date


@dataclass
class CosnerFiling:
    case_number: str
    defendant_name: str                 # the person just sued (the lead)
    defendant_address: str              # defendant HOME address
    creditor_name: str | None = None    # plaintiff / debt buyer
    state: str = "TX"
    county: str = "Harris"
    filing_date: date | None = None
    answer_deadline: date | None = None  # filing_date + Answer window
    debt_amount: float | None = None
    amount_kind: str | None = None
    source_url: str | None = None

    def to_row(self) -> dict:
        """Supabase-ready insert dict (dates as ISO strings). Enrichment and
        outreach columns are written later via UPDATE, not at insert time."""
        return {
            "case_number": self.case_number,
            "defendant_name": self.defendant_name,
            "defendant_address": self.defendant_address,
            "creditor_name": self.creditor_name,
            "state": self.state,
            "county": self.county,
            "filing_date": self.filing_date.isoformat() if self.filing_date else None,
            "answer_deadline": self.answer_deadline.isoformat() if self.answer_deadline else None,
            "debt_amount": self.debt_amount,
            "amount_kind": self.amount_kind,
            "source_url": self.source_url,
        }
