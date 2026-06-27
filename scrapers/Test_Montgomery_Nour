import os
import re
import csv
import logging
import asyncio
from datetime import datetime
from bs4 import BeautifulSoup
from playwright.async_api import async_playwright

# Setup application logs
logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
log = logging.getLogger(__name__)

# Constants matching Montgomery County Odyssey Infrastructure
MAIN_URL = "https://odyssey.mctx.org/Unsecured/default.aspx"
SEARCH_BASE = "https://odyssey.mctx.org/Unsecured"
# Write the CSV next to this script file, regardless of where Python is launched from
OUTPUT_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "court_leads.csv")

_VS_RE = re.compile(r"\s+vs\.\s+", flags=re.IGNORECASE)

def parse_calendar_results(html_content: str) -> list[dict]:
    """Parses the Court Calendar Results grid.

    Confirmed column layout (11 tds per data row):
      td[1]  = case number (contains the CaseDetail link)
      td[8]  = hearing date  e.g. "05/29/2026"
      td[9]  = hearing time  e.g. "9:00 AM"
      td[10] = hearing type  e.g. "Pre-Trial Conference"
    """
    soup = BeautifulSoup(html_content, "html.parser")
    records = []

    if "exceeded" in soup.get_text().lower() or "too many matches" in soup.get_text().lower():
        log.warning("Search exceeded server row cap — results may be truncated.")

    for tr in soup.find_all("tr"):
        tds = tr.find_all("td")
        if len(tds) < 11:
            continue

        # Case link lives in td[1]
        case_link = tds[1].find("a", href=re.compile(r"CaseDetail\.aspx\?CaseID=\d+"))
        if not case_link:
            continue

        case_number = case_link.get_text(strip=True)
        relative_href = case_link.get("href", "")
        case_url = (f"{SEARCH_BASE}/{relative_href}"
                    if not relative_href.startswith("http") else relative_href)

        hearing_date = tds[8].get_text(strip=True) or "Unknown"
        hearing_time = tds[9].get_text(strip=True) or "Unknown"
        hearing_type = tds[10].get_text(strip=True) or "Unknown"

        records.append({
            "case_number": case_number,
            "case_url": case_url,
            "hearing_date": f"{hearing_date} {hearing_time}".strip(),
            "hearing_type": hearing_type,
        })

    log.info(f"parse_calendar_results: extracted {len(records)} cases from HTML.")
    return records


def parse_case_header(html_content: str) -> dict:
    """Extracts Case Type, Date Filed, and Location from the detail page header.

    The page renders label/value pairs in dedicated 2-cell rows:
      <tr><td>Case Type:</td>   <td>Post Judgment Action Modification Custody</td></tr>
      <tr><td>Date Filed:</td>  <td>04/02/2012</td></tr>
      <tr><td>Location:</td>    <td>County Court at Law #3</td></tr>
    """
    soup = BeautifulSoup(html_content, "html.parser")
    meta = {"case_type": "Unknown", "date_filed": "Unknown", "court_location": "Unknown"}
    label_map = {
        "case type:": "case_type",
        "date filed:": "date_filed",
        "location:": "court_location",
    }
    for tr in soup.find_all("tr"):
        cells = tr.find_all(["td", "th"])
        if len(cells) == 2:
            label = cells[0].get_text(strip=True).lower()
            value = cells[1].get_text(strip=True)
            if label in label_map and value:
                meta[label_map[label]] = value
    return meta


# Role keywords in Montgomery County Odyssey party headers
_PARTY_ROLES = re.compile(
    r"^(DEFENDANT|PLAINTIFF|PETITIONER|RESPONDENT|APPLICANT|COUNTER.DEFENDANT|"
    r"COUNTER.PLAINTIFF|INTERVENOR|GARNISHEE|TRUSTEE|RECEIVER|STATE OF TEXAS)",
    re.IGNORECASE
)
# Stop collecting address lines when we hit these
_STOP_WORDS = re.compile(
    r"^(SID:|Other Agency|DOB:|Male|Female|Pro Se|Attorney|Retained|Appointed|Bar |Phone|Fax)",
    re.IGNORECASE
)

def parse_case_detail_parties(html_content: str) -> list[dict]:
    """Parses a Register of Actions page for party names and addresses.

    Confirmed Montgomery County Odyssey HTML layout:
      Row A: <th id="PIrXX">ROLE</th>  <th id="PIrYY">Party Name</th>  ...  (rowspan=2)
      Row B: <td headers="PIrXX PIrYY">Address line 1<br>City, ST ZIP</td>

    We find all <th class="ssTableHeader"> whose id matches PIr[0-9]{2} and text
    matches a role keyword. The party name is the next <th> in the same row.
    The address comes from the immediately following <tr>'s <td headers="...">.
    """
    soup = BeautifulSoup(html_content, "html.parser")
    extracted_parties = []

    all_rows = soup.find_all("tr")
    for i, tr in enumerate(all_rows):
        # Role label is always in a <th class="ssTableHeader" id="PIrNN">
        role_th = tr.find("th", id=re.compile(r"^PIr\d"), class_="ssTableHeader")
        if not role_th:
            continue

        role_text = role_th.get_text(strip=True)
        if not _PARTY_ROLES.match(role_text):
            continue

        role_id = role_th.get("id", "")  # e.g. "PIr01"

        # Party name: the next <th> in the same row
        all_ths = tr.find_all("th")
        party_name = ""
        for th in all_ths:
            if th.get("id", "") != role_id:
                name_candidate = th.get_text(strip=True)
                if name_candidate:
                    party_name = name_candidate
                    break

        if not party_name:
            continue

        # Address: in the NEXT row, a <td> whose headers attr references role_id
        address = "No Address Listed"
        if i + 1 < len(all_rows):
            next_tr = all_rows[i + 1]
            for td in next_tr.find_all("td"):
                headers = td.get("headers", "")
                if role_id in headers:
                    raw_lines = [l.strip() for l in td.get_text("\n").split("\n") if l.strip()]
                    addr_lines = []
                    for line in raw_lines:
                        if _STOP_WORDS.match(line):
                            break
                        addr_lines.append(line)
                    if addr_lines:
                        address = ", ".join(addr_lines)
                    break

        extracted_parties.append({
            "party_role": role_text.upper(),
            "party_name": party_name,
            "address": address,
        })

    return extracted_parties


def save_to_csv(flat_records: list[dict], filename: str):
    """Saves flattened dataset dictionaries straight into a CSV spreadsheet document."""
    fields = [
        "Case Number", "Case Type", "Date Filed", "Court Location",
        "Party Role", "Party Name", "Address",
        "Hearing Date", "Hearing Type", "Source URL"
    ]
    
    try:
        with open(filename, mode="w", newline="", encoding="utf-8") as f:
            writer = csv.DictWriter(f, fieldnames=fields)
            writer.writeheader()
            
            for item in flat_records:
                writer.writerow({
                    "Case Number":    item["case_number"],
                    "Case Type":      item.get("case_type", "Unknown"),
                    "Date Filed":     item.get("date_filed", "Unknown"),
                    "Court Location": item.get("court_location", "Unknown"),
                    "Party Role":     item["party_role"],
                    "Party Name":     item["party_name"],
                    "Address":        item["address"],
                    "Hearing Date":   item["hearing_date"],
                    "Hearing Type":   item["hearing_type"],
                    "Source URL":     item["case_url"]
                })
        log.info(f"Successfully saved tracking log records sheet directly to: {filename}")
    except Exception as e:
        log.error(f"Failed writing database structure output to disk storage: {e}")


async def main():
    # Setting an tight lookback target day window to completely avoid hitting the 200-row cap limit.
    target_day = "05/29/2026" 
    flattened_lead_rows = []
    
    async with async_playwright() as p:
        log.info("Launching Playwright automated browser instance...")
        browser = await p.chromium.launch(headless=False)  # Set to True to background the execution run
        context = await browser.new_context()
        page = await context.new_page()
        
        # --- PHASE 1: LOAD HOMEPAGE AND TRIGGER COURT CALENDAR VIA JS ---
        # The site renders search forms inside a frame via LaunchSearch().
        # We must load the homepage first, then click the link to fire the JS.
        log.info(f"Loading homepage: {MAIN_URL}")
        await page.goto(MAIN_URL)
        await page.wait_for_load_state("networkidle")

        # Click the "Court Calendar" link — it calls LaunchSearch('Search.aspx?ID=900', ...)
        # which loads the search form into the content frame.
        log.info("Clicking Court Calendar link to trigger LaunchSearch JS...")
        await page.click("a:has-text('Court Calendar')")
        await page.wait_for_load_state("networkidle")

        # The form loads inside a frame — find it by URL pattern
        search_frame = None
        for frame in page.frames:
            if "Search.aspx" in frame.url or "ID=900" in frame.url:
                search_frame = frame
                break

        # Fallback: if no named frame matched, try the main page itself
        if search_frame is None:
            log.warning("Could not locate a Search.aspx frame; attempting on main page.")
            search_frame = page

        # --- PHASE 2: CONFIGURE DATE CRITERIA SEARCH FORM ---
        log.info("Selecting 'Date Range' search mode...")

        # Click the DateRange radio button directly by its known ID
        await search_frame.wait_for_selector("#DateRange", timeout=10000)
        await search_frame.click("#DateRange")

        # Give the JS panel time to reveal the date fields
        await asyncio.sleep(1.0)

        # Check the Civil category checkbox (id=chkDtRangeCivil)
        civil_cb = search_frame.locator("#chkDtRangeCivil")
        if not await civil_cb.is_checked():
            await civil_cb.click()
            log.info("Checked Civil category.")

        # Fill the date range fields using confirmed IDs from DOM inspection
        await search_frame.fill("#DateSettingOnAfter", target_day)
        await search_frame.fill("#DateSettingOnBefore", target_day)
        log.info(f"Date range set to: {target_day}")

        # Submit using confirmed submit button ID
        log.info("Submitting search...")
        await search_frame.click("#SearchSubmit")
        await page.wait_for_load_state("networkidle")

        # Ensure results grid has stabilized — check frame first, fallback to page
        try:
            await search_frame.wait_for_selector("table", timeout=20000)
            grid_html = await search_frame.content()
        except Exception:
            await page.wait_for_selector("table", timeout=20000)
            grid_html = await page.content()

        # --- PHASE 3: EXECUTE GRID SCRAPE WITH FULL PAGINATION ---
        all_cases = []
        page_num = 1

        # Odyssey pager: rows of page-number links sit below the results table.
        # The "next" link has the text ">" (right-angle bracket, sometimes &gt;).
        # We find it in the HTML via BeautifulSoup and navigate by href.
        def find_next_page_url(html: str, base: str) -> str | None:
            s = BeautifulSoup(html, "html.parser")
            for a in s.find_all("a", href=True):
                txt = a.get_text(strip=True)
                if txt in (">", "›", "Next"):
                    href = a["href"]
                    if href.startswith("http"):
                        return href
                    return base.rstrip("/") + "/" + href.lstrip("/")
            return None

        while True:
            cases_on_page = parse_calendar_results(grid_html)
            log.info(f"Page {page_num}: found {len(cases_on_page)} cases.")
            all_cases.extend(cases_on_page)

            next_url = find_next_page_url(grid_html, SEARCH_BASE)
            if next_url:
                log.info(f"Navigating to page {page_num + 1}: {next_url}")
                await page.goto(next_url)
                await page.wait_for_load_state("networkidle")
                await asyncio.sleep(0.8)
                grid_html = await page.content()
                # Re-sync search_frame after navigation
                search_frame = page
                for frame in page.frames:
                    if "Search.aspx" in frame.url or "Results" in frame.url:
                        search_frame = frame
                        break
                page_num += 1
            else:
                log.info(f"No more pages. Total cases collected: {len(all_cases)}")
                break

        log.info(f"Identified {len(all_cases)} total cases. Commencing detail parsing deep loop...")

        for case in all_cases:
            log.info(f"Deep crawling case: {case['case_number']}")
            await page.goto(case["case_url"])
            await page.wait_for_load_state("networkidle")
            detail_html = await page.content()
            case_meta = parse_case_header(detail_html)
            parties = parse_case_detail_parties(detail_html)
            log.info(f"  -> found {len(parties)} parties | {case_meta['case_type']}")
            for party in parties:
                flattened_lead_rows.append({**case, **case_meta, **party})
            await asyncio.sleep(1.2)

        await browser.close()
        log.info("Scraping pipeline finished.")
        
    # --- PHASE 4: DATA EXPORT ENGINE ---
    if flattened_lead_rows:
        save_to_csv(flattened_lead_rows, OUTPUT_FILE)
    else:
        log.warning("The execution loop closed without parsing valid lead strings.")

if __name__ == "__main__":
    asyncio.run(main())
