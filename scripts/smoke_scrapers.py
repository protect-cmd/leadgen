from __future__ import annotations

import argparse
import asyncio
import inspect
import logging
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

sys.path.insert(0, str(Path(__file__).parent.parent))

from dotenv import load_dotenv

from scrapers.tennessee.davidson import DavidsonTNScraper
from scrapers.texas.harris import HarrisCountyScraper
from services import notification_service

log = logging.getLogger(__name__)

StateFactory = Callable[[int, bool], list[tuple[str, object]]]


@dataclass(frozen=True)
class ScraperResult:
    state: str
    label: str
    count: int
    error: str | None = None


@dataclass(frozen=True)
class SmokeResult:
    results: list[ScraperResult]
    pushover_sent: bool = False


def _texas_scrapers(lookback_days: int, headless: bool) -> list[tuple[str, object]]:
    return [("Harris", HarrisCountyScraper(headless=headless, lookback_days=lookback_days))]


def _tennessee_scrapers(lookback_days: int, headless: bool) -> list[tuple[str, object]]:
    return [("Davidson", DavidsonTNScraper(lookback_days=lookback_days))]


SCRAPER_FACTORIES: dict[str, StateFactory] = {
    "texas": _texas_scrapers,
    "tennessee": _tennessee_scrapers,
}

STATE_ALIASES = {
    "tx": "texas",
    "texas": "texas",
    "harris": "texas",
    "tn": "tennessee",
    "tennessee": "tennessee",
    "davidson": "tennessee",
}


def parse_states(raw: str) -> list[str]:
    parts = [part.strip().lower() for part in raw.split(",") if part.strip()]
    if not parts or parts == ["all"]:
        return list(SCRAPER_FACTORIES)

    states: list[str] = []
    for part in parts:
        state = STATE_ALIASES.get(part)
        if not state:
            valid = ", ".join(sorted([*STATE_ALIASES, "all"]))
            raise ValueError(f"Unknown state {part!r}. Valid values: {valid}")
        if state not in states:
            states.append(state)
    return states


async def _scrape(scraper: object) -> list[object]:
    result = scraper.scrape()
    if inspect.isawaitable(result):
        result = await result
    return list(result)


def _summary_line(result: ScraperResult) -> str:
    label = f"{result.state.title()} / {result.label}"
    base = f"{label}: {result.count} filings"
    if result.error:
        return f"{base} (error: {result.error})"
    return base


def format_summary(results: list[ScraperResult]) -> str:
    return "\n".join(_summary_line(result) for result in results)


async def run_smoke(
    *,
    states: list[str],
    lookback_days: int,
    notify: bool,
    headless: bool = True,
    factories: dict[str, StateFactory] | None = None,
) -> SmokeResult:
    factories = factories or SCRAPER_FACTORIES
    results: list[ScraperResult] = []

    for state in states:
        for label, scraper in factories[state](lookback_days, headless):
            log.info("Scraper-only smoke: %s / %s", state, label)
            error: str | None = None
            try:
                filings = await _scrape(scraper)
            except Exception as e:
                filings = []
                error = str(e)
                log.exception("Scraper smoke failed: %s / %s", state, label)

            error = error or getattr(scraper, "last_error", None)
            result = ScraperResult(
                state=state,
                label=label,
                count=len(filings),
                error=error,
            )
            results.append(result)
            print(_summary_line(result))

    pushover_sent = False
    if notify:
        pushover_sent = await notification_service.send_alert(
            "Leadgen scraper smoke test",
            format_summary(results),
            priority=0,
            tags={"mode": "scraper-only", "runner": "not called"},
        )
        print(f"Pushover sent: {pushover_sent}")

    return SmokeResult(results=results, pushover_sent=pushover_sent)


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Run scraper-only smoke tests. Does not call runner, enrichment, GHL, or Bland."
    )
    parser.add_argument(
        "--states",
        default="texas,tennessee",
        help="Comma-separated states/aliases: texas, tx, harris, tennessee, tn, davidson, all.",
    )
    parser.add_argument("--lookback-days", type=int, default=2)
    parser.add_argument("--notify", action="store_true", help="Send Pushover summary if enabled.")
    parser.add_argument("--headed", action="store_true", help="Run browser scrapers headed.")
    parser.add_argument("--env-file", default=".env", help="Env file to load before running.")
    return parser


async def _main_async(argv: list[str] | None = None) -> int:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )
    args = _build_parser().parse_args(argv)
    load_dotenv(dotenv_path=args.env_file)

    try:
        states = parse_states(args.states)
    except ValueError as e:
        print(e, file=sys.stderr)
        return 2

    await run_smoke(
        states=states,
        lookback_days=args.lookback_days,
        notify=args.notify,
        headless=not args.headed,
    )
    return 0


def main() -> int:
    return asyncio.run(_main_async())


if __name__ == "__main__":
    raise SystemExit(main())
