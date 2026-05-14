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

## Franklin County OH Municipal Court — fcmcclerk.com

**Status:** Green address-bearing CSV source as of 2026-05-14.

**Source:**
- Monthly Civil F.E.D. eviction reports: https://www.fcmcclerk.com/reports/evictions

**Findings:**
- FCMC publishes monthly Civil F.E.D. eviction case lists as CSV files for the current month and prior 12 months.
- The page states the reports are created nightly.
- Confirmed CSV fields include case number, case file date, disposition fields, first plaintiff name/address, and first defendant name/address.
- Defendant address fields are `FIRST_DEFENDANT_ADDRESS_LINE_1`, `FIRST_DEFENDANT_ADDRESS_LINE_2`, `FIRST_DEFENDANT_CITY`, `FIRST_DEFENDANT_STATE`, and `FIRST_DEFENDANT_ZIP`.
- Clerk caveat: party mailing addresses may or may not be related to the property referenced in the complaint. Treat this as stronger than calendar-only data, but keep tenant-phone enrichment confidence and DNC gates.
- Live scraper-only smoke on 2026-05-14: `python scripts/smoke_scrapers.py --states franklin_oh --lookback-days 14` returned 660 filings.

**Implementation:**
- Scraper: `scrapers/ohio/franklin.py`
- Smoke alias: `franklin_oh`, `franklin`, `columbus`, `oh`
- Raw Supabase ingest schedule path: `scripts/push_franklin_filings.py --lookback-days 2 --yes-write-supabase`.
- This raw ingest path inserts/dedupes filings only and does not call enrichment, GHL, Bland, or outreach.
- Tenant phone proof on the latest 100 Franklin rows found 10 phones, all DNC clear. Keep paid enrichment and outreach gated until user approves cost/volume.

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

**Status:** Yellow source as of 2026-05-14. Hearing export confirmed Magistrate Court dispossessory volume, but no property/defendant address fields are exposed.

**Access:** Uses existing `RESEARCHGA_EMAIL` and `RESEARCHGA_PASSWORD` credentials. Do not add paid search/API products unless explicitly approved by the user.

**Current behavior:**
- Public home page redirects through Tyler/eFileGA identity.
- Login uses the `Sign in with Your eFileGA Account` path, then Tyler identity fields `#UserName` and `#Password`.
- Advanced Search > Hearings supports `Case Type` + `Hearing Date` filtering and CSV export.
- A 2026-05-14 export for dispossessory hearing types returned exactly 1,000 rows from a UI result count of ~2.0k, so the export path appears capped at 1,000 rows.
- The sample export deduped to 946 unique case numbers: 915 Fulton Magistrate rows and 85 Spalding Magistrate rows.
- A scraper-only smoke on 2026-05-14 using 7-day hearing-date chunks returned 1,496 hearing rows and 1,361 deduped filings for the default 2-day lookback plus 45-day hearing lookahead.
- Export fields include hearing date/type/location/result, case description, case number, case location, case type, filed date, case status, attorneys, and judge.
- Export/search data does **not** include property address, defendant address, phone, or email.
- Search limits may apply; avoid repeated broad smoke runs unless needed.

**Pipeline status:**
- `scrapers/georgia/researchga.py` now uses the Hearings search shape with dispossessory case-type values captured from the export UI and chunks hearing-date searches into 7-day windows to avoid Tyler's 1,000-row cap.
- Rows are deduped by case number, parse landlord/tenant from `Case Description` or API party arrays, and keep `property_address="Unknown"` to maximize filing volume.
- Keep re:SearchGA scraper-only / at bay until Melissa Personator Search tenant enrichment is implemented and proven. `jobs/run_georgia.py` defaults to scraper-only mode; `--pipe` must not be used for production until Melissa match quality is approved.
- `scripts/smoke_scrapers.py --states georgia` supports scraper-only validation.
- Treat as a volume/tenant proof source until address/contact enrichment quality is validated.

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

**Status:** Yellow proof source as of 2026-05-13.

**Sources confirmed:**
- Official civil calendars page: `https://dekalbcountymagistratecourt.com/civil-matters/civil-calendars/`
- Public dispossessory PDFs under the "Dispossessory Calendars" section, with filenames such as `Civil-Dispo-05.12.26-CT2-1pm-Attorney-Calendar.pdf`.

**Fields confirmed from PDFs:**
| Field | Available | Notes |
|---|---|---|
| `case_number` | yes | Format like `26D08231`. |
| `landlord_name` | yes | Plaintiff/party column; may span multiple lines. |
| `tenant_name` | yes | Defendant/party column; occupant labels need stripping. |
| `court_date` | yes | Calendar header date. |
| `filing_date` | no | Use court date as proof placeholder. |
| `property_address` | no | Not exposed in PDFs. |

**Vantage/Melissa note:** DeKalb is a good tenant-volume proof once Melissa Personator is available, because PDFs provide tenant names and case context but not reliable address/contact fields. Keep the job scraper-only until Melissa matching rules or another address source is added.

**Current implementation:** `jobs/run_georgia_dekalb.py` is scraper-only by default. It can run with `--pipe`, but DeKalb should not be scheduled or piped into production until proof volume and tenant-enrichment quality are reviewed.

---

### Fulton County Magistrate Court

**Status:** Confirmed red as of 2026-05-14.

**Finding (2026-05-14):** `magistratefulton.org` is an informational-only site. The Tyler portal at `portal-gafulton.tdr.tylerhosting.cloud/portal` now explicitly requires login — registration is mandatory post-2025 system migration. The prior public records portal at `publicrecordsaccess.fultoncountyga.gov/Portal/` has a TLS certificate error. Previous timeout during 2026-05-13 research was caused by the system migration in progress at that time.

**Decision:** Skip permanently. Do not build a scraper for Fulton GA without a confirmed public-access path. If a public extract or no-login calendar emerges, reassess.

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

---

## Travis County TX JP Court — odysseypa.traviscountytx.gov

**Status:** Green — date-enumerable, no login, address likely present. Assessed 2026-05-14.

**Portal:** `https://odysseypa.traviscountytx.gov/JPPublicAccess/default.aspx`

**Findings (2026-05-14):**
- Tyler Odyssey JP Public Access. No login or registration required.
- Supports Advanced Filter options including date filed range — the same mechanism January Advisors uses to collect Travis County eviction data for Eviction Lab's Austin TX tracking dashboard.
- Platform is the same Tyler Odyssey stack as Harris County JP. Harris County's extract portal is a custom CSV form-POST system; Travis uses Odyssey search result pages — the scraper approach differs.
- Defendant address is expected in petition/case data (consistent with Harris County case detail behavior), but must be confirmed by inspecting a case detail page during scraper development.
- No native bulk CSV export. Rows must be enumerated by date range via Odyssey search results.

**Field mapping (expected, verify against actual response):**
| Field | Source | Notes |
|---|---|---|
| `case_number` | Odyssey case ID | |
| `landlord_name` | Plaintiff party | |
| `tenant_name` | Defendant party | Strip occupant suffixes |
| `filing_date` | Date Filed | |
| `court_date` | Hearing date | From case events |
| `property_address` | Defendant/petition address | Must confirm on detail page |
| `county` | `Travis` | Hardcoded |
| `state` | `TX` | Hardcoded |

**Live probe findings (2026-05-14):**
- Court Calendar Date Range search confirmed: `javascript:LaunchSearch('Search.aspx?ID=900', false, true, sbxControlID2)` loads form via AJAX into `default.aspx`. Form action correctly targets `Search.aspx?ID=900`.
- First probe returned 1,612 civil rows for May 7–14. Hearing types seen: Small Claims, Debt Claims. No Forcible Detainer or Eviction hearing type confirmed.
- Repeated automated POSTs throw HTTP 500 from `ODY-APP-P.travis.local` backend. Likely rate-limiting or VPN IP blocking on the application server.
- Session handling is AJAX-based: must navigate `default.aspx` → click Court Calendar link → wait for form AJAX load → fill form → submit. Cannot POST directly with requests library (VIEWSTATE is context-bound to default.aspx AJAX response).
- Case number format: `J1-CV-24-003871` = Precinct 1, Civil, filed 2024.

**Classification downgrade (2026-05-14):** Yellow — not green. Two gates remain:
1. Confirm Forcible Detainer / Eviction hearing type exists in the calendar.
2. Confirm server reliability for daily automation from a fixed IP (not VPN).

**Scraper approach (when both gates clear):**
- Playwright with session refresh on each run (navigate main → Court Calendar AJAX → fill → submit).
- Wait for form element visibility after AJAX load, not `networkidle`.
- 2-day date windows for Civil only (1,612 rows/7 days = ~230/day, well within limit).
- Dedupe by case number. Follow case detail for address if not in calendar row.
- Retry logic on 500 errors with 30s backoff.

**Production gate:**
- Confirm eviction hearing type label in the calendar.
- Run from a fixed residential or datacenter IP (not VPN) to confirm server reliability.
- Scraper-only smoke returns eviction rows with landlord and tenant names.
- Address confirmed on case detail page before wiring enrichment.
- TX ZIPs in allowlist before scheduling.

---

## Hamilton County OH Municipal Court (Cincinnati) — courtclerk.org

**Status:** Yellow / proof-only as implemented 2026-05-14. Dedicated eviction-by-date search works without login, but returned rows are address-absent.

**Portal:** `https://www.courtclerk.org/records-search/eviction-schedule-search/`
**Secondary (by name):** `https://www.courtclerk.org/records-search/eviction-schedules-for-public-parties/`

**Findings (2026-05-14):**
- Hamilton County Municipal Court Clerk has a dedicated eviction schedule search by date — the most purpose-built eviction date-search tool found across all assessed states.
- No login or registration required.
- An Ohio Court of Appeals ruling required Hamilton County clerk to rescind a restrictive public records policy and allow online access to all residential eviction cases regardless of age.
- Direct HTTP GET works with browser-like headers.
- Address exposure was checked in the schedule and case summary path; no property/defendant address is exposed by the implemented source.
- Tenant parser strips common occupant suffixes such as `ET AL`, `AND ALL OCCUPANTS`, and `AND ALL OTHER OCCUPANTS`.

**Field mapping (to confirm):**
| Field | Source | Notes |
|---|---|---|
| `case_number` | Court case number | |
| `landlord_name` | Plaintiff | |
| `tenant_name` | Defendant | |
| `filing_date` | Filed date | |
| `court_date` | Hearing date | Primary search driver |
| `property_address` | Not exposed | Set to `Unknown`; do not pipeline without a proven address/contact enrichment path. |
| `county` | `Hamilton` | Hardcoded |
| `state` | `OH` | Hardcoded |

**Scraper approach:**
- Test direct HTTP first with full browser headers (User-Agent, Accept, Referer, Accept-Language). If 403 persists, use Playwright for form submission.
- Filter by eviction case type + hearing date range.
- Dedupe by court case number.

**Live probe findings (2026-05-14):**
- Actual endpoint: `GET https://www.courtclerk.org/data/eviction_schedule.php?chosendate=M/D/YYYY&court=MCV&location=EVIM`
- HTTP 200, no auth, no bot blocking on direct requests with browser User-Agent. No Playwright needed.
- Live result for 5/13/2026: 57 unique case numbers, case type `G1-EVICTION` confirmed.
- Fields returned per case block: `Case #`, `Time`, `Plaintiff` (landlord), `Attorney for Plaintiff`, `Defendant` (tenant, may include "et al"), `Attorney for Defendant`, `Next Action`.
- Case format: `26CV13828` = Municipal Civil, filed 2026. Some older cases (`25CV31511`) from 2025 still on docket.
- Case detail via `GET /data/case_summary.php?casenumber=26CV13828&court[MCV]=on` returns: case number, court, case caption, judge, filed date, case type. No property address.
- No property address anywhere in schedule or case detail. The "By Parcel ID / Address" navigation link returned 404.
- Hearing days are not every day — eviction court sits specific days (May 13 had results, May 12 had none).

**Classification (2026-05-14):** Updated to yellow. Volume confirmed (57 cases/day), case type confirmed, no address. This is a `proof_only` source for phone outreach until Melissa Personator is wired.

**Scraper approach:**
- Direct HTTP GET to `eviction_schedule.php` endpoint — no Playwright, no session needed.
- Enumerate each day in the lookback window (use 7-day lookback to catch all hearing days).
- Parse HTML table rows into case blocks: case number, hearing date (from URL param), time, landlord, tenant, next action.
- Strip "et al" and occupant suffixes from tenant name.
- Look up filed date via `case_summary.php` for each case (or skip and use hearing date as `court_date`).
- Dedupe by case number.
- `property_address = "Unknown"` — no address source.

**Field mapping:**
| Field | Source | Notes |
|---|---|---|
| `case_number` | `Case #` cell | Format `26CV13828` |
| `landlord_name` | `Plaintiff` cell | |
| `tenant_name` | `Defendant` cell | Strip "et al" and occupant suffixes |
| `filing_date` | `case_summary.php` filed date | Optional — can skip to avoid per-case HTTP call |
| `court_date` | `chosendate` param | Hearing date (not filing date) |
| `property_address` | Not available | `Unknown` |
| `county` | `Hamilton` | Hardcoded |
| `state` | `OH` | Hardcoded |
| `notice_type` | `G1-EVICTION` | From case summary; hardcode as "Eviction" |
| `source_url` | `eviction_schedule.php` URL | Stable per date |

**Production gate:**
- Parser tests pass on real case blocks (two HTML table structures confirmed).
- Scraper-only smoke returns expected volume (50+ cases/week); a 2026-05-14 2-day smoke returned 163 filings.
- OH ZIPs must be in the allowlist before scheduling.
- Do not run enrichment or outreach until Melissa Personator is live and Hamilton County tenant name match quality is measured.
- Calendar-only rows are address-poor; keep out of automated tenant phone outreach until proof metrics are collected.

---

## Shelby County TN General Sessions (Memphis) — shelbygeneralsessions.com

**Status:** Yellow — download page exists and warrants hands-on browser test. Assessed 2026-05-14.

**Portal (case search):** `https://gscivildata.shelbycountytn.gov/pls/gnweb/ck_public_qry_main.cp_main_idx` (ACS CourtConnect)
**Download page:** `https://www.shelbygeneralsessions.com/115/Download-Case-Information`

**Findings (2026-05-14):**
- The ACS CourtConnect case search requires party name — not date-enumerable. Same backend as other TN counties.
- The official court website has a dedicated "Download Case Information" page that is unusual for Tennessee General Sessions courts. This page may offer a free bulk export of civil/eviction case data, or it may redirect to a clerk-registration-only form.
- Address exposure via CourtConnect is unlikely (ACS platform typically shows names, case numbers, dates, and case types without property address).
- If the download page is a free export with eviction cases, tenant name, and defendant/property address, this becomes the Nashville (Davidson) model applied to Memphis — Tennessee's second-largest city.

**Next step:** Open `shelbygeneralsessions.com/115/Download-Case-Information` in a browser and record: (1) whether a form or direct download appears, (2) whether registration is required, (3) what file format and fields are offered.

**Production gate:** Do not build until download page behavior is confirmed. If gated, reclassify as red.

---

## Cuyahoga County OH — Cleveland Housing Court

**Status:** Yellow — public HTML docket, tenant names and case numbers available, no address. Assessed 2026-05-14.

**Portal:** `https://www.clevelandhousingcourt.org/accessible-civil-docket`

**Findings (2026-05-14):**
- Accessible civil docket is a static HTML page, no login, updated weekly.
- Approximately 583 cases visible per two-week window, sorted by hearing date.
- Confirmed fields on docket: case number, plaintiff (landlord) name, defendant (tenant) name, hearing date, hearing time, case type (`Housing Eviction`, `Housing Default`).
- No property address or defendant address on the docket page.
- A criminal XLSX download exists; no equivalent civil case export found.
- Direct HTTP fetch works (no 403 or bot block encountered).

**Field mapping:**
| Field | Source | Notes |
|---|---|---|
| `case_number` | Docket row | |
| `landlord_name` | Plaintiff column | |
| `tenant_name` | Defendant column | Strip occupant suffixes |
| `court_date` | Hearing date column | |
| `filing_date` | Unknown | Not on docket; use `court_date` as placeholder |
| `property_address` | Not available | `Unknown` — enrichment needed |
| `county` | `Cuyahoga` | Hardcoded |
| `state` | `OH` | Hardcoded |

**Enrichment path:** Tenant name + county only — too ambiguous for automated phone outreach without a ZIP or address anchor. Source is proof/volume only until Melissa Personator is live and match quality is measured.

**Scraper approach:**
- Direct HTTP GET to docket URL. Parse HTML table rows.
- Weekly docket window — run with 14-day lookback to capture the rolling 2-week window.
- Dedupe by case number.

**Production gate:** Volume/proof source only. Calendar-only rows are address-poor; keep out of automated tenant phone outreach. Do not schedule enrichment until Melissa hit rate is measured on a Cleveland tenant name sample.

---

## Montgomery County OH — Dayton Municipal Court

**Status:** Yellow — filing date search confirmed on clerk portal. Address exposure unconfirmed. Assessed 2026-05-14.

**Portal:** `https://clerkofcourt.daytonohio.gov`
**Avoid:** `https://pro.mcohio.org` (PRO system — anti-scraping warning, requires prior approval for bulk data)

**Findings (2026-05-14):**
- Dayton Municipal Court Clerk portal explicitly lists "Filing Date" as one of its six search types (alongside Case Number, Ticket Number, Defendant Information, Defense Attorney, Forms & Costs).
- No login required on the clerk portal itself.
- The Montgomery County PRO system (`pro.mcohio.org`) explicitly warns: *"Efforts to mine large quantities of data from the PRO System without prior approval of the Clerk of Courts office will be detected and stopped."* Do not target PRO.
- Address exposure on clerk portal search results is unconfirmed.

**Scraper approach:**
- Use Dayton clerk portal (`clerkofcourt.daytonohio.gov`) filing date search only.
- Do not touch the PRO system.
- Confirm address field presence on result rows before building.

**Production gate:** Confirm address fields with a direct browser test. If no address, classify as proof/volume source pending Melissa enrichment.

---

## Williamson County TX JP Court — judicialrecords.wilco.org

**Status:** Yellow — same Tyler Odyssey stack as Travis/Harris, but `securepa` subdomain may gate JP records. Assessed 2026-05-14.

**Portal:** `https://judicialrecords.wilco.org`
**Concern:** `https://judicialrecords.wilco.org/securepa/` — labeled "Secure PA," may require login for JP civil cases.

**Findings (2026-05-14):**
- Same Tyler Odyssey platform as Travis County and Harris County JP. If the public Odyssey path is accessible without login for JP civil cases, this is effectively a Travis clone.
- The `securepa` subdomain is the primary unknown. Some Odyssey deployments gate civil/JP records behind registration.
- Help documentation describes Civil, Family & Probate and Court Calendar search options. No explicit confirmation that JP records are on the public vs. secure path.

**Next step:** Test `judicialrecords.wilco.org` public path for JP Forcible Detainer cases by date. If it returns results without login, classify as green candidate. If it redirects to `securepa`, classify as red.

**Production gate:** Do not build until access path is confirmed. Part of the Austin metro (Williamson County = Round Rock, Georgetown, Cedar Park) — meaningful eviction volume if accessible.

---

## Fort Bend County TX JP Court — tylerpaw.fortbendcountytx.gov

**Status:** Yellow — Tyler PAW exists, part of Houston metro, but no Harris-style bulk extract and date-filed behavior for civil cases is unconfirmed. Assessed 2026-05-14.

**Portal:** `https://tylerpaw.fortbendcountytx.gov/PublicAccess/`

**Findings (2026-05-14):**
- Tyler PAW (Public Access Web) system — different from Tyler Odyssey JP. No Harris-style `/PublicExtracts/` CSV download system found.
- Date Filed is a confirmed search parameter for criminal cases in the PAW system. Whether the same parameter works for civil/eviction cases is unconfirmed.
- Portal returned 522 error on direct fetch during research — intermittently unreachable.
- Fort Bend is part of the Houston metro (Sugar Land, Missouri City, Pearland). Meaningful eviction volume if accessible.

**Next step:** Test Tyler PAW civil case search with Date Filed filter for Forcible Detainer. If date-enumerable and exposes defendant address, classify as green.

**Production gate:** Do not build until civil date-filed search behavior is confirmed. If address is not exposed, add BatchData enrichment path same as Clark NV / Maricopa AZ.

---

## Pima County AZ — Consolidated Justice Court — jp.pima.gov

**Status:** Red — name or case number required, no date enumeration. Assessed 2026-05-14.

**Portal:** `https://www.jp.pima.gov/CaseSearch/`

**Findings (2026-05-14):**
- Case search supports three modes: By Name (last/first required), By Case Number, By Complaint Number. No filing date or hearing date range filter.
- Date-only enumeration is not possible — cannot batch-retrieve eviction cases without knowing names or case IDs.
- No bulk CSV export found.
- Pima restricts felony, juvenile, IAH, and OP records from online access; civil eviction access appears available by case but not bulk-enumerable.

**Decision:** Skip permanently. Pima AZ is not scrapeable by date. If a date-enumerable public calendar or court report is discovered later, reassess.

---

## Knox County TN — Civil Sessions Court — knoxcounty.org

**Status:** Yellow — public weekly PDF dockets, no login. Address unlikely. Paid sub for case detail. Assessed 2026-05-14.

**Portal:** `https://www.knoxcounty.org/civil/dockets.php`
**Paid sub (avoid):** Knox Circuit Records — $120/3-month subscription for case detail online access.

**Findings (2026-05-14):**
- Public weekly PDF dockets are available at no cost, no login, organized by hearing date. Links labeled "2026 Weekly Court Dates" link to dated PDF files (e.g., `DailyDkt20260512.pdf`).
- Dockets cover Civil Sessions Court which handles eviction/detainer cases in Knox County (Knoxville metro).
- Property address is not expected in the PDF docket — typical TN General Sessions dockets show party names, case numbers, and hearing times only.
- Case detail (including addresses) requires a $120/quarter subscription to Knox Circuit Records — do not pursue this paid path.

**Scraper approach:**
- Fetch docket page to enumerate current PDF links.
- Download and parse PDFs with pdfplumber — same approach as Cobb/DeKalb GA.
- Extract case number, landlord, tenant, hearing date.
- Set `property_address = "Unknown"`.

**Production gate:** Volume/proof source only. Address-absent; keep out of automated tenant phone outreach. Hold until Melissa Personator can be tested on Knox County tenant names. Do not pay the $120/quarter subscription to unlock address data — use Melissa enrichment instead.
