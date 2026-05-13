from __future__ import annotations

import logging
from dataclasses import dataclass

import requests

log = logging.getLogger(__name__)

_NOMINATIM_URL = "https://nominatim.openstreetmap.org/search"
_USER_AGENT = "eviction-leadgen/1.0 (contact: dev@evictioncommand.com)"


@dataclass(frozen=True)
class NominatimResult:
    city: str | None
    postcode: str | None


def geocode_street_cobb(street: str) -> NominatimResult | None:
    """Geocode a Cobb County GA street address to city + postcode via Nominatim OSM.

    Caller is responsible for rate-limiting (Nominatim policy: 1 req/sec).
    Returns None if the address cannot be resolved.
    """
    query = f"{street}, Cobb County, GA, USA"
    try:
        r = requests.get(
            _NOMINATIM_URL,
            params={"q": query, "format": "json", "limit": 1, "addressdetails": 1},
            headers={"User-Agent": _USER_AGENT},
            timeout=15,
        )
        r.raise_for_status()
        hits = r.json()
        if not hits:
            log.debug("Nominatim: no result for %r", street)
            return None
        addr = hits[0].get("address", {})
        city = (
            addr.get("city")
            or addr.get("town")
            or addr.get("village")
            or addr.get("suburb")
        )
        postcode = addr.get("postcode")
        return NominatimResult(city=city, postcode=postcode)
    except Exception:
        log.warning("Nominatim geocode failed for %r", street, exc_info=True)
        return None
