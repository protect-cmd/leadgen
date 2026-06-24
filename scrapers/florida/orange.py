from __future__ import annotations

import asyncio
import io
import logging
import os
import re
from datetime import date, datetime, timedelta

import httpx
import pdfplumber

from models.filing import Filing
from scrapers.base_scraper import BaseScraper
from scrapers.dates import court_today
from services.name_utils import clean_tenant_name

log = logging.getLogger(__name__)

# Portal: Orange County FL Clerk of Courts — myeclerk
# Search page exposes Case Type multiselect (Eviction = checkbox value "41")
# + DateFrom/DateTo text inputs (M/d/yy) + reCAPTCHA + Search button.
#
# The portal loads fine on a plain US IP (HTTP 200) — the ONLY gate is a
# Google reCAPTCHA v2 *checkbox* (not Enterprise) that keeps #caseSearch
# disabled until solved. BrightData's residential zone refuses this domain
# (classified "Government"), so we solve the captcha directly via a solver
# service (2Captcha / CapSolver) and inject the token. See _solve_recaptcha.
PORTAL_URL       = "https://myeclerk.myorangeclerk.com/"
CASE_SEARCH_URL  = "https://myeclerk.myorangeclerk.com/Cases/search"
DOC_BASE_URL     = "https://myeclerk.myorangeclerk.com"
SOURCE_URL       = CASE_SEARCH_URL
STATE            = "FL"
COUNTY           = "Orange"
COURT_TIMEZONE   = "America/New_York"
NOTICE_TYPE      = "Residential Eviction"

EVICTION_CASE_TYPE_VALUE = "41"   # Confirmed via DOM inspection (June 2026)

# reCAPTCHA v2 checkbox on the search form (confirmed via live DOM, June 2026).
# Used as a fallback only — _solve_recaptcha reads the live data-sitekey first.
RECAPTCHA_SITEKEY = "6LdtOBETAAAAABvi0Md4UUqb7GKfkRiUR6AsrFX-"

# Captcha solver config — env-gated so unit tests and the existing "external
# infra solves it" deployment keep working with no key set.
#   CAPTCHA_PROVIDER : "2captcha" (default) | "capsolver"
#   CAPTCHA_API_KEY  : solver account key; if unset, solving is skipped
CAPTCHA_SOLVE_TIMEOUT_S = 150   # max wall-clock to wait for a solved token

# Regex for street-address recovery from Complaint PDFs.
# Florida complaints follow FL Statute 83 service-of-process formatting:
# tenant address typically appears as STREET + CITY, FL + ZIP on consecutive
# lines OR on one line separated by commas.
STREET_SUFFIX_REGEX = re.compile(
    r"\b\d{1,6}\s+[A-Z0-9][A-Z0-9 .'\-]*?\b"
    r"(?:STREET|ST|AVENUE|AVE|BOULEVARD|BLVD|ROAD|RD|DRIVE|DR|LANE|LN|"
    r"COURT|CT|CIRCLE|CIR|PLACE|PL|PARKWAY|PKWY|TERRACE|TER|TRAIL|TRL|"
    r"WAY|HIGHWAY|HWY|SQUARE|SQ|LOOP|ALLEY|ALY|ROUTE|RTE|RUN|PATH)\b"
    r"(?:\s+(?:APT|UNIT|#|STE|SUITE)\s*[A-Z0-9\-]+)?"
    r"(?:[,\s]+[A-Z][A-Z\s]*?,\s*FL\s*\d{5}(?:-\d{4})?)?",
    re.IGNORECASE,
)


class OrangeScraper(BaseScraper):
    """
    Scrapes Orange County FL Clerk of Courts for Residential Eviction filings.

    Portal: https://myeclerk.myorangeclerk.com/Cases/search

    Flow per run:
      1. Load /Cases/search
      2. Open Case Type multiselect → check "Eviction" (value=41)
      3. Fill DateFrom (today - lookback_days) and DateTo (today)
      4. Solve reCAPTCHA v2 + inject token (_solve_recaptcha) so Search enables
      5. Click #caseSearch
      6. Iterate paginated case list — for each case number link:
         - Open case detail
         - Read Defendant name from Parties section
         - Click Complaint link in Docket Events → fetch PDF
         - Parse PDF for property address (STREET_SUFFIX_REGEX)
      7. Return Filing list
    """

    def __init__(self, lookback_days: int = 7, headless: bool = True):
        super().__init__(headless=headless)
        self.lookback_days = lookback_days

    async def scrape(self) -> list[Filing]:
        today = court_today(COURT_TIMEZONE)
        start = today - timedelta(days=self.lookback_days)
        filings: list[Filing] = []

        page = await self._launch_browser()
        try:
            log.info("Orange FL: loading case search page")
            await page.goto(CASE_SEARCH_URL, wait_until="domcontentloaded", timeout=60_000)
            await page.wait_for_timeout(3_000)

            filings = await self._run_search(page, start, today)
        except Exception as e:
            log.error("Orange FL: scrape failed: %s", e, exc_info=True)
        finally:
            await self._close_browser()

        log.info("Orange FL: %d filings found", len(filings))
        return filings

    # ------------------------------------------------------------------ #
    #  Search form                                                        #
    # ------------------------------------------------------------------ #

    async def _run_search(self, page, start: date, today: date) -> list[Filing]:
        # Cross-platform M/d/yy format (no leading zero on month/day, 2-digit year)
        start_str = f"{start.month}/{start.day}/{start.year % 100:02d}"
        end_str   = f"{today.month}/{today.day}/{today.year % 100:02d}"

        # Step 1 — open Case Type multiselect dropdown
        log.info("Orange FL: opening Case Type multiselect")
        await page.click("button.multiselect.dropdown-toggle")
        await page.wait_for_timeout(800)

        # Step 2 — check Eviction option (value=41)
        log.info("Orange FL: selecting Eviction case type")
        await page.click(f"input[type='checkbox'][value='{EVICTION_CASE_TYPE_VALUE}']")
        await page.wait_for_timeout(400)

        # Step 3 — close the multiselect by clicking the toggle again
        await page.click("button.multiselect.dropdown-toggle")
        await page.wait_for_timeout(400)

        # Step 4 — fill DateFrom and DateTo
        log.info("Orange FL: setting date range %s → %s", start_str, end_str)
        await page.fill("#DateFrom", start_str)
        await page.wait_for_timeout(300)
        await page.fill("#DateTo", end_str)
        await page.wait_for_timeout(300)

        # Step 5 — solve the reCAPTCHA so #caseSearch becomes enabled.
        # If a solver key is configured we solve+inject directly; otherwise we
        # fall back to passively waiting (e.g. an external solver toggles it).
        await self._solve_recaptcha(page)

        log.info("Orange FL: waiting for search button to enable")
        try:
            await page.wait_for_function(
                "() => { const b = document.querySelector('#caseSearch');"
                " return b && !b.disabled; }",
                timeout=60_000,
            )
        except Exception:
            log.warning("Orange FL: search button never enabled within 60s — captcha may be unsolved")
            return []

        # Step 6 — click Search
        log.info("Orange FL: clicking Search button")
        await page.click("#caseSearch")

        # Step 7 — wait for results table to render
        try:
            await page.wait_for_selector("table#caseList tbody tr", timeout=30_000)
        except Exception:
            log.warning("Orange FL: results table did not render after search")
            return []

        await page.wait_for_timeout(2_000)
        return await self._collect_all_pages(page, today)

    # ------------------------------------------------------------------ #
    #  reCAPTCHA v2 solving (2Captcha / CapSolver)                         #
    # ------------------------------------------------------------------ #

    async def _solve_recaptcha(self, page) -> bool:
        """
        Make #caseSearch become enabled by satisfying the reCAPTCHA v2.

        Three modes via CAPTCHA_PROVIDER:
          - "audio"            : free, no key — solve the audio challenge
                                 in-browser with local speech-to-text.
          - "2captcha"/"capsolver" : paid solver API; token is injected.

        For the paid modes this is a no-op (returns False) when
        CAPTCHA_API_KEY is unset — preserving the old passive-wait behaviour
        and keeping unit tests green.
        """
        provider = os.getenv("CAPTCHA_PROVIDER", "2captcha").strip().lower()

        # Free path: complete the audio challenge directly in the browser so
        # reCAPTCHA itself sets the token + fires the callback. No key needed.
        if provider == "audio":
            return await self._solve_recaptcha_audio(page)

        api_key = os.getenv("CAPTCHA_API_KEY", "").strip()
        if not api_key:
            log.info("Orange FL: no CAPTCHA_API_KEY set — relying on passive wait")
            return False

        # Prefer the live sitekey off the page; fall back to the known constant.
        sitekey = await page.evaluate(
            "() => { const el = document.querySelector('.g-recaptcha');"
            " return el ? el.getAttribute('data-sitekey') : null; }"
        ) or RECAPTCHA_SITEKEY

        log.info("Orange FL: solving reCAPTCHA via %s (sitekey %s…)", provider, sitekey[:12])
        try:
            if provider == "capsolver":
                token = await self._solve_via_capsolver(api_key, sitekey)
            else:
                token = await self._solve_via_2captcha(api_key, sitekey)
        except Exception as e:
            log.warning("Orange FL: captcha solve failed: %s", e)
            return False

        if not token:
            log.warning("Orange FL: captcha solver returned no token")
            return False

        # Inject the token into the response field(s) and fire the page callback
        # (data-callback="recaptchaCallback") so the form's validation enables
        # the Search button exactly as a human solve would.
        await page.evaluate(
            """(token) => {
                document.querySelectorAll('textarea[name="g-recaptcha-response"], #g-recaptcha-response')
                    .forEach(el => { el.style.display = ''; el.value = token; });
                const el = document.querySelector('.g-recaptcha');
                const cbName = el && el.getAttribute('data-callback');
                const cb = cbName && window[cbName];
                if (typeof cb === 'function') { try { cb(token); } catch (e) {} }
            }""",
            token,
        )
        log.info("Orange FL: reCAPTCHA token injected")
        return True

    @staticmethod
    async def _solve_via_2captcha(api_key: str, sitekey: str) -> str | None:
        """2Captcha userrecaptcha flow (proxyless)."""
        async with httpx.AsyncClient(timeout=30) as client:
            r = await client.post(
                "https://2captcha.com/in.php",
                data={
                    "key": api_key,
                    "method": "userrecaptcha",
                    "googlekey": sitekey,
                    "pageurl": CASE_SEARCH_URL,
                    "json": "1",
                },
            )
            r.raise_for_status()
            data = r.json()
            if data.get("status") != 1:
                raise RuntimeError(f"2captcha submit error: {data.get('request')}")
            captcha_id = data["request"]

            deadline = asyncio.get_event_loop().time() + CAPTCHA_SOLVE_TIMEOUT_S
            while asyncio.get_event_loop().time() < deadline:
                await asyncio.sleep(5)
                rr = await client.get(
                    "https://2captcha.com/res.php",
                    params={"key": api_key, "action": "get", "id": captcha_id, "json": "1"},
                )
                rr.raise_for_status()
                res = rr.json()
                if res.get("status") == 1:
                    return res["request"]
                if res.get("request") != "CAPCHA_NOT_READY":
                    raise RuntimeError(f"2captcha poll error: {res.get('request')}")
        return None

    @staticmethod
    async def _solve_via_capsolver(api_key: str, sitekey: str) -> str | None:
        """CapSolver ReCaptchaV2TaskProxyLess flow."""
        async with httpx.AsyncClient(timeout=30) as client:
            r = await client.post(
                "https://api.capsolver.com/createTask",
                json={
                    "clientKey": api_key,
                    "task": {
                        "type": "ReCaptchaV2TaskProxyLess",
                        "websiteURL": CASE_SEARCH_URL,
                        "websiteKey": sitekey,
                    },
                },
            )
            r.raise_for_status()
            data = r.json()
            if data.get("errorId"):
                raise RuntimeError(f"capsolver createTask error: {data.get('errorDescription')}")
            task_id = data["taskId"]

            deadline = asyncio.get_event_loop().time() + CAPTCHA_SOLVE_TIMEOUT_S
            while asyncio.get_event_loop().time() < deadline:
                await asyncio.sleep(5)
                rr = await client.post(
                    "https://api.capsolver.com/getTaskResult",
                    json={"clientKey": api_key, "taskId": task_id},
                )
                rr.raise_for_status()
                res = rr.json()
                if res.get("errorId"):
                    raise RuntimeError(f"capsolver result error: {res.get('errorDescription')}")
                if res.get("status") == "ready":
                    return res["solution"]["gRecaptchaResponse"]
        return None

    # ------------------------------------------------------------------ #
    #  Free audio-challenge solver (no key, no per-solve cost)             #
    # ------------------------------------------------------------------ #

    async def _solve_recaptcha_audio(self, page) -> bool:
        """
        Solve the reCAPTCHA v2 *audio* challenge entirely in the browser:
        open the checkbox → switch to audio → download the spoken-phrase MP3 →
        transcribe it locally (miniaudio + SpeechRecognition) → type the answer
        → verify. reCAPTCHA then sets g-recaptcha-response and fires the page
        callback, so #caseSearch enables exactly as a human solve would.

        Returns True if an answer was submitted (the caller's button-enable
        wait is the final confirmation). Returns False if the challenge frames
        aren't found, Google blocks automated solving, or transcription fails.
        """
        sitekey = await page.evaluate(
            "() => { const el = document.querySelector('.g-recaptcha');"
            " return el ? el.getAttribute('data-sitekey') : null; }"
        ) or RECAPTCHA_SITEKEY

        def frame(substr: str):
            # Match the VISIBLE widget's frames by sitekey (the page also has a
            # second, invisible reCAPTCHA we must not touch).
            for f in page.frames:
                u = f.url or ""
                if substr in u and f"k={sitekey}" in u:
                    return f
            return None

        anchor = frame("api2/anchor")
        if not anchor:
            log.warning("Orange FL: reCAPTCHA anchor frame not found")
            return False

        log.info("Orange FL: solving reCAPTCHA via free audio challenge")
        try:
            await anchor.click("#recaptcha-anchor", timeout=10_000)
        except Exception as e:
            log.warning("Orange FL: could not click reCAPTCHA checkbox: %s", e)
        await page.wait_for_timeout(2_000)

        # Sometimes the checkbox passes with no challenge at all.
        if (await anchor.get_attribute("#recaptcha-anchor", "aria-checked")) == "true":
            log.info("Orange FL: reCAPTCHA passed without a challenge")
            return True

        bframe = frame("api2/bframe")
        if not bframe:
            await page.wait_for_timeout(2_000)
            bframe = frame("api2/bframe")
        if not bframe:
            log.warning("Orange FL: reCAPTCHA challenge frame not found")
            return False

        try:
            await bframe.click("#recaptcha-audio-button", timeout=10_000)
            await page.wait_for_timeout(2_000)
        except Exception as e:
            log.warning("Orange FL: could not open audio challenge: %s", e)
            return False

        for attempt in range(1, 4):
            body_txt = ""
            try:
                body_txt = (await bframe.inner_text("body")).lower()
            except Exception:
                pass
            if "automated queries" in body_txt or await bframe.query_selector(".rc-doscaptcha-header"):
                log.warning("Orange FL: reCAPTCHA blocked automated audio solving")
                return False

            audio_url = await bframe.get_attribute("#audio-source", "src")
            if not audio_url:
                link = await bframe.query_selector(".rc-audiochallenge-tdownload-link")
                audio_url = await link.get_attribute("href") if link else None
            if not audio_url:
                log.warning("Orange FL: no audio source found")
                return False

            text = await self._transcribe_audio(audio_url)
            if not text:
                log.warning("Orange FL: transcription failed (attempt %d), reloading", attempt)
                reload_btn = await bframe.query_selector("#recaptcha-reload-button")
                if reload_btn:
                    await reload_btn.click()
                    await page.wait_for_timeout(2_000)
                continue

            log.info("Orange FL: audio transcription (attempt %d): %r", attempt, text)
            await bframe.fill("#audio-response", text.lower().strip())
            await bframe.click("#recaptcha-verify-button")
            await page.wait_for_timeout(3_000)

            token = await page.evaluate(
                "() => { const t = document.querySelector('#g-recaptcha-response');"
                " return t ? t.value : ''; }"
            )
            if token or (await anchor.get_attribute("#recaptcha-anchor", "aria-checked")) == "true":
                log.info("Orange FL: reCAPTCHA audio challenge solved")
                return True
            log.info("Orange FL: audio answer not accepted, retrying")

        return False

    async def _transcribe_audio(self, audio_url: str) -> str | None:
        """Download the challenge MP3 and transcribe it off the event loop."""
        try:
            async with httpx.AsyncClient(timeout=30) as client:
                r = await client.get(audio_url)
                r.raise_for_status()
                mp3_bytes = r.content
            return await asyncio.to_thread(self._mp3_to_text, mp3_bytes)
        except Exception as e:
            log.warning("Orange FL: audio fetch/transcribe error: %s", e)
            return None

    @staticmethod
    def _mp3_to_text(mp3_bytes: bytes) -> str | None:
        """Decode MP3 → 16 kHz mono PCM (miniaudio, no ffmpeg) → free Google STT."""
        import miniaudio
        import speech_recognition as sr

        decoded = miniaudio.decode(
            mp3_bytes,
            output_format=miniaudio.SampleFormat.SIGNED16,
            nchannels=1,
            sample_rate=16_000,
        )
        audio = sr.AudioData(decoded.samples.tobytes(), 16_000, 2)
        try:
            return sr.Recognizer().recognize_google(audio)
        except sr.UnknownValueError:
            return None
        except Exception as e:
            log.warning("Orange FL: speech-to-text error: %s", e)
            return None

    # ------------------------------------------------------------------ #
    #  Results table — paginate and collect                               #
    # ------------------------------------------------------------------ #

    async def _collect_all_pages(self, page, today: date) -> list[Filing]:
        filings: list[Filing] = []
        seen_cases: set[str] = set()
        page_idx = 1

        while True:
            log.info("Orange FL: collecting page %d", page_idx)
            page_filings = await self._collect_current_page(page, today, seen_cases)
            filings.extend(page_filings)

            # Find pagination Next link (anchor with text "Next")
            next_link = await page.query_selector("a[aria-controls='caseList']:has-text('Next')")
            if not next_link:
                break
            cls = (await next_link.get_attribute("class")) or ""
            if "disabled" in cls:
                break

            await next_link.click()
            await page.wait_for_timeout(2_500)
            page_idx += 1
            if page_idx > 100:
                log.warning("Orange FL: pagination safety stop at page 100")
                break

        return filings

    async def _collect_current_page(
        self, page, today: date, seen_cases: set[str]
    ) -> list[Filing]:
        filings: list[Filing] = []

        rows = await page.query_selector_all("table#caseList tbody tr")
        case_numbers: list[str] = []
        for row in rows:
            link = await row.query_selector("a")
            if not link:
                continue
            txt = (await link.inner_text()).strip()
            if txt and txt not in seen_cases:
                case_numbers.append(txt)
                seen_cases.add(txt)

        log.info("Orange FL: %d case numbers on this page", len(case_numbers))

        for case_number in case_numbers:
            try:
                filing = await self._process_case(page, case_number, today)
                if filing:
                    filings.append(filing)
                # Go back to the results list
                await page.go_back(wait_until="domcontentloaded", timeout=30_000)
                await page.wait_for_timeout(1_500)
            except Exception as e:
                log.warning("Orange FL: case %s failed: %s", case_number, e)

        return filings

    # ------------------------------------------------------------------ #
    #  Single case — open detail, read Parties, fetch Complaint PDF       #
    # ------------------------------------------------------------------ #

    async def _process_case(self, page, case_number: str, today: date) -> Filing | None:
        log.debug("Orange FL: processing %s", case_number)

        # Click the case-number link
        link = await page.query_selector(f"table#caseList a:has-text('{case_number}')")
        if not link:
            return None
        await link.click()
        await page.wait_for_load_state("domcontentloaded", timeout=30_000)
        await page.wait_for_timeout(2_000)

        # Read Defendant + Plaintiff from Parties section
        defendant_name = await self._extract_defendant_name(page)
        plaintiff_name = await self._extract_plaintiff_name(page)

        # Find Complaint link in Docket Events table
        complaint_href = await self._find_complaint_href(page)

        property_address = "Unknown"
        if complaint_href:
            pdf_url = complaint_href
            if pdf_url.startswith("/"):
                pdf_url = DOC_BASE_URL + pdf_url
            property_address = await self._fetch_and_parse_complaint(pdf_url) or "Unknown"

        filing_date = today  # Refined below if filing date appears on detail page
        filing_date_text = await self._extract_filing_date(page)
        if filing_date_text:
            filing_date = filing_date_text

        return Filing(
            case_number      = case_number,
            tenant_name      = clean_tenant_name(defendant_name) or defendant_name or "Unknown",
            property_address = property_address,
            landlord_name    = plaintiff_name or "Unknown",
            filing_date      = filing_date,
            court_date       = None,
            state            = STATE,
            county           = COUNTY,
            notice_type      = NOTICE_TYPE,
            source_url       = page.url or SOURCE_URL,
        )

    async def _extract_defendant_name(self, page) -> str:
        """Defendant name lives in the Parties section. Try a few selectors."""
        candidates = [
            "tr:has(td:has-text('Defendant')) td:nth-child(2)",
            "tr:has(td:has-text('DEFENDANT')) td:nth-child(2)",
            "table.parties tr:has-text('Defendant') td:nth-child(2)",
        ]
        for sel in candidates:
            el = await page.query_selector(sel)
            if el:
                txt = (await el.inner_text()).strip()
                if txt:
                    return txt
        return ""

    async def _extract_plaintiff_name(self, page) -> str:
        candidates = [
            "tr:has(td:has-text('Plaintiff')) td:nth-child(2)",
            "tr:has(td:has-text('PLAINTIFF')) td:nth-child(2)",
        ]
        for sel in candidates:
            el = await page.query_selector(sel)
            if el:
                txt = (await el.inner_text()).strip()
                if txt:
                    return txt
        return ""

    async def _extract_filing_date(self, page) -> date | None:
        """Filing date often appears as 'Filed: MM/DD/YYYY' on case detail."""
        try:
            el = await page.query_selector("text=/Filed:?\\s*\\d{1,2}\\/\\d{1,2}\\/\\d{4}/")
            if el:
                raw = (await el.inner_text()).strip()
                m = re.search(r"(\d{1,2})/(\d{1,2})/(\d{4})", raw)
                if m:
                    mo, da, yr = int(m.group(1)), int(m.group(2)), int(m.group(3))
                    return date(yr, mo, da)
        except Exception:
            pass
        return None

    async def _find_complaint_href(self, page) -> str | None:
        """
        Locate the Complaint link in the Docket Events table.

        Confirmed structure (from live HTML):
          <td class="cdDocLink">
              <a class="noprint dDescription" href="/DocView/Doc?eCode=...">Complaint</a>
          </td>
        """
        candidates = [
            "td.cdDocLink a:has-text('Complaint')",
            "td.cdDocLink a.dDescription",
        ]
        for sel in candidates:
            el = await page.query_selector(sel)
            if el:
                href = await el.get_attribute("href")
                if href:
                    return href
        return None

    async def _fetch_and_parse_complaint(self, pdf_url: str) -> str | None:
        """Download Complaint PDF and extract the property address via regex."""
        try:
            async with httpx.AsyncClient(timeout=30) as client:
                r = await client.get(pdf_url)
                r.raise_for_status()
                pdf_bytes = r.content

            with pdfplumber.open(io.BytesIO(pdf_bytes)) as pdf:
                text_parts = []
                for p in pdf.pages:
                    t = p.extract_text() or ""
                    text_parts.append(t)
                full_text = "\n".join(text_parts)

            return self._parse_address_from_text(full_text)
        except Exception as e:
            log.warning("Orange FL: PDF fetch/parse failed for %s: %s", pdf_url, e)
            return None

    @staticmethod
    def _parse_address_from_text(text: str) -> str | None:
        if not text:
            return None
        # Normalize whitespace
        clean = re.sub(r"[ \t]+", " ", text)
        match = STREET_SUFFIX_REGEX.search(clean)
        if not match:
            return None
        return " ".join(match.group(0).split()).strip()
