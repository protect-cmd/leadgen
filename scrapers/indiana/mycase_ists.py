"""Indiana ISTS scraper -- finds EV cases where a judgment has been entered.

ISTS mode differs from VDG:
  VDG  -- searches recent filing dates, returns all new EV filings
  ISTS -- searches older filing dates, filters by _has_judgment(), then filters
          by judgment recency (EventDate of the judgment event within
          judgment_recency_days days)

Search strategy
---------------
The portal only allows search by filing date. Judgments in Indiana EV cases
typically enter 14-25 days after the case is filed. To find judgments entered
in the last judgment_recency_days days we search a shifted filing window:

    start = today - lookback_days         (e.g. 25 days ago)
    end   = today - _MIN_FILING_AGE_DAYS  (e.g. 14 days ago)

Cases filed more recently than _MIN_FILING_AGE_DAYS cannot have a judgment yet
and are skipped to save API calls.

Runtime estimate (Indiana statewide, avg 3 s / detail fetch)
-------------------------------------------------------------
  lookback_days=25 -> effective 11-day window -> ~1100 cases -> ~55 min
  lookback_days=30 -> effective 16-day window -> ~1550 cases -> ~78 min
"""
from __future__ import annotations

import logging
import random
import time
from datetime import timedelta

from models.filing import Filing
from scrapers.dates import court_today
from scrapers.indiana.mycase import (
    COURT_TIMEZONE,
    _DETAIL_DELAY_MAX,
    _DETAIL_DELAY_MIN,
    _PortalBlockedError,
    IndianaMyCaseScraper,
)

log = logging.getLogger(__name__)

_NOTICE_TYPE         = "Eviction Judgment"
_MIN_FILING_AGE_DAYS = 14   # cases newer than this cannot have a judgment yet


class IndianaISTSScraper(IndianaMyCaseScraper):
    """ISTS variant of IndianaMyCaseScraper.

    Parameters
    ----------
    lookback_days:
        How far back (in filing days) to search. Effective window is
        lookback_days - _MIN_FILING_AGE_DAYS days.  Default 25 -> ~55 min.
    judgment_recency_days:
        Only surface cases where the judgment event's EventDate is within the
        last N days.  Default 7.
    """

    def __init__(
        self,
        lookback_days: int = 25,
        judgment_recency_days: int = 7,
    ) -> None:
        super().__init__(lookback_days=lookback_days, mode="judgments")
        self.notice_type           = _NOTICE_TYPE
        self.judgment_recency_days = judgment_recency_days

    def _scrape_sync(self) -> list[Filing]:
        self.last_error = None
        if not self._init_session():
            return []

        today  = court_today(COURT_TIMEZONE)
        start  = today - timedelta(days=self.lookback_days)
        end    = today - timedelta(days=_MIN_FILING_AGE_DAYS)
        cutoff = today - timedelta(days=self.judgment_recency_days)

        log.info(
            "Indiana ISTS: searching filings %s -> %s  (judgment recency: %d d)",
            start, end, self.judgment_recency_days,
        )

        try:
            ev_cases = self._search_range(start, end)
        except _PortalBlockedError as exc:
            self.last_error = str(exc)
            log.error("Indiana ISTS: %s", exc)
            return []

        log.info("Indiana ISTS: %d EV case(s) to check", len(ev_cases))

        filings: list[Filing] = []
        for case in ev_cases:
            try:
                filing = self._fetch_detail(case)
                # judgment_date is populated by parent _fetch_detail when mode=="judgments"
                if filing and filing.judgment_date and filing.judgment_date >= cutoff:
                    filings.append(filing)
            except Exception as exc:
                log.warning(
                    "Detail fetch failed for %s: %s",
                    case.get("CaseNumber", "?"), exc,
                )
            time.sleep(random.uniform(_DETAIL_DELAY_MIN, _DETAIL_DELAY_MAX))

        log.info("Indiana ISTS: %d judgment filing(s) returned", len(filings))
        return filings
