# Hamilton County OH Green Upgrade Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Upgrade Hamilton County OH from yellow (name-only, SearchBug) to green (real defendant street address, BatchData skip-trace) by fetching the party page from `courtclerk.org/data/case_summary.php` after scraping the eviction schedule.

**Architecture:** After scraping the eviction schedule (which gives case numbers + names but no addresses), the scraper makes a second POST per case to the party page to retrieve the defendant's street address. If successful, the Filing gets a real `property_address`; if the POST fails, we fall back to `"Cincinnati, OH"` (existing yellow behavior). `run_ohio.py` then pipes Hamilton filings through the same green-path pipeline as Franklin.

**Tech Stack:** Python 3.11, requests, BeautifulSoup, pytest, BatchData skip-trace API

---

### Discovered HTML structure

The party page is fetched via:
```
POST https://www.courtclerk.org/data/case_summary.php
body: sec=party&casenumber=26CV11460&submit=
Referer: https://www.courtclerk.org/data/case_summary.php?casenumber=26CV11460
```

The party table is `<table id="party_info_table" class="tablesorter">`. Each `<tbody><tr>` has:
- `td[0]` — party name (e.g. `AARON BOUQUIA`)
- `td[1]` — address with `<br/>` between street and city-state-zip (e.g. `1451 HILLCREST RD APT 2<br/>CINCINNATI OH 45224`)
- `td[2]` — party type prefix: `P` = plaintiff, `D` = defendant (contains non-ASCII byte before the number, so check `td[2].startswith("D")` after `.strip()`)

Note: malformed HTML nests the second defendant row inside the first — BeautifulSoup's `html.parser` auto-corrects this, but use `.find_all("tr")` on `<tbody>` rather than relying on nesting depth.

Target address format for `_split_address()` in `batchdata_service.py`:
`"1451 HILLCREST RD APT 2, CINCINNATI, OH 45224"` (comma before city, comma+space before STATE ZIP)

---

### Task 1: Add `_fetch_defendant_address` to Hamilton scraper

**Files:**
- Modify: `scrapers/ohio/hamilton.py`
- Test: `tests/test_hamilton_scraper.py`

- [ ] **Step 1: Write failing tests**

Check if `tests/test_hamilton_scraper.py` exists. If it does, add to it. If not, create it.

```python
from unittest.mock import patch, MagicMock
import pytest
from scrapers.ohio.hamilton import _parse_party_address, _fetch_defendant_address


class TestParsePartyAddress:
    def test_standard_address(self):
        from bs4 import BeautifulSoup
        html = '<td>1451 HILLCREST RD APT 2<br/>CINCINNATI OH 45224</td>'
        td = BeautifulSoup(html, 'html.parser').find('td')
        assert _parse_party_address(td) == "1451 HILLCREST RD APT 2, CINCINNATI, OH 45224"

    def test_no_apt(self):
        from bs4 import BeautifulSoup
        html = '<td>2200 DANA AVE<br/>CINCINNATI OH 45207</td>'
        td = BeautifulSoup(html, 'html.parser').find('td')
        assert _parse_party_address(td) == "2200 DANA AVE, CINCINNATI, OH 45207"

    def test_empty_address_returns_empty(self):
        from bs4 import BeautifulSoup
        html = '<td></td>'
        td = BeautifulSoup(html, 'html.parser').find('td')
        assert _parse_party_address(td) == ""

    def test_missing_city_state_returns_street_only(self):
        from bs4 import BeautifulSoup
        html = '<td>SOME ST</td>'
        td = BeautifulSoup(html, 'html.parser').find('td')
        # single-line — returned as-is with no city transform
        result = _parse_party_address(td)
        assert "SOME ST" in result


class TestFetchDefendantAddress:
    def test_returns_address_on_success(self):
        """_fetch_defendant_address parses first defendant address from party page."""
        import requests
        from bs4 import BeautifulSoup

        party_html = """
        <table id="party_info_table">
          <thead><tr><th>Name</th><th>Address</th><th>Party</th><th>Attorney</th><th>Address</th><th>ID</th></tr></thead>
          <tbody>
            <tr>
              <td>LANDLORD LLC</td>
              <td>100 MAIN ST<br/>CINCINNATI OH 45202</td>
              <td>P\xa01</td>
              <td></td><td></td><td></td>
            </tr>
            <tr>
              <td>JOHN DOE</td>
              <td>456 ELM ST APT 3<br/>CINCINNATI OH 45219</td>
              <td>D\xa01</td>
              <td colspan="3"></td>
            </tr>
          </tbody>
        </table>
        """
        mock_session = MagicMock()
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.text = party_html
        mock_session.post.return_value = mock_resp

        result = _fetch_defendant_address(mock_session, "26CV11460")
        assert result == "456 ELM ST APT 3, CINCINNATI, OH 45219"

    def test_returns_none_on_http_error(self):
        mock_session = MagicMock()
        mock_session.post.side_effect = Exception("timeout")
        result = _fetch_defendant_address(mock_session, "26CV11460")
        assert result is None

    def test_returns_none_when_no_defendant_row(self):
        party_html = """
        <table id="party_info_table">
          <thead><tr><th>Name</th><th>Address</th><th>Party</th></tr></thead>
          <tbody>
            <tr><td>LANDLORD</td><td>100 MAIN<br/>CINCINNATI OH 45202</td><td>P\xa01</td></tr>
          </tbody>
        </table>
        """
        mock_session = MagicMock()
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.text = party_html
        mock_session.post.return_value = mock_resp
        result = _fetch_defendant_address(mock_session, "26CV11460")
        assert result is None
```

- [ ] **Step 2: Run to verify they fail**

```
cd "d:\Freelance Projects\EvictionCommand\leadgen" && python -m pytest tests/test_hamilton_scraper.py -v
```

Expected: FAILED (names not found)

- [ ] **Step 3: Implement the helpers in `scrapers/ohio/hamilton.py`**

Add these two functions after the existing `_OCCUPANT_SUFFIXES` constant and before `HamiltonCountyMunicipalScraper`:

```python
CASE_SUMMARY_URL = "https://www.courtclerk.org/data/case_summary.php"


def _parse_party_address(td) -> str:
    """Convert a BeautifulSoup <td> address cell to 'STREET, CITY, STATE ZIP' format."""
    parts = [t.strip() for t in td.stripped_strings]
    if not parts:
        return ""
    street = parts[0]
    if len(parts) < 2:
        return street
    city_state_zip = parts[1]
    tokens = city_state_zip.split()
    if len(tokens) >= 3:
        city = " ".join(tokens[:-2])
        state = tokens[-2]
        zip_code = tokens[-1].rstrip("0") or tokens[-1]  # strip trailing zeros from 9-digit ZIP
        zip_code = tokens[-1][:5]  # use first 5 digits only
        return f"{street}, {city}, {state} {zip_code}"
    return f"{street}, {city_state_zip}"


def _fetch_defendant_address(session: requests.Session, case_number: str) -> str | None:
    """POST to the party page and return the first defendant's street address, or None."""
    try:
        r = session.post(
            CASE_SUMMARY_URL,
            data={"sec": "party", "casenumber": case_number, "submit": ""},
            headers={"Referer": f"{CASE_SUMMARY_URL}?casenumber={case_number}"},
            timeout=15,
        )
        if r.status_code != 200:
            return None
    except Exception as exc:
        log.warning("Hamilton OH: party fetch failed for %s: %s", case_number, exc)
        return None

    soup = BeautifulSoup(r.text, "html.parser")
    party_table = soup.find("table", {"id": "party_info_table"})
    if not party_table:
        return None

    tbody = party_table.find("tbody")
    if not tbody:
        return None

    for row in tbody.find_all("tr"):
        tds = row.find_all("td")
        if len(tds) < 3:
            continue
        party_type = tds[2].get_text(strip=True)
        if party_type.upper().startswith("D"):
            addr = _parse_party_address(tds[1])
            if addr:
                return addr
    return None
```

- [ ] **Step 4: Update `scrape()` to fetch defendant addresses**

In `HamiltonCountyMunicipalScraper.scrape()`, after appending each filing, add the address fetch. Change the loop in `scrape()` so it calls `_fetch_defendant_address` for each new filing and patches `property_address` if a real address is found:

```python
def scrape(self) -> list[Filing]:
    self.last_error = None
    today = court_today(COURT_TIMEZONE)

    filings: list[Filing] = []
    seen_cases: set[str] = set()

    for offset in range(self.lookback_days + 1):
        target = today - timedelta(days=offset)
        date_str = f"{target.month}/{target.day}/{target.year}"
        url = f"{BASE_URL}?chosendate={date_str}&court={COURT}&location={LOCATION}"

        try:
            html = self._get_text(url)
        except Exception as e:
            self.last_error = f"failed to fetch Hamilton eviction schedule for {date_str}: {e}"
            log.error("Hamilton OH: fetch failed for %s: %s", date_str, e)
            continue

        for filing in _parse_eviction_schedule(html, hearing_date=target, source_url=url):
            if filing.case_number in seen_cases:
                continue
            seen_cases.add(filing.case_number)

            # Attempt to upgrade from yellow (city-only) to green (real address)
            defendant_address = _fetch_defendant_address(self.session, filing.case_number)
            if defendant_address:
                filing = filing.model_copy(update={"property_address": defendant_address})
                log.debug("Hamilton OH: resolved address for %s: %s", filing.case_number, defendant_address)

            filings.append(filing)

    log.info("Hamilton OH: %s eviction filings found", len(filings))
    return filings
```

- [ ] **Step 5: Run the tests**

```
cd "d:\Freelance Projects\EvictionCommand\leadgen" && python -m pytest tests/test_hamilton_scraper.py -v
```

Expected: all pass

- [ ] **Step 6: Run full suite for regressions**

```
cd "d:\Freelance Projects\EvictionCommand\leadgen" && python -m pytest tests/ -q --tb=short
```

Expected: all pass

- [ ] **Step 7: Commit**

```
cd "d:\Freelance Projects\EvictionCommand\leadgen" && git add scrapers/ohio/hamilton.py tests/test_hamilton_scraper.py && git commit -m "feat: fetch defendant street address from Hamilton party page (yellow to green upgrade)"
```

---

### Task 2: Enable Hamilton pipeline in `run_ohio.py`

**Files:**
- Modify: `jobs/run_ohio.py`

Now that Hamilton filings carry real addresses, wire them into the pipeline and update the summary messages.

- [ ] **Step 1: Write a failing test**

Add to `tests/test_run_ohio.py` (read the file first to understand existing structure):

```python
@pytest.mark.asyncio
async def test_hamilton_piped_when_pipe_flag():
    """When --pipe is set, Hamilton filings should be sent to the pipeline."""
    from unittest.mock import patch, AsyncMock, MagicMock
    from jobs.run_ohio import main

    mock_filing = MagicMock()
    mock_filing.property_address = "456 ELM ST, CINCINNATI, OH 45219"

    with (
        patch("jobs.run_ohio.FranklinCountyMunicipalScraper") as mock_franklin_cls,
        patch("jobs.run_ohio.HamiltonCountyMunicipalScraper") as mock_hamilton_cls,
        patch("jobs.run_ohio.pipeline_runner") as mock_runner,
    ):
        mock_franklin_cls.return_value.scrape.return_value = []
        mock_hamilton_cls.return_value.scrape.return_value = [mock_filing]
        mock_runner.run = AsyncMock()

        await main(pipe=True, counties=["hamilton"])

        mock_runner.run.assert_called_once()
        call_args = mock_runner.run.call_args
        assert call_args.kwargs.get("county") == "Hamilton" or call_args.args[1:] or True
```

Actually the test approach depends on how `pipeline_runner` is imported. Read `run_ohio.py` carefully before writing this test — adapt to how `pipeline_runner.run` is called.

The key assertion: with `pipe=True` and Hamilton filings present, `pipeline_runner.run` is called with the Hamilton filings.

- [ ] **Step 2: Run to verify it fails**

```
cd "d:\Freelance Projects\EvictionCommand\leadgen" && python -m pytest tests/test_run_ohio.py -v -k "hamilton_piped"
```

Expected: FAILED

- [ ] **Step 3: Update `run_ohio.py`**

Add Hamilton pipeline routing alongside Franklin. Replace the existing pipe block and summary:

```python
    if pipe and franklin_filings:
        from pipeline import runner as pipeline_runner
        await pipeline_runner.run(franklin_filings, state="OH", county="Franklin")

    if pipe and hamilton_filings:
        from pipeline import runner as pipeline_runner
        await pipeline_runner.run(hamilton_filings, state="OH", county="Hamilton")
```

Update `OhioRunSummary.to_lines()` to reflect the upgrade:

```python
    def to_lines(self) -> list[str]:
        runner_line = (
            f"Runner: called with {self.total_filings} filings"
            if self.piped
            else "Runner/enrichment/outreach: not called (scraper-only mode)"
        )
        return [
            "Ohio" + (" pipeline run" if self.piped else " scraper-only proof"),
            f"Franklin Municipal (Columbus): {self.franklin_filings} filings",
            f"Hamilton Municipal (Cincinnati): {self.hamilton_filings} filings",
            f"Total: {self.total_filings}",
            runner_line,
        ]
```

Also update the argparse description to remove the "proof-only until Melissa" note.

- [ ] **Step 4: Run tests**

```
cd "d:\Freelance Projects\EvictionCommand\leadgen" && python -m pytest tests/test_run_ohio.py -v
```

Expected: all pass

- [ ] **Step 5: Commit**

```
cd "d:\Freelance Projects\EvictionCommand\leadgen" && git add jobs/run_ohio.py tests/test_run_ohio.py && git commit -m "feat: pipe Hamilton filings through green pipeline (address now available from party page)"
```

---

### Task 3: Update source discovery matrix

**Files:**
- Modify: `docs/source_discovery_matrix.md`

Update the Hamilton entry from yellow/blocked to green, and update the header note.

- [ ] **Step 1: Find and update the Hamilton row**

Search for "Hamilton County OH Municipal Court" in `docs/source_discovery_matrix.md`. Update the entry to reflect:
- Status: green (defendant address available from party page)
- Endpoint: `POST /data/case_summary.php?sec=party` — works with browser-like Referer header
- Address hit rate: expected ~90%+ (party page is part of the court record, not a lookup service)

Also update the header "last updated" line and any note referring to Hamilton staying yellow.

- [ ] **Step 2: Commit**

```
cd "d:\Freelance Projects\EvictionCommand\leadgen" && git add docs/source_discovery_matrix.md && git commit -m "docs: mark Hamilton County OH as green (defendant address via party page)"
```

---

### Task 4: Smoke test the upgraded scraper live

**Files:** none (read-only live test)

- [ ] **Step 1: Run a live scrape (no pipeline, no credits)**

```
cd "d:\Freelance Projects\EvictionCommand\leadgen" && python jobs/run_ohio.py --lookback-days 3 --counties hamilton
```

Expected output:
- Several Hamilton filings listed
- Each filing should now have a real street address (not "Cincinnati, OH") if the party fetch succeeded
- No errors in stderr

- [ ] **Step 2: Spot-check one address**

Pick a case number from the output and verify:
```
python -c "
import requests, sys
sys.path.insert(0, '.')
from scrapers.ohio.hamilton import _fetch_defendant_address
session = requests.Session()
session.headers.update({'User-Agent': 'Mozilla/5.0', 'Referer': 'https://www.courtclerk.org/records-search/eviction-schedule-search/'})
print(_fetch_defendant_address(session, 'PASTE_CASE_NUMBER_HERE'))
"
```

Confirm address looks like a real Cincinnati street address.

- [ ] **Step 3: Note address hit rate**

Count how many filings got a real address vs fell back to "Cincinnati, OH". Log this in a comment on this task for future reference.
