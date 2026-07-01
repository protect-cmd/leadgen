"""Anti-bot vendor detection + block diagnosis for court portals.

Operationalizes the building-court-scrapers / reviewing-scraper-prs skill steps:
"identify the vendor first" (the fix depends on it) and "0 filings +
last_error=None == silent block, not a quiet day". Pure functions over an
HTTP response's status / headers / cookies / body — no network, no new deps —
so they're cheap to call from a scraper, a runner, or the probe_portal CLI.

Vendor fingerprint cheat-sheet (from the skill):
  - CF-Ray header / "Just a moment…" body / Server: cloudflare  -> Cloudflare
  - _px* cookies / "Press & Hold" / HUMAN                        -> PerimeterX
  - _abck cookie                                                 -> Akamai
  - datadome cookie / device-fingerprint challenge              -> DataDome
  - bare 429 with an empty body                                  -> Kasada
"""
from __future__ import annotations

from dataclasses import dataclass

# HTTP statuses a WAF typically returns when it blocks automation. 200 is NOT
# here on purpose: a vendor can serve a 200 "challenge" page (e.g. Cloudflare
# "Just a moment…"), which body-based detection still catches.
BLOCK_STATUSES = frozenset({401, 403, 406, 429, 503})

# Body markers that mean THIS response is an active challenge/block — not merely
# that a WAF fronts the host. A 200 with a CF-Ray header and real content is a
# normal Cloudflare-fronted page, NOT a block; a 200 with "Just a moment…" is.
_CHALLENGE_MARKERS = (
    "just a moment",
    "checking your browser",
    "enable javascript and cookies",
    "attention required",
    "verifying you are human",
    "press & hold",
    "px-captcha",
    "cf_chl",
    "/cdn-cgi/challenge-platform",
)


def _lower_keys(d: dict | None) -> dict[str, str]:
    return {str(k).lower(): str(v) for k, v in (d or {}).items()}


def detect_vendor(
    *,
    status_code: int | None = None,
    headers: dict | None = None,
    cookies: dict | None = None,
    body_text: str | None = None,
) -> str | None:
    """Return the anti-bot vendor name, or None if no fingerprint is present.

    Pass whatever you have; each layer is checked independently so a header-only
    or cookie-only response still resolves. One of: ``cloudflare``,
    ``perimeterx``, ``akamai``, ``datadome``, ``kasada``.
    """
    h = _lower_keys(headers)
    # cookie names can arrive as a dict or be embedded in a Set-Cookie header.
    cookie_names = {str(k).lower() for k in (cookies or {})}
    cookie_blob = (h.get("set-cookie", "") + " " + " ".join(cookie_names)).lower()
    body = (body_text or "").lower()
    server = h.get("server", "").lower()

    # Cloudflare — the most common .gov / Tyler block.
    if "cf-ray" in h or "cloudflare" in server or "just a moment" in body \
            or "__cf_bm" in cookie_blob or "cf_chl" in body:
        return "cloudflare"

    # PerimeterX / HUMAN (the Hillsborough wall).
    if "_px" in cookie_blob or "press & hold" in body or "px-captcha" in body \
            or "perimeterx" in body or "_pxhd" in cookie_blob:
        return "perimeterx"

    # DataDome (check before Akamai: DataDome also sets device-fingerprint cookies).
    if "datadome" in cookie_blob or "x-datadome" in h or "datadome" in body:
        return "datadome"

    # Akamai Bot Manager.
    if "_abck" in cookie_blob or "ak_bmsc" in cookie_blob:
        return "akamai"

    # Kasada — bare 429, empty/near-empty body, no other signal.
    if status_code == 429 and len(body.strip()) < 64 and "x-kpsdk-ct" not in h:
        return "kasada"
    if "x-kpsdk-ct" in h or "x-kpsdk-cd" in h:
        return "kasada"

    return None


def looks_blocked(
    *,
    status_code: int | None = None,
    headers: dict | None = None,
    cookies: dict | None = None,
    body_text: str | None = None,
) -> bool:
    """True if the response is a WAF block/challenge rather than real content.

    A blocking status, or a 200 served with an active challenge body (e.g.
    Cloudflare "Just a moment…", PerimeterX "Press & Hold"). Note: a vendor
    merely *fronting* the host (CF-Ray on a normal 200) is NOT a block — most
    Cloudflare/Akamai sites serve real content. Use this to turn a silent
    ``0 filings`` into an explicit ``last_error`` instead of a clean empty."""
    if status_code in BLOCK_STATUSES:
        return True
    body = (body_text or "").lower()
    if any(m in body for m in _CHALLENGE_MARKERS):
        return True
    # Kasada's bare-429 (covered above) aside, a near-empty Kasada body with its
    # header is also a block.
    h = _lower_keys(headers)
    return "x-kpsdk-ct" in h and len(body.strip()) < 64


@dataclass(frozen=True)
class ProbeResult:
    host: str
    reachable: bool
    status_code: int | None = None
    server: str | None = None
    vendor: str | None = None
    blocked: bool = False
    body_len: int = 0
    error: str | None = None

    def summary(self) -> str:
        if not self.reachable:
            return f"{self.host}: UNREACHABLE ({self.error})"
        if self.blocked:
            verdict = f"BLOCKED by {self.vendor}" if self.vendor else "BLOCKED"
        elif self.vendor:
            verdict = f"reachable (fronted by {self.vendor})"
        else:
            verdict = "reachable"
        return (
            f"{self.host}: {verdict} "
            f"(HTTP {self.status_code}, Server={self.server or '-'}, body={self.body_len}b)"
        )


def probe(host: str, *, timeout: int = 20) -> ProbeResult:
    """Reachability + vendor probe for a portal host (or full URL).

    Implements the skill's "Verify, don't ask step 1" as a repeatable tool: does
    the portal answer YOUR egress IP, and if it pushes back, which vendor is it?
    """
    import requests  # local import: keeps the module importable without network deps

    url = host if host.startswith(("http://", "https://")) else f"https://{host}"
    display = host.split("//", 1)[-1].split("/", 1)[0]
    try:
        r = requests.get(
            url,
            headers={
                "User-Agent": (
                    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                    "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
                ),
                "Accept-Language": "en-US,en;q=0.9",
            },
            timeout=timeout,
        )
    except Exception as e:  # DNS/timeout/refused — an IP-level block looks like this
        return ProbeResult(host=display, reachable=False, error=repr(e)[:160])

    vendor = detect_vendor(
        status_code=r.status_code,
        headers=dict(r.headers),
        cookies=r.cookies.get_dict(),
        body_text=r.text,
    )
    return ProbeResult(
        host=display,
        reachable=True,
        status_code=r.status_code,
        server=r.headers.get("Server"),
        vendor=vendor,
        blocked=looks_blocked(
            status_code=r.status_code, headers=dict(r.headers), body_text=r.text
        ),
        body_len=len(r.text or ""),
    )
