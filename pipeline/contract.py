"""Shared lead-pipeline contract for the four businesses.

This is the normalization layer the consolidation plan (PLAN.md, Phase 1) calls
for. The four businesses keep their own storage models and tables — there is NO
table unification here. Instead, every source maps onto one normalized
`RawCourtRecord`, and the pipeline stages (ingest -> enrich -> stage -> fire)
consume that single shape regardless of which business produced it.

Three layers, by lifecycle stage:
  RawCourtRecord  -- what a source (scraper or import) produces
  LeadCandidate   -- a RawCourtRecord after gating + pre-paid scoring
  OutreachState   -- the mutable outreach/enrichment state for a lead

The "lead person" is named differently per business (tenant / defendant /
debtor) but is always the person we contact; `full_name`/`first_name`/
`last_name` normalize that. Likewise the counterparty (landlord / plaintiff /
creditor) and the freshness-anchor date (filing / judgment / writ).
"""
from __future__ import annotations

import re
from datetime import date
from enum import Enum

from pydantic import BaseModel

from models.amount_kind import BACK_RENT_TOTAL
from models.cosner import CosnerFiling
from models.filing import Filing
from models.garnishment import GarnishmentRecord
from models.judgment import JudgmentRecord
from services.name_utils import parse_name

_STREET_RE = re.compile(r"\d")


class Business(str, Enum):
    """The four lead-gen businesses (2x2: eviction/debt x filed/judgment)."""

    VANTAGE = "vantage"            # eviction x filed       -> filings + lead_contacts
    ISTS = "ists"                  # eviction x judgment    -> ists_judgments
    COSNER = "cosner"              # debt x filed           -> cosner_filings
    GARNISH_PROOF = "garnish_proof"  # debt x judgment     -> garnishment_orders


class RawCourtRecord(BaseModel):
    """Normalized source record. Every scraper/import maps onto this."""

    business: Business
    case_number: str
    full_name: str                       # raw lead-person name as captured
    first_name: str | None = None
    last_name: str | None = None
    raw_address: str                     # contactable address as captured
    state: str
    county: str
    counterparty_name: str | None = None  # landlord / plaintiff / creditor
    amount: float | None = None
    amount_kind: str | None = None
    freshness_date: date | None = None
    freshness_kind: str = ""             # which date drives freshness
    deadline_date: date | None = None
    deadline_kind: str | None = None
    source_url: str | None = None

    @property
    def dedupe_key(self) -> tuple[str, str, str]:
        """Composite identity (business, county, case_number).

        Court case numbers are reused across counties/courts, so case_number
        alone collides (see PLAN.md Phase 3). Dedup must key on this tuple.
        """
        return (self.business.value, self.county, self.case_number)

    @property
    def has_street_address(self) -> bool:
        """True when the address looks like a real street address (has a digit
        and is not the 'Unknown' placeholder). The quality floor requires this."""
        a = (self.raw_address or "").strip()
        return bool(a) and a.lower() != "unknown" and bool(_STREET_RE.search(a))


class LeadCandidate(BaseModel):
    """A RawCourtRecord after gating + pre-paid scoring (pre-enrichment)."""

    record: RawCourtRecord
    freshness_ok: bool = False
    floor_pass: bool = False
    floor_reasons: list[str] = []        # why the quality floor passed/failed
    prepaid_score: float | None = None   # uses only free/static data


class OutreachState(BaseModel):
    """Mutable enrichment + outreach state for a lead (mirrors lead_contacts).

    Read by the post-enrichment guard to decide whether an outward channel may
    fire. Never used to authorize a paid lookup (that is the pre-paid floor)."""

    business: Business
    case_number: str
    enriched_at: str | None = None
    phone: str | None = None
    email: str | None = None
    dnc_status: str | None = None        # unified enum (PLAN.md Phase 4)
    ghl_contact_id: str | None = None
    instantly_enrolled: bool = False
    bland_status: str | None = None


# --- Adapters: existing per-business model -> RawCourtRecord ----------------
# Thin, lossless-where-it-matters. No table changes; pure in-memory mapping.


def from_filing(f: Filing, *, county: str | None = None) -> RawCourtRecord:
    """Vantage / VDG (eviction x filed)."""
    first, last = parse_name(f.tenant_name)
    return RawCourtRecord(
        business=Business.VANTAGE,
        case_number=f.case_number,
        full_name=f.tenant_name,
        first_name=first or None,
        last_name=last or None,
        raw_address=f.property_address,
        state=f.state,
        county=county or f.county,
        counterparty_name=f.landlord_name or None,
        amount=f.claim_amount,
        amount_kind=BACK_RENT_TOTAL if f.claim_amount is not None else None,
        freshness_date=f.filing_date,
        freshness_kind="filing_date",
        deadline_date=f.court_date,
        deadline_kind="court_date" if f.court_date else None,
        source_url=f.source_url,
    )


def from_judgment(j: JudgmentRecord) -> RawCourtRecord:
    """ISTS (eviction x judgment)."""
    first, last = parse_name(j.defendant_name)
    return RawCourtRecord(
        business=Business.ISTS,
        case_number=j.case_number,
        full_name=j.defendant_name,
        first_name=first or None,
        last_name=last or None,
        raw_address=j.property_address,
        state=j.state,
        county=j.county,
        counterparty_name=j.plaintiff_name,
        freshness_date=j.judgment_date,
        freshness_kind="judgment_date",
        source_url=j.source_url,
    )


def from_cosner_filing(c: CosnerFiling) -> RawCourtRecord:
    """Cosner Drake (debt x filed)."""
    first, last = parse_name(c.defendant_name)
    return RawCourtRecord(
        business=Business.COSNER,
        case_number=c.case_number,
        full_name=c.defendant_name,
        first_name=first or None,
        last_name=last or None,
        raw_address=c.defendant_address,
        state=c.state,
        county=c.county,
        counterparty_name=c.creditor_name,
        amount=c.debt_amount,
        amount_kind=c.amount_kind,
        freshness_date=c.filing_date,
        freshness_kind="filing_date",
        deadline_date=c.answer_deadline,
        deadline_kind="answer_deadline" if c.answer_deadline else None,
        source_url=c.source_url,
    )


def from_garnishment(g: GarnishmentRecord) -> RawCourtRecord:
    """Garnish Proof (debt x judgment). freshness anchors on the WRIT filed
    date, which the schema stores in filing_date (see PLAN.md Phase 7)."""
    first, last = parse_name(g.debtor_name)
    return RawCourtRecord(
        business=Business.GARNISH_PROOF,
        case_number=g.case_number,
        full_name=g.debtor_name,
        first_name=first or None,
        last_name=last or None,
        raw_address=g.debtor_address,
        state=g.state,
        county=g.county,
        counterparty_name=g.creditor_name,
        freshness_date=g.filing_date,
        freshness_kind="writ_filed_date",
        deadline_date=g.exemption_deadline,
        deadline_kind="exemption_deadline" if g.exemption_deadline else None,
        source_url=g.source_url,
    )
