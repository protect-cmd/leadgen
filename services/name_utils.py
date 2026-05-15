from __future__ import annotations


def _is_middle_initial(token: str) -> bool:
    """Single letter or single letter followed by a period."""
    t = token.rstrip(".")
    return len(t) == 1


def parse_name(raw: str) -> tuple[str, str]:
    """Parse a raw court name into (first_name, last_name).

    Handles:
    - "LAST, FIRST"
    - "LAST, FIRST MIDDLE"  → middle stripped
    - "FIRST LAST"
    - "FIRST MIDDLE LAST"   → middle stripped
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

    # Space-separated: "FIRST [MIDDLE] LAST" or "FIRST LAST"
    tokens = raw.split()
    if len(tokens) < 2:
        return "", ""

    first = tokens[0]
    last = tokens[-1]

    # If there are middle tokens and last == middle initial, this is ambiguous;
    # trust first + last (first and last token) regardless.
    return first, last


def split_tenants(raw: str) -> list[str]:
    """Split a multi-tenant string into individual name strings.

    Only splits 4-token strings where no token looks like a middle initial
    (single character or single char + dot). All other strings returned as-is.

    Examples:
        "AVONTE THOMAS ASHANTE JOHNSON" → ["AVONTE THOMAS", "ASHANTE JOHNSON"]
        "BRETT L LILLY"                 → ["BRETT L LILLY"]
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


# Representative ZIP codes for yellow-source cities (city.lower(), state.upper()) → ZIP
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
