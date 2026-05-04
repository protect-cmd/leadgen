# Court Portal Notes

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
| Case type dropdown | `select#casetype` | first non-zero option |
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

## San Diego Superior Court — sdcourt.ca.gov
**Status:** Not yet discovered  
**Discovery checklist:** Same 9 steps as LA above.

---

## Orange County Superior Court — occourts.org
**Status:** Not yet discovered

---

## Riverside Superior Court — riverside.courts.ca.gov
**Status:** Not yet discovered
