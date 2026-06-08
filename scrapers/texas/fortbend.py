"""
Fort Bend County TX JP Courts eviction scraper.

Portal: https://tylerpaw.fortbendcountytx.gov/PublicAccess/default.aspx
Type: Tyler legacy PublicAccess (same template as Denton/Montgomery).
Coverage: All JP precincts pooled under single 'Fort Bend' location entry.
Geo: Requires US IP (Railway US deployment) - .gov subdomain.
Anti-bot: Cloudflare on main county site; tylerpaw subdomain accessible.
Address recovery: Case detail page hides street; Original Petition PDF
exposes full address per TX Justice Court Rule 510.3 (extracted via
pdfplumber).
"""

STATE = "TX"
COUNTY = "Fort Bend"
NOTICE_TYPE = "Eviction"
TIMEZONE = "America/Chicago"

PORTAL_BASE = "https://tylerpaw.fortbendcountytx.gov"
PORTAL_URL = f"{PORTAL_BASE}/PublicAccess/default.aspx"


class FortBendTXJPScraper:
    """Scraper stub - implementation begins Step 2."""

    def __init__(self):
        self.last_error = None