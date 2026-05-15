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
