# scrapers/texas/harris_debt_claims.py
"""Cosner Drake source — Harris JP 'Cases Filed / Debt Claim' extract.

A debt-claim lawsuit just FILED against a consumer is the Cosner Drake client:
they have been sued by a debt collector and have a hard ~30-day window to file a
written Answer before the court enters a default judgment against them. This is
the pre-judgment, upstream half of the same debt lifecycle Garnish Proof sits at
the downstream end of (lawsuit filed -> [Answer window: Cosner Drake] ->
default judgment -> [Garnish Proof] -> garnishment).

Reuses the Vantage Harris machinery (same portal, same 'Cases Filed' extract,
same defendant address columns) and only swaps the case type to 'Debt Claim'.
Unlike the eviction product there is no disposition/outcome filter -- these are
brand-new filings, so every individual defendant with a home address is a lead.

The 30-day Answer deadline (filing_date + 30d) is the urgency clock; it is a
storage concern applied downstream, not here.
"""
from __future__ import annotations

from datetime import timedelta

from models.amount_kind import DEBT_CLAIM_TOTAL
from models.cosner import CosnerFiling
from models.filing import Filing
from scrapers.texas.harris import HarrisCountyScraper

# A Texas defendant generally has until the end of the Monday after 20 days from
# service to file a written Answer; ~30 days from the filing date is the
# operational urgency window Cosner Drake reaches them within.
TX_ANSWER_WINDOW_DAYS = 30

CASE_TYPE = "debt claim"
EXTRACT_TEXT = "cases filed"


class HarrisDebtClaimScraper(HarrisCountyScraper):
    """Harris JP debt-claim filings (Cases Filed), with full defendant address."""

    def __init__(self, headless: bool = True, lookback_days: int = 1):
        super().__init__(
            headless=headless,
            lookback_days=lookback_days,
            casetype=CASE_TYPE,
            extract_text=EXTRACT_TEXT,
        )


def to_cosner_filing(filing: Filing) -> CosnerFiling:
    """Map a debt-claim Filing onto the Cosner Drake storage shape.

    The eviction-shaped Filing reuses tenant/landlord field names; here the
    defendant is the consumer who was sued (the lead) and the 'landlord' slot
    carries the creditor/debt-buyer plaintiff.
    """
    deadline = (
        filing.filing_date + timedelta(days=TX_ANSWER_WINDOW_DAYS)
        if filing.filing_date else None
    )
    amount_kind = DEBT_CLAIM_TOTAL if filing.claim_amount is not None else None
    return CosnerFiling(
        case_number=filing.case_number,
        defendant_name=filing.tenant_name,
        defendant_address=filing.property_address,
        creditor_name=filing.landlord_name or None,
        state=filing.state,
        county=filing.county,
        filing_date=filing.filing_date,
        answer_deadline=deadline,
        debt_amount=filing.claim_amount,
        amount_kind=amount_kind,
        source_url=filing.source_url,
    )
