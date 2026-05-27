# Portal Research Template

Fill out one block per portal you investigate. Send the completed block (plus
screenshots / screen recording) in chat. Don't skip fields — write
`unknown` or `n/a` if a field doesn't apply or wasn't tested. The whole point
is that every portal report has the same shape so it can be scored
green/yellow/red the same day.

## Why each field matters

- **Search access** decides whether the portal is date-enumerable. If a party
  name or case number is required, we can't enumerate filings and the source
  is dead for our purposes.
- **Volume** tells us if the source is worth building. A statewide portal that
  returns 3 evictions/day is not worth a scraper.
- **Address exposure on an EVICTION case** is the single most important
  finding for Vantage tenant outreach. Address on a mortgage/foreclosure case
  proves nothing — the case-type template can differ.
- **Anti-automation signals** decide our bypass cost. A portal with no
  CAPTCHA is cheap to scrape. A portal with hCaptcha/Turnstile may require
  paid bypass or a different vendor.
- **Backend** is the fastest path to a scraper. If the portal calls a JSON
  endpoint behind the page, we usually skip Playwright entirely.

## Per-portal template

Copy this block, fill it in, and send.

```
Portal: <state> — <portal/court name>
URL (search/advanced page): <full URL>
Goal: tenant phone outreach (NG) | landlord (EC) | volume only
Date tested: YYYY-MM-DD

SEARCH ACCESS
- Required fields to run a date-only search: <list, or "none">
- Date filter present: yes / no
- Case-type / eviction filter present: yes (value=___) / no
- Statewide or county-only: ___
- IP / geo restriction observed: <yes — needs US/MX VPN | no | unknown>
- Login or paid subscription required: yes / no
- Free-text notes on the search UX: ___

VOLUME (single recent weekday)
- Date used for volume test: YYYY-MM-DD
- Total rows returned: ___
- Eviction rows after applying the eviction filter: ___

ADDRESS EXPOSURE — EVICTION CASE ONLY (NOT mortgage/foreclosure/CV)
- Eviction case number tested: ___
- Defendant / respondent street address visible: yes / no
- City, state, ZIP visible: yes / no
- DOB / age visible: yes / no
- Phone / email visible: yes / no
- Screenshot of eviction detail page: <attached>

ANTI-AUTOMATION SIGNALS
- DevTools → Elements, search HTML for: hcaptcha / recaptcha / turnstile / cloudflare
  - Found which: ___
- During manual browsing, does a CAPTCHA fire on:
  - Form submit: yes / no
  - Detail click: yes / no
  - Direct nav to a detail URL: yes / no
- Retest in a private/incognito window from a cold session:
  - Search still works: yes / no
  - Detail still renders: yes / no

BACKEND
- DevTools → Network → Fetch/XHR while submitting the search:
  - Is the result loaded via a JSON endpoint (not HTML render)? yes / no
  - If yes, endpoint URL: ___
  - Method: GET / POST
  - One sample payload or query string: ___

OVERALL
- Your gut: green / yellow / red
- One-sentence reason: ___
- Artifacts attached: <screenshot count>, <recording yes/no>
```

## Hand-off flow

1. Researcher fills the template, attaches screenshots + recording, sends to
   project lead.
2. Project lead pastes the block to Claude.
3. Claude verifies the claims with a short headed Playwright probe (or skips
   the probe when the CAPTCHA/backend notes are decisive) and replies with:
   `GREEN — building scraper`, `YELLOW — needs X`, or `RED — reason`.
4. On green, Claude builds the scraper + parser tests using the captured
   artifacts. The portal row is added/updated in
   [docs/source_discovery_matrix.md](source_discovery_matrix.md).

## Common red flags to call out explicitly

- Address only shown on non-eviction case types (mortgage, foreclosure, CV
  injunction). Test an actual eviction or small-claims-eviction case.
- Geo-blocked portal that requires a specific country's IP. Note the country.
- Invisible CAPTCHA (hCaptcha / reCAPTCHA v3 / Turnstile). Even if you don't
  see a challenge during manual browsing, search the HTML — invisible widgets
  fire on bot signals and kill automation.
- "Public" portal that gates behind a paid CCAP-style subscription for bulk
  or REST access. Note the pricing path if visible.
- Date-search results that hit a round number (200, 500, 1000) — usually a
  hard cap. Flag it; we'll need to chunk by court or sub-county.
