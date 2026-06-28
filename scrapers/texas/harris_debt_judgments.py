# scrapers/texas/harris_debt_judgments.py
"""Garnish Proof source — Harris JP 'Judgments Entered / Debt Claim' extract.

A debt-claim judgment entered AGAINST a consumer is the Garnish Proof client:
they lost or ignored a debt-collection suit, a money judgment is now on record,
and collection (bank-account garnishment / seizure in TX) is imminent. The
extract carries the debtor's full home address, so these are directly enrichable.

Reuses the ISTS Harris machinery (same portal, same 'Judgments Entered' extract,
same defendant-lost parser) and only swaps the case type to 'Debt Claim'.
The default-judgment subset (debtor never responded) is the prime GP lead.
"""
from __future__ import annotations

from datetime import timedelta

from models.garnishment import GarnishmentRecord
from models.judgment import JudgmentRecord
from scrapers.texas.harris_judgments import HarrisJudgmentScraper

# Texas: a defendant generally has ~30 days to move for a new trial / to vacate a
# default judgment (TRCP 329b). That window is the Garnish Proof urgency clock.
TX_VACATE_WINDOW_DAYS = 30

# Debt judgments stay actionable across the vacate window; pull fresh ones.
DEBT_FLOOR_DAYS = 1
DEBT_CEILING_DAYS = 14


class HarrisDebtJudgmentScraper(HarrisJudgmentScraper):
    """Harris JP debt-claim judgments (defendant-lost), with full debtor address."""

    def __init__(self, headless: bool = True,
                 floor_days: int = DEBT_FLOOR_DAYS, ceiling_days: int = DEBT_CEILING_DAYS):
        super().__init__(
            headless=headless,
            floor_days=floor_days,
            ceiling_days=ceiling_days,
            casetype="debt claim",
        )


def is_default_judgment(record: JudgmentRecord) -> bool:
    """True when the consumer never responded (the prime Garnish Proof lead)."""
    return "default" in (record.disposition_desc or "").lower()


def to_garnishment_record(record: JudgmentRecord) -> GarnishmentRecord:
    """Map a debt-claim JudgmentRecord onto the Garnish Proof storage shape."""
    deadline = (
        record.judgment_date + timedelta(days=TX_VACATE_WINDOW_DAYS)
        if record.judgment_date else None
    )
    return GarnishmentRecord(
        case_number=record.case_number,
        debtor_name=record.defendant_name,
        debtor_address=record.property_address,
        creditor_name=record.judgment_in_favor_of or record.plaintiff_name,
        garnishee_name=None,  # no garnishee yet — this is the judgment, pre-garnishment
        state="TX",
        county="Harris",
        filing_date=record.judgment_date,
        garnishment_type="default_judgment",
        exemption_deadline=deadline,
        source_url=record.source_url,
    )
