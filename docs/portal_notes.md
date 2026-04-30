# Court Portal Notes

## LA Superior Court — lacourt.ca.gov

**Status:** In discovery  
**Target:** Daily new filings register filtered to Unlawful Detainer

### Discovery Steps (run once, headed mode)
1. Navigate to https://www.lacourt.ca.gov/newfilings/ui/index.aspx
2. Identify: Does a "Case Type" or "Category" dropdown exist? What are its options?
3. Identify: Is there a date filter? Default to today?
4. Identify: CSS selector for each result row
5. Identify: Fields visible in the results list (case number, parties, address, filing date)
6. Click one result row — identify the case detail page URL pattern
7. On case detail page: identify selectors for court_date, landlord_name
8. Identify: Pagination — next button selector, total pages indicator
9. Note: Any CAPTCHA, rate limiting, or session token requirements?

### Confirmed Selectors (fill in after discovery)
| Field | Selector | Notes |
|---|---|---|
| Case type dropdown | TBD | |
| Date filter | TBD | |
| Result rows | TBD | |
| Case number in row | TBD | |
| Tenant name in row | TBD | |
| Property address in row | TBD | |
| Filing date in row | TBD | |
| Next page button | TBD | |
| Case detail: court date | TBD | |
| Case detail: landlord name | TBD | |

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
