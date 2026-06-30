"""Persistence for Cosner Drake debt suits (cd_debt_suits table).

Isolated from dedup_service (the eviction filings store) on purpose — CD is a
separate business line, same as the ISTS judgment store. Raw ingest only:
dedupe by case_number and insert. No enrichment / outreach here.
"""
from __future__ import annotations

import asyncio
import logging
import os
import time
from functools import lru_cache

import httpx
from dotenv import load_dotenv
from supabase import Client, create_client

from models.debt_suit import DebtSuit

load_dotenv()
log = logging.getLogger(__name__)

_TABLE = "cd_debt_suits"
_RETRY_ATTEMPTS = 3
_RETRY_DELAY_SECONDS = 1.0
_INSERT_CHUNK = 500


@lru_cache(maxsize=1)
def _client() -> Client:
    """Lazily build the Supabase client so importing this module (e.g. in the
    scraper-only smoke or unit tests) doesn't require Supabase env vars."""
    return create_client(
        os.environ["SUPABASE_URL"],
        os.environ["SUPABASE_SERVICE_ROLE_KEY"],
    )


def _execute_with_retry(query, label: str):
    for attempt in range(1, _RETRY_ATTEMPTS + 1):
        try:
            return query.execute()
        except httpx.TransportError as exc:
            if attempt == _RETRY_ATTEMPTS:
                raise
            log.warning("Supabase %s transport error %s/%s: %s",
                        label, attempt, _RETRY_ATTEMPTS, exc)
            time.sleep(_RETRY_DELAY_SECONDS * attempt)


def _existing_case_numbers(case_numbers: list[str]) -> set[str]:
    existing: set[str] = set()
    for i in range(0, len(case_numbers), _INSERT_CHUNK):
        chunk = case_numbers[i : i + _INSERT_CHUNK]
        result = _execute_with_retry(
            _client().table(_TABLE).select("case_number").in_("case_number", chunk),
            "cd dedup check",
        )
        existing.update(row["case_number"] for row in (result.data or []))
    return existing


def _insert_suits(suits: list[DebtSuit]) -> int:
    """Dedupe by case_number, insert new rows. Returns number inserted."""
    if not suits:
        return 0
    # Collapse intra-batch duplicates first (same case scraped from two windows).
    by_case = {s.case_number: s for s in suits if s.case_number}
    case_numbers = list(by_case)
    existing = _existing_case_numbers(case_numbers)
    new_rows = [s.to_row() for cn, s in by_case.items() if cn not in existing]
    if not new_rows:
        return 0
    for i in range(0, len(new_rows), _INSERT_CHUNK):
        chunk = new_rows[i : i + _INSERT_CHUNK]
        _execute_with_retry(
            _client().table(_TABLE).insert(chunk),
            "cd insert suits",
        )
    return len(new_rows)


async def insert_suits(suits: list[DebtSuit]) -> int:
    """Async wrapper: dedupe + insert; returns count of newly inserted suits."""
    return await asyncio.to_thread(_insert_suits, suits)
