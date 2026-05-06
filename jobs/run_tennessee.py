import logging
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from scrapers.tennessee.davidson import DavidsonTNScraper

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s %(message)s",
)
log = logging.getLogger(__name__)


def main() -> None:
    log.info("Starting Tennessee scrape job")

    scrapers = [
        ("Davidson County", DavidsonTNScraper(lookback_days=2)),
    ]

    for label, scraper in scrapers:
        log.info(f"=== Tennessee / {label} ===")
        filings = scraper.scrape()
        if filings:
            log.info(f"{label}: {len(filings)} filings")
            for f in filings[:5]:
                log.info(
                    f"  {f.case_number} | {f.tenant_name} | {f.court_date} | {f.property_address[:40]}"
                )
        else:
            log.info(f"{label}: no filings found")

    log.info("Tennessee scrape job complete")


if __name__ == "__main__":
    main()
