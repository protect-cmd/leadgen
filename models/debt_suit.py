# models/debt_suit.py
"""Cosner Drake debt-collection lawsuit (just FILED).

Parallel to models/judgment.py (ISTS) — a separate business line that does NOT
flow through the eviction Filing model / pipeline.runner. The target lead is the
sued consumer (the DEFENDANT), never the plaintiff/creditor.
"""
from __future__ import annotations
from dataclasses import dataclass
from datetime import date


@dataclass
class DebtSuit:
    case_number: str
    defendant_name: str          # the lead (sued consumer)
    defendant_address: str       # full home street address (gate_address-ready)
    plaintiff_name: str | None = None   # creditor — NEVER the target
    filing_date: date | None = None
    case_type_code: str = "CC"          # CC = Civil Collection
    county: str = ""
    state: str = "IN"
    court_code: str | None = None
    # amount sued for is NOT a structured field in MyCase (only the ~$157 filing
    # fee is exposed). Left None until complaint-PDF parsing is added. amount_kind
    # is "debt_claim_total" only when amount is genuinely populated.
    amount: float | None = None
    amount_kind: str | None = None
    case_status: str | None = None      # carried so Garnish Proof can later filter judgments
    source_url: str | None = None

    def to_row(self) -> dict:
        """Supabase-ready dict (dates as ISO strings)."""
        return {
            "case_number": self.case_number,
            "defendant_name": self.defendant_name,
            "defendant_address": self.defendant_address,
            "plaintiff_name": self.plaintiff_name,
            "filing_date": self.filing_date.isoformat() if self.filing_date else None,
            "case_type_code": self.case_type_code,
            "county": self.county,
            "state": self.state,
            "court_code": self.court_code,
            "amount": self.amount,
            "amount_kind": self.amount_kind,
            "case_status": self.case_status,
            "source_url": self.source_url,
        }
