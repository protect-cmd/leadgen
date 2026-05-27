from __future__ import annotations

import re

_GENERATIONAL_SUFFIXES: frozenset[str] = frozenset({"jr", "sr", "ii", "iii", "iv"})

_OCCUPANT_TRAILER_RE = re.compile(
    r"[,\s]+("
    r"and(?:/or)?\s+all\s+(?:other\s+)?occupants?"
    r"|all\s+(?:other\s+)?occupants?"
    r"|et\s*\.?\s*al\s*\.?"
    r")"
    r"(?:\s+of\s+.*)?"          # also drop "of <address>" tail
    r".*$",                       # and any tokens after the trailer
    flags=re.IGNORECASE,
)

_PLACEHOLDER_NAMES = frozenset({
    "john doe", "jane doe", "j doe", "jdoe",
    "unknown", "unknown tenant", "tenant", "tenant in possession",
    "all occupants", "occupants", "occupants unknown",
    "squaters", "squatter", "squatters",
})


def clean_tenant_name(raw: str) -> str:
    """Strip occupant trailers and reject placeholder defendant names.

    Returns the cleaned name, or '' if the row is a placeholder
    (causes downstream bad_name gate to drop the filing).
    """
    if not raw:
        return ""
    cleaned = _OCCUPANT_TRAILER_RE.sub("", raw).strip(" ,.")
    cleaned = re.sub(r"\s+", " ", cleaned)
    if not cleaned:
        return ""
    if cleaned.lower() in _PLACEHOLDER_NAMES:
        return ""
    return cleaned


_BUSINESS_RE = re.compile(
    r"\b(LLC|INC|CORP|LTD|LP|LLP|PLLC|PROPERTIES|PROPERTY|MANAGEMENT|MGMT|"
    r"REALTY|INVESTMENTS|HOLDINGS|TRUST|PARTNERS|GROUP|ENTERPRISES|VENTURES|"
    r"ESTATE\s+OF|DBA|C/O|S\.A\.|BANK)\b",
    re.IGNORECASE,
)

_COMMERCIAL_NOTICE_RE = re.compile(r"\b(commercial|retail|office)\b", re.IGNORECASE)


def infer_property_type(filing) -> str:
    """Return 'commercial' or 'residential' from notice_type + tenant_name signals.

    Replaces the per-filing BatchData lookup_property_info call for tenant-only mode.
    """
    if _COMMERCIAL_NOTICE_RE.search(filing.notice_type or ""):
        return "commercial"
    if _BUSINESS_RE.search(filing.tenant_name or ""):
        return "commercial"
    return "residential"


def _is_middle_initial(token: str) -> bool:
    """Single letter or single letter followed by a period."""
    t = token.rstrip(".")
    return len(t) == 1


_PARTICLE_TOKENS: frozenset[str] = frozenset({
    "de", "del", "la", "los", "las", "van", "von", "der", "da", "di", "dos",
})


def _is_particle(token: str) -> bool:
    return token.rstrip(".").lower() in _PARTICLE_TOKENS


def parse_name(raw: str) -> tuple[str, str]:
    """Parse a raw court name into (first_name, last_name).

    Handles:
    - "LAST, FIRST"
    - "LAST, FIRST MIDDLE"  -> middle stripped
    - "FIRST LAST"
    - "FIRST MIDDLE LAST"   -> middle stripped
    - "FIRST [MIDDLE] LAST JR/SR/II/III/IV"  -> suffix stripped
    - "FIRST PARTICLE [PARTICLE] LAST" -> particle(s) kept with last name
      (e.g. "Stephanie De Los Santos" -> ("Stephanie", "De Los Santos"))
    """
    raw = raw.strip()
    if not raw:
        return "", ""

    if "," in raw:
        # "LAST, FIRST [MIDDLE...]"
        last, _, rest = raw.partition(",")
        last = last.strip()
        parts = rest.strip().split()
        first = parts[0] if parts else ""
        return (first, last) if first and last else ("", "")

    # Space-separated: "FIRST [MIDDLE] LAST [SUFFIX]" or "FIRST LAST"
    tokens = raw.split()
    if len(tokens) < 2:
        return "", ""

    first = tokens[0]
    remaining = list(tokens[1:])

    # Strip trailing generational suffixes (Jr, Sr, II, III, IV)
    while remaining and remaining[-1].rstrip(".").lower() in _GENERATIONAL_SUFFIXES:
        remaining.pop()

    if not remaining:
        return "", ""

    # Walk backward from the final token to find where the surname begins.
    # If the token before the current start is a particle (De / La / Van / ...),
    # include it in the last name. This keeps compound surnames intact:
    # "Stephanie De Los Santos" -> last="De Los Santos".
    last_start = len(remaining) - 1
    while last_start > 0 and _is_particle(remaining[last_start - 1]):
        last_start -= 1

    last = " ".join(remaining[last_start:])
    return first, last


def split_tenants(raw: str) -> list[str]:
    """Split a multi-tenant string into individual name strings.

    Only splits 4-token strings where no token looks like a middle initial
    (single character or single char + dot). All other strings returned as-is.

    Examples:
        "AVONTE THOMAS ASHANTE JOHNSON" -> ["AVONTE THOMAS", "ASHANTE JOHNSON"]
        "BRETT L LILLY"                 -> ["BRETT L LILLY"]
    """
    tokens = raw.strip().split()
    if len(tokens) == 4 and not any(_is_middle_initial(t) for t in tokens):
        return [" ".join(tokens[:2]), " ".join(tokens[2:])]
    return [raw]


# Top-300 US Census 2010 surnames (lower-cased). Common names produce
# multi-match SearchBug responses we'd pay for but reject. Skip them.
_COMMON_SURNAMES: frozenset[str] = frozenset({
    "smith", "johnson", "williams", "brown", "jones", "garcia", "miller",
    "davis", "rodriguez", "martinez", "hernandez", "lopez", "gonzalez",
    "wilson", "anderson", "thomas", "taylor", "moore", "jackson", "martin",
    "lee", "perez", "thompson", "white", "harris", "sanchez", "clark",
    "ramirez", "lewis", "robinson", "walker", "young", "allen", "king",
    "wright", "scott", "torres", "nguyen", "hill", "flores", "green",
    "adams", "nelson", "baker", "hall", "rivera", "campbell", "mitchell",
    "carter", "roberts", "gomez", "phillips", "evans", "turner", "diaz",
    "parker", "cruz", "edwards", "collins", "reyes", "stewart", "morris",
    "morales", "murphy", "cook", "rogers", "gutierrez", "ortiz", "morgan",
    "cooper", "peterson", "bailey", "reed", "kelly", "howard", "ramos",
    "kim", "cox", "ward", "richardson", "watson", "brooks", "chavez",
    "wood", "james", "bennett", "gray", "mendoza", "ruiz", "hughes",
    "price", "alvarez", "castillo", "sanders", "patel", "myers", "long",
    "ross", "foster", "jimenez", "owens", "weaver", "graves",
    "washington", "butler", "simmons", "gonzales", "bryant",
    "alexander", "russell", "griffin", "hayes", "ford",
    "hamilton", "graham", "sullivan", "wallace", "woods", "cole", "west",
    "jordan", "reynolds", "fisher", "ellis", "harrison", "gibson",
    "mcdonald", "marshall", "ortega", "freeman",
    "wells", "webb", "simpson", "stevens", "tucker", "porter", "hunter",
    "hicks", "crawford", "henry", "boyd", "mason", "moreno", "kennedy",
    "warren", "dixon", "burns", "gordon", "shaw",
    "holmes", "rice", "robertson", "hunt", "black", "daniels", "palmer",
    "mills", "nichols", "grant", "knight", "ferguson", "rose", "stone",
    "hawkins", "dunn", "perkins", "hudson", "spencer", "gardner", "stephens",
    "payne", "pierce", "berry", "matthews", "arnold", "wagner", "willis",
    "ray", "watkins", "olson", "carroll", "duncan", "snyder", "hart",
    "cunningham", "bradley", "lane", "andrews", "harper", "fox",
    "riley", "armstrong", "austin", "pope",
})


def is_common_surname(last_name: str) -> bool:
    """Return True if last_name is in the top-300 US Census surnames."""
    return last_name.strip().lower() in _COMMON_SURNAMES


# Representative ZIP codes for yellow-source cities (city.lower(), state.upper()) -> ZIP
_CITY_ZIP: dict[tuple[str, str], str] = {
    ("cincinnati", "OH"): "45202",
    ("cleveland", "OH"): "44113",
    ("dayton", "OH"): "45402",
    ("columbus", "OH"): "43215",
    ("atlanta", "GA"): "30303",
    ("griffin", "GA"): "30223",
    ("marietta", "GA"): "30060",
    ("decatur", "GA"): "30030",
    ("chattanooga", "TN"): "37402",
    ("gallatin", "TN"): "37066",
    ("knoxville", "TN"): "37902",
    ("nashville", "TN"): "37201",
    ("phoenix", "AZ"): "85001",
    ("scottsdale", "AZ"): "85251",
    ("las vegas", "NV"): "89101",
    ("reno", "NV"): "89501",
    ("austin", "TX"): "78701",
}


def resolve_zip(city: str, state: str) -> str:
    """Return a representative ZIP for a known yellow-source city, or '' if unknown."""
    key = (city.strip().lower(), state.strip().upper())
    return _CITY_ZIP.get(key, "")
