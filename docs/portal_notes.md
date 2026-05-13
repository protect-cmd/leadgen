# Court Portal Notes

## King County WA District Court — blue.kingcounty.com

**Status:** Permanently red as of 2026-05-13.

**Findings (2026-05-13):**
- Court portal supports filing-date search but officially states addresses/phones/DOB are not published.
- King County eReal property (`blue.kingcounty.com/Assessor/eRealProperty/`) is parcel-number-only. No owner-name search available.
- King County GIS open data (`gis-kingcounty.opendata.arcgis.com`) has parcel geometry layers but no owner-name field.
- RCW 42.56.070(9) explicitly prohibits use of public records lists for "commercial purposes," defined as communicating with individuals to facilitate profit-expecting activity — which is exactly what lead gen does.

**Decision:** Skip permanently. No viable enrichment path exists, and the commercial-use restriction applies.

---

## LA Superior Court — media.lacourt.org

**Status:** BLOCKED — data source unresolved  
**Issue:** CCP 1161.2 restricts UD case info from public access for 60 days after filing.  
**Portal:** LASC Media Access Portal requires paid subscription ($750/yr single user). Current account is "Temp User" with no remote access.  
**Options being evaluated:**
- Email PublicInfo@LACourt.org to confirm if paid MAP subscription bypasses 60-day restriction
- UniCourt Enterprise API (custom pricing, contact sales) — claims daily new filings but expensive
- Accept 60-day delay and build pipeline on older filings  

**Do not implement until data source is confirmed.**

---

---

## Harris County JP Court — jpwebsite.harriscountytx.gov

**Status:** In discovery — scraper written, selectors need verification  
**Case type filter:** "Forcible Detainer" or "Eviction"  
**Data source:** Public Extract Service (CSV download, no login required)  
**Portal URL:** https://jpwebsite.harriscountytx.gov/PublicExtracts/search.jsp

### Known Fields (from January Advisors data dictionary)
| CSV Field | Maps to | Notes |
|---|---|---|
| Case Number | `case_number` | |
| Case File Date | `filing_date` | MM/DD/YYYY format |
| Style of Case | `landlord_name` + `tenant_name` | "Plaintiff vs. Defendant" |
| Plaintiff Name | `landlord_name` fallback | |
| Defendant Address | `property_address` | |
| Nature of Claim | `notice_type` | "Forcible Detainer" |
| Next Hearing Date | `court_date` | nullable |

### Confirmed Selectors (verified 2026-05-01)
| Field | Selector | Value |
|---|---|---|
| Civil radio | `input#civil` | `CV` |
| Extract dropdown | `select#extract` | first non-zero option after CV loads |
| Court dropdown | `select#court` | `300` = All Courts |
| Case type dropdown | `select#casetype` | visible text `Eviction` |
| Format dropdown | `select#format` | `csv` |
| From date | `input#fdate` | MM/DD/YYYY |
| To date | `input#tdate` | MM/DD/YYYY |
| Submit | `input#submitBtn` | JS click (type="button", not submit) |

### Notes
- Extract and casetype dropdowns load dynamically after selecting CV — wait 800ms before interacting
- Submit button is `type="button"`, not `type="submit"` — must use `.click()` not form submit
- `Claim Amount` field contains rent as a decimal string — can be used for routing threshold
- `Cause of Action` = "Nonpayment - Residential" or "Nonpayment - Commercial" → maps to property_type for routing
- Defendant name often includes ", And All Other Occupants" — stripped in scraper

---

## Florida — Miami-Dade, Broward, Hillsborough

**Status:** Blocked for no-cost public scraping as of 2026-05-13.

**Business decision:** Avoid paid/premium API access for now unless the user explicitly approves it. Florida should not add another recurring automation cost while the current pipeline is still being stabilized.

### Miami-Dade Clerk OCS
- Current public OCS page is a newer SPA, not the old `Search.aspx` WebForms flow targeted by the scraper.
- Public party search requires a last name; filing-date-only search was rejected.
- Hearing search requires court/calendar/judge filters and does not provide a clean new-filings-by-date feed.
- Miami-Dade Commercial Data Services appears to offer bulk civil data/API access after registration and notarized approval, but do not pursue this paid/registered path unless approved.

### Broward Clerk ECA
- Public ECA portal loads, but the visible party search requires first and last name and uses CAPTCHA-backed form actions.
- Blank filing-date search was rejected by the UI.
- Broward offers a premium Clerk API with file-date filtering, but avoid using it for now due to cost.

### Hillsborough HOVER
- HOVER returned an access-denied/bot-block page to Playwright automation.
- HOVER references public data/search features, but the browser portal is not currently a reliable no-cost automation source.

### Next Step If Revisited
- First look for no-cost downloadable daily/new-case files before attempting more UI automation.
- Only consider official API/commercial data access after the user approves the added cost.

---

## Georgia re:SearchGA — researchga.tylerhost.net

**Status:** Login/search flow repaired 2026-05-13; scraper-only smoke returned 0 filings for both 2-day and 7-day lookbacks.

**Access:** Uses existing `RESEARCHGA_EMAIL` and `RESEARCHGA_PASSWORD` credentials. Do not add paid search/API products unless explicitly approved by the user.

**Current behavior:**
- Public home page redirects through Tyler/eFileGA identity.
- Login uses the `Sign in with Your eFileGA Account` path, then Tyler identity fields `#UserName` and `#Password`.
- Search endpoint responded normally after login, but returned no dispossessory cases in the tested window.
- Existing code comments say re:SearchGA search limits may apply, so avoid repeated broad smoke runs unless needed.

**Pipeline status:**
- `scripts/smoke_scrapers.py --states georgia` supports scraper-only validation.
- `jobs/run_georgia.py` is now production-shaped and sends non-empty filings to `pipeline.runner.run(..., state="GA", county="re:SearchGA")`.
- Do not add Georgia to the daily scheduler until a smoke test returns real filings or the source/county coverage is confirmed.

---

## Indiana Marion — public.courts.in.gov/mycase

**Status:** Blocked from current automation environment as of 2026-05-13.

**Findings:**
- Existing scraper assumes the old `/mycase/Search/SearchCases` JSON endpoint shape.
- A 90-day diagnostic search returned HTML instead of JSON for tested prefixes.
- Direct MyCase portal load redirected to `/mycase/Error/Forbidden`.
- The displayed error text says access may be denied for restricted pages, outside-US access, anonymous browsing services, or being flagged as a non-human automated process.

**Decision:** Do not continue Marion scraper work unless a compliant no-cost access path is found. The current zero-result smoke was not a valid “no cases” result.

---

## Nevada Clark County — CourtView and Las Vegas Odyssey

**Status:** Enrichment path confirmed 2026-05-13 — scraper + assessor proof pending.

### Clark County Justice Court Calendar
- CourtView calendar at `https://cvpublicaccess.clarkcountynv.gov/eservices/calendar.page` is public, no login.
- Calendar lists eviction hearings (event types: "EVICTION HEARING", "EVICTION 5 DAY UNLAWFUL DETAINER", "LANDLORD/TENANT HEARING") for all 10 townships.
- Live case confirmed: `26EL000030 RIVER CITY REALTY LLC VS REYES, YAIRE et al` (Laughlin, 2026-05-13).
- Case rows include hearing time, event type, judge, location, case number, landlord vs. tenant, and status.
- Court detail pages do not expose property addresses.
- Multi-day navigation requires Playwright (Wicket Java app with session state).

### Clark County Assessor Owner-Name Search
- URL: `https://maps.clarkcountynv.gov/assessor/AssessorParcelDetail/ownr.aspx`
- ASP.NET WebForms POST: fields `txtBxLastName`, `txtBxFirstName`, `r1=rdCurrent`, `btnSubmit`.
- Returns table of matching owner names + APN numbers (formatted with dashes). Link href format: `parceldetail.aspx?hdnParcel={apn_no_dashes}&hdnInstance=pcl7`.
- Detail page (`parceldetail.aspx?hdnParcel=...`) returns owner name, mailing address, and **Location Address** (the property address).
- **Limitation**: Corporate landlord names (LLCs, property management companies) frequently return 0 matches. "RIVER CITY REALTY LLC" → 0 results. Individual owner names match well.
- Expected single_match rate: ~20-25% (same structural gap as Maricopa).

### BatchData APN Skip-Trace (confirmed working 2026-05-13)
- Clark County FIPS: `32003`.
- Endpoint: `POST /api/v1/property/skip-trace` with `{"requests": [{"apn": "001-04-210-031", "countyFipsCode": "32003"}]}`.
- Returns: property address (validated, with ZIP+4), owner name, phone numbers with DNC status, emails.
- APN-based lookup is preferred over address-based for uniqueness (APN is the canonical property identifier).
- This replaces the need to parse the detail page address — get APN from assessor, pass directly to BatchData.

### NG Track (Tenant Contact Enrichment)
- BatchData has no name-only skip-trace endpoint. Tenant contact info requires a property address or APN first.
- Once APN is resolved from the assessor, BatchData APN skip-trace returns property address. A second BatchData skip-trace with the property address returns the tenant's phone/email (NG track).
- Total: 2 BatchData calls per lead for dual-track enrichment (EC + NG).

### Las Vegas Township Justice Court
- Separate Odyssey Public Access portal exists for Las Vegas civil records.
- Civil search form presents CAPTCHA before search.
- Skip for automation unless another no-CAPTCHA extract or report source is found.

**Decision:** Clark non-Las Vegas has a confirmed enrichment path. Next step: build Playwright scraper for CourtView calendar + ClarkAssessorClient (ASP.NET form POST), run 50-case proof to measure single_match rate. If rate is acceptable, wire to pipeline. Only `single_match` APNs should enter BatchData enrichment.

---

## Arizona Maricopa County — Justice Court Calendars

**Status:** Strong no-cost candidate, but missing address fields as of 2026-05-13.

**Sources:**
- Calendar index: https://justicecourts.maricopa.gov/app/courtrecords/CourtCalendars
- Case detail pattern: `https://justicecourts.maricopa.gov/app/courtrecords/CaseInfo.aspx?casenumber={case_number}000`

**Findings:**
- The calendar index lists every Justice Court with a 7-day schedule URL, e.g. `CourtCalendar?id=3822&startdate=5/12/2026&length=7`.
- Court calendars include real `Eviction Action Hearing` rows with case number, hearing date, hearing time, event subtype, plaintiff/landlord, and defendant/tenant.
- Detail pages expose case number, judge, file date, court location, case type, status, parties, attorney/pro per status, calendar events, and summons event dates.
- Tested detail page did not expose property address, defendant address, ZIP code, rent/claim amount, or documents.
- `scripts/smoke_scrapers.py --states arizona --lookback-days 7` uses a 25-case cap for proof runs. A 2026-05-13 scraper-only smoke returned 25 filings and skipped one detail page whose file date was not parseable.
- Address feasibility check: the public Maricopa County Assessor Parcel Viewer configuration exposes an ArcGIS FeatureServer layer with `OWNER_NAME`, `PHYSICAL_ADDRESS`, mailing address, APN, city, ZIP, and jurisdiction fields. A 15-case Maricopa sample matched some cases to exactly one parcel, some cases to multiple parcels under the same owner, and some cases to no parcel-owner match. This can support a confidence-scored no-cost enrichment proof, but it cannot safely infer a unique property address for every eviction filing.
- `scripts/proof_maricopa_addresses.py --max-cases 15 --lookback-days 7` performs the no-cost address proof and labels each case as `single_match`, `ambiguous`, `no_match`, or `error`. A 2026-05-13 live proof returned 15 filings: 3 `single_match`, 6 `ambiguous`, 6 `no_match`, and 0 `error`. Only `single_match` results populated `property_address`; ambiguous/no-match results remained `Unknown`.
- `jobs/run_arizona.py --max-cases 50 --lookback-days 7` is the repeatable scraper-only decision job. A 2026-05-13 live run returned 50 filings: 10 usable `single_match` addresses, 22 ambiguous owner matches, 18 no matches, and 0 match errors. By default, the job uses a 2-day daily lookback and does not call the pipeline runner, BatchData, Supabase, GHL, or Bland unless `--pipe` is provided.
- Daily scheduler status: Arizona runs at 13:40 UTC with `--pipe --notify`. Only assessor `single_match` filings with a non-`Unknown` property address are passed into the pipeline; ambiguous, no-match, and error cases remain out of downstream enrichment/outreach.

**Decision:** Scheduled as a constrained pipeline source. The scraper remains confidence-scored: only `single_match` assessor address results are eligible for downstream workflows, and no-address leads stay held out.

---

## San Diego Superior Court — sdcourt.ca.gov

**Status:** Calendar confirmed 2026-05-13 — address lookup via Odyssey not yet tested

**CCP 1161.2 constraint:** UD case records sealed for 60 days after filing. Calendar-based leads are 60–180+ days old, not fresh filings. Freshness tolerance must be confirmed with user before scheduling.

### Calendar source (confirmed)

Public civil calendar — no login, no CAPTCHA, no cost:

```
http://www.sandiego.courts.ca.gov/portal/online/calendar/F_SVCAL{N}.html
```

`N=1` = today, `N=2` = tomorrow, `N=3` = day after tomorrow. Confirmed pageable in both directions.

| Division | Pattern |
|---|---|
| Central | `F_SVCAL{N}.html` |
| North County | `F_VVCAL{N}.html` |
| East County | `F_EVCAL{N}.html` |
| South County | `F_BVCAL{N}.html` |

Calendar rows include: case number (e.g. `26UD016659C`), entitlement (plaintiff vs. defendant), event type ("Unlawful Detainer Court Trial"), department, location, time, and attorney names. Tested 2026-05-13 against Central division; confirmed live UD rows.

### Address lookup (not yet tested)

Odyssey Register of Actions portal:

```
https://odyroa.sdcourt.ca.gov/
```

Gated by a session-cookie disclaimer. Must click "I have read, understood, and agree" to reach the case search at `https://odyroa.sdcourt.ca.gov/Cases`. Requires Playwright — cannot scrape with simple HTTP fetch. Portal says it provides "non-confidential case data and documents for imaged cases." Whether addresses appear for 60+ day UD cases is unconfirmed.

**Odyssey bot-blocked (2026-05-13):** Playwright proof attempted. Portal returns "Performing security verification" (Cloudflare-style bot challenge) on every page load — case search is unreachable via automation. Do not pursue Odyssey for address lookup without a residential proxy or manual-session approach.

**Alternative enrichment path investigated (2026-05-13):** The public SANDAG parcel FeatureServer (`geo.sandag.org/server/rest/services/Hosted/Parcels/FeatureServer/0`) has parcel geometry, APN, and situs address fields but **no owner name field** — cannot do the Maricopa-style landlord-name → parcel → address lookup. ParcelQuest (the ARCC's recommended property search tool) is a paid service. No free owner-name-searchable layer found. **Best remaining option:** pass calendar-sourced leads (landlord + tenant names) through the existing BatchData pipeline enrichment, accepting that enrichment costs apply the same as other states.

### Known fields from calendar (confirmed)
| Field | Source |
|---|---|
| `case_number` | Calendar row |
| `landlord_name` | Calendar row (plaintiff) |
| `tenant_name` | Calendar row (defendant) |
| `court_date` | Calendar row (hearing date/time) |
| `filing_date` | Odyssey detail (unconfirmed) |
| `property_address` | Odyssey detail (unconfirmed) |
| `state` | Hardcoded `CA` |
| `county` | Hardcoded `San Diego` |

**Do not implement scraper until Odyssey address lookup is confirmed.**

---

## Orange County Superior Court — occourts.org

**Status:** Partially discovered 2026-05-13 — calendar portal unconfirmed for UD access

**CCP 1161.2 constraint:** Same 60-day UD seal as all California courts.

**Calendar portal:** `https://courtcalendar.occourts.org/` — supports Civil search by date range without login. However, the form uses server-side session state; URL parameters alone (e.g. `?catDesc=Civil&hearingDateFrom=...`) return the blank search form, not results. Requires Playwright form submission to test. Unclear whether UD cases appear in results or are filtered by the court.

**Civil case access:** `https://civilwebshopping.occourts.org/` — case number required, no date-enumerable search.

**Name search:** `https://namesearch.occourts.org/` — requires paid account registration.

**Decision:** Low priority. Hold until SD Odyssey proof is done. If SD calendar → Odyssey pattern confirms addresses, apply same pattern to OC.

---

## Riverside Superior Court — riverside.courts.ca.gov

**Status:** Red — no-cost date-enumerable path does not exist as of 2026-05-13

**CCP 1161.2 constraint:** Same 60-day UD seal as all California courts.

**Public portal:** `https://epublic-access.riverside.courts.ca.gov/public-portal/` — requires paid name-search credits ($1/name, or $250/month unlimited). UD case access explicitly requires providing plaintiff name + defendant name + property address before the case is shown — circular dependency for cold-start scraping.

**Decision:** Skip unless a no-cost daily filing extract or public calendar is discovered.

---

## Georgia — Magistrate Court Research (2026-05-13)

**Context:** re:SearchGA (Tyler/eFileGA) was reclassified red — it covers State Court, not Magistrate Court, and exposes no addresses. Residential dispossessory filings in Georgia are handled by Magistrate Courts. Four priority counties researched below.

---

### Cobb County Magistrate Court — cobbcounty.gov/magistrate-court

**Status:** Strong yellow — calendar and dockets are public, no login, no CAPTCHA. Address lookup requires BatchData or records-search (bulk use prohibited).

**Sources confirmed (2026-05-13):**

1. **infax.com XML docket** — `https://www.infax.com/Docket/CobbCountyMagistrate/assets/newData.xml`
   - Direct HTTP GET, no auth, no CAPTCHA.
   - Machine-readable XML: `caseName` (tenant/defendant), `caseNumber`, `caseType` (`MD` = Magistrate Dispossessory, `WA` = Warrant), `roomID`, `floorID`, `timeStart`, `timeEnd`.
   - Shows the rolling hearing calendar (recent + upcoming days).
   - Only shows tenant name, not landlord name. Case numbers are date-enumerable via this feed.

2. **DISPO PDF calendars** — `https://judicial.cobbcounty.gov/mc/magCalendars/{DD} {MON} {YYYY} DISPO {TIME} {JUDGE}.pdf`
   - Public HTTP GET, no auth.
   - pdfplumber-parseable. Confirmed structure: entry number, case number, plaintiff/landlord, plaintiff attorney name, `VS`, hearing type (`DISPOSSESSORY HEARING` or `MOTION HEARING`), defendant/tenant, and occupants.
   - Court date in PDF header: `FRIDAY, MAY 08, 2026 09:00AM`.
   - Judge names rotate (Inmon, Lumpkin-Dawson, Kasper, Murphy, Cherry, Barnett). Session times are 9AM and 1:30PM.
   - URL pattern: `01 MAY 2026 DISPO 9 AM INMON.pdf`, `01 MAY 2026 DISPO 130 PM LUMPKIN-DAWSON.pdf`.
   - Calendar page at `cobbcounty.gov/magistrate-court/magistrate-court-calendars` lists all available PDFs. PDFs going back ~30 days confirmed live.

3. **Judicial records search** — `https://courts.cobbcounty.gov/MagistrateCourtRecordSearch`
   - Supports search by name, case number, or citation number.
   - Disclaimer confirms addresses are stored: *"The addresses displayed may be the address associated to the party at the time the case was filed."*
   - Also warns: *"Users formulating or constructing their own search or query URLs in an attempt to directly access the database or documents will be permanently denied access to the system without notice."*
   - **Do not automate bulk case-number lookups against this system.**

**Fields confirmed from DISPO PDFs:**
| Field | Available | Source |
|---|---|---|
| `case_number` | ✓ | PDF |
| `landlord_name` | ✓ | PDF |
| `tenant_name` | ✓ | PDF |
| `court_date` | ✓ | PDF header |
| `filing_date` | ✗ | Not in PDF or XML |
| `property_address` | ✗ | Records search only (bulk use prohibited) |

**Address enrichment path:** BatchData — same per-lead cost as other states. Only `MD`-type cases (caseType=MD) from infax XML are dispossessory.

**Recommended scraper approach:**
1. Fetch calendar page to discover all DISPO PDF links for the rolling 7-day window.
2. Download each DISPO PDF, parse with pdfplumber.
3. De-duplicate by case number (multiple sessions may list the same case).
4. Pass to pipeline for BatchData address enrichment.

**Decision:** Buildable source. Recommend as the first GA Magistrate scraper target.

---

### Gwinnett County Magistrate Court

**Status:** Red — requires free account registration as of March 2023.

**Portal:** `https://portal-gagwinnett.tylertech.cloud/Portal` (Tyler Odyssey)

**Finding (2026-05-13):** Login page confirms: "Effective March 3, 2023 all users must register for a free account to search the Gwinnett County Portal." Registration wall blocks unauthenticated enumeration.

**Decision:** Skip for now. Do not build without a bypass or official public extract.

---

### DeKalb County Magistrate Court

**Status:** Unclear — county government portal exists but not yet tested.

**Finding (2026-05-13):** Official magistrate site is `dekalbcountymagistratecourt.com`. Case lookup page at `dekalbcountymagistratecourt.com/find-your-case/` references "DeKalb County Government Portal" for case search but appears to require e-file registration. No public calendar or extract found.

**Decision:** Low priority. Investigate only if Cobb scraper proves insufficient volume.

---

### Fulton County Magistrate Court

**Status:** Unclear — official site timed out during research.

**Finding (2026-05-13):** Official site is `magistratefulton.org`. Homepage timed out during automated scrape. `fultonclerk.org/eServices` references judicial records search. Georgia Courts `georgiacourts.gov/eaccess-court-records/` routes to Tyler/re:SearchGA (requires account).

**Decision:** Low priority. Test `magistratefulton.org` manually to see if a public calendar or date-enumerable docket exists before building.

---

## Tarrant County JP Courts — odyssey.tarrantcounty.com

**Status:** Yellow — Odyssey portal has JP court calendar but needs case-type/date filtering test.

**Portal:** `https://odyssey.tarrantcounty.com/PublicAccess/default.aspx`

**Finding (2026-05-13):** Tyler Technologies Odyssey Public Access. JP courts are selectable (JP No. 1 through 8). Interface offers "Case Records Search" and "Court Calendar" options. Court calendar may support date-enumerable case listing similar to Clark County NV CourtView. Whether results expose defendant address or can be filtered to eviction cases without a name is not yet confirmed.

**Decision:** Test the Court Calendar option for JP courts against a filing-date range without providing a name. If it works and exposes case number + party names (as with Clark NV), classify as candidate. Address situation (same as Clark NV: likely not exposed) would require BatchData.

---

## Dallas County JP Courts — dallascounty.org

**Status:** Red — no public extract found.

**Finding (2026-05-13):** Individual JP court pages exist per precinct (JP 1-1, 1-2, 3-1, 4-1). Each has eviction info but links to eFile Texas for new filings. Public access page at `dallascounty.org/services/public-access.php` covers felony/misdemeanor documents — not JP civil. No date-enumerable JP eviction extract found.

**Decision:** Skip unless a public filing extract or calendar is found.

---

## Bexar County JP Courts — bexar.org

**Status:** Red — no public extract found.

**Finding (2026-05-13):** Bexar reports page loaded blank (JS-heavy). No JP eviction CSV extract found matching Harris County model. Third-party sites (`bexar-evictions.com`, Texas Housers mapping) exist but are not primary court data sources for automation.

**Decision:** Skip unless a public filing extract is found.
