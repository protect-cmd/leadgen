from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date
from decimal import Decimal


DEFAULT_RENT_THRESHOLD = Decimal("1800")
STATE_RENT_THRESHOLDS: dict[str, Decimal] = {
    "TX": Decimal("1500"),
    "TN": Decimal("1600"),
    "GA": Decimal("1600"),
    "FL": Decimal("1800"),
    "IL": Decimal("1800"),
    "WA": Decimal("1900"),
    "AZ": Decimal("1500"),
    "NV": Decimal("1600"),
}
FRESH_FILING_DAYS = 7

APPROVED_ZIPS: dict[str, set[str]] = {
    "TX": {
        "77002", "77003", "77004", "77006", "77007", "77008", "77019",
        "77024", "77025", "77027", "77046", "77056", "77057", "77098",
        "75201", "75202", "75204", "75205", "75206", "75209", "75214",
        "75219", "75220", "75225", "75230", "75231", "78205", "78209",
        "78212", "78213", "78215", "78216", "78217", "78218", "78701",
        "78702", "78703", "78704", "78705", "78731", "78733", "78746",
    },
    "GA": {
        "30301", "30303", "30305", "30306", "30307", "30308", "30309",
        "30312", "30313", "30314", "30315", "30316", "30317", "30318",
        "30319", "30324", "30326", "30327", "30328", "30338", "30339",
        "30342", "30346",
    },
    "FL": {
        "33101", "33109", "33129", "33130", "33131", "33132", "33133",
        "33134", "33137", "33138", "33139", "33140", "33141", "33143",
        "33145", "33146", "33154", "33156", "33301", "33304", "33305",
        "33306", "33308", "33309", "33310", "33311", "33312", "33315",
        "33316", "33601", "33602", "33603", "33604", "33605", "33606",
        "33607", "33609", "33611", "33612", "33614", "33615", "33616",
        "33629",
    },
    "IL": {
        "60601", "60602", "60603", "60604", "60605", "60606", "60607",
        "60608", "60610", "60611", "60613", "60614", "60615", "60616",
        "60618", "60622", "60625", "60626", "60630", "60640", "60641",
        "60645", "60647", "60657",
    },
    "WA": {
        "98101", "98102", "98103", "98104", "98105", "98106", "98107",
        "98109", "98112", "98115", "98116", "98117", "98118", "98119",
        "98121", "98122", "98125", "98126", "98133", "98144",
    },
    "AZ": {
        "85004", "85006", "85007", "85008", "85012", "85013", "85014",
        "85015", "85016", "85018", "85020", "85021", "85022", "85023",
        "85024", "85028", "85029", "85032", "85034", "85040", "85044",
        "85048", "85050", "85051", "85053", "85054",
    },
    "NV": {
        "89101", "89102", "89103", "89104", "89106", "89107", "89108",
        "89109", "89110", "89117", "89118", "89119", "89120", "89121",
        "89128", "89129", "89130", "89131", "89134", "89135", "89138",
        "89141", "89144", "89145", "89146", "89147", "89148", "89149",
    },
    "TN": {
        "37201", "37203", "37204", "37205", "37206", "37207", "37208",
        "37209", "37210", "37211", "37212", "37213", "37214", "37215",
        "37216", "37217", "37218", "37219", "37220", "37221",
    },
}

_ZIP_RE = re.compile(r"\b(\d{5})(?:-\d{4})?\b")
_COMMERCIAL_TYPES = {"commercial", "retail", "office"}


@dataclass(frozen=True)
class QualificationOutcome:
    property_zip: str | None
    lead_bucket: str
    discard_reason: str | None
    qualification_notes: str


def extract_property_zip(address: str) -> str | None:
    matches = _ZIP_RE.findall(address or "")
    return matches[-1] if matches else None


def is_approved_zip(state: str, property_zip: str | None) -> bool:
    if not property_zip:
        return False
    return property_zip in APPROVED_ZIPS.get(state.upper(), set())


def rent_threshold_for_state(state: str) -> Decimal:
    return STATE_RENT_THRESHOLDS.get(state.upper(), DEFAULT_RENT_THRESHOLD)


def classify_lead(
    *,
    state: str,
    property_address: str,
    filing_date: date,
    property_type: str | None = None,
    estimated_rent: float | Decimal | None = None,
    today: date | None = None,
) -> QualificationOutcome:
    property_zip = extract_property_zip(property_address)
    if property_zip is None:
        return QualificationOutcome(
            property_zip=None,
            lead_bucket="discarded",
            discard_reason="missing_zip",
            qualification_notes="Discarded before enrichment: no property ZIP found.",
        )

    if not is_approved_zip(state, property_zip):
        return QualificationOutcome(
            property_zip=property_zip,
            lead_bucket="discarded",
            discard_reason="zip_not_approved",
            qualification_notes="Discarded before enrichment: property ZIP is not approved.",
        )

    normalized_type = (property_type or "").strip().lower()
    if normalized_type in _COMMERCIAL_TYPES:
        return QualificationOutcome(
            property_zip=property_zip,
            lead_bucket="commercial",
            discard_reason=None,
            qualification_notes="Commercial lead: high priority.",
        )

    rent_threshold = rent_threshold_for_state(state)
    threshold_label = f"${rent_threshold:,.0f}"

    if estimated_rent is not None and Decimal(str(estimated_rent)) < rent_threshold:
        return QualificationOutcome(
            property_zip=property_zip,
            lead_bucket="discarded",
            discard_reason="rent_below_threshold",
            qualification_notes=f"Discarded after enrichment: estimated rent is below {threshold_label}.",
        )

    reference_date = today or date.today()
    if (reference_date - filing_date).days >= FRESH_FILING_DAYS:
        return QualificationOutcome(
            property_zip=property_zip,
            lead_bucket="held",
            discard_reason=None,
            qualification_notes="Held for Chris review: filing is 7+ days old.",
        )

    if estimated_rent is None:
        notes = "Approved by ZIP fallback; rent estimate unavailable."
    else:
        notes = f"Approved residential lead: rent estimate is {threshold_label}+."

    return QualificationOutcome(
        property_zip=property_zip,
        lead_bucket="residential_approved",
        discard_reason=None,
        qualification_notes=notes,
    )
