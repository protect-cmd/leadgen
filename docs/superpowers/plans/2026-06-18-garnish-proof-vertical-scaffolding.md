# Garnish Proof Vertical Scaffolding Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Stand up the Garnish Proof lead vertical as an isolated parallel track that mirrors the ISTS pattern, reusing the shared SearchBug/DNC/name-parsing spine, fixture-tested end to end so the live Miami-Dade scraper can drop in once portal verification clears.

**Architecture:** Garnish Proof is NOT a flag on the eviction pipeline. Like ISTS, it is a physically isolated vertical: its own Supabase table (`garnishment_orders`), its own dataclass model, its own store and enrichment modules, and its own job harness. It reuses the shared primitives — `services/searchbug_service.search_tenant_detailed`, `services/name_utils`, the DNC scrubber — exactly as `services/ists_enrich.py` does. The daily scheduler never touches this table.

**Tech Stack:** Python 3.11, pydantic/dataclasses, Supabase Python client, pytest, Playwright (live scraper only — out of scope here).

## Global Constraints

- **FL-first, wage-garnishment only.** This plan builds for Florida (Miami-Dade) wage garnishment. TX is legally excluded (constitution bars private-debt wage garnishment); GA/OH are separate follow-on sources. Records whose `garnishment_type` is not `"wage"` are dropped at classification — the paycheck pitch only applies to wage garnishment.
- **The lead is the debtor, not the garnishee.** `debtor_name`/`debtor_address` are the worker (the lead). `garnishee_name` is the employer/bank and is never the enrichment target or contact. Any record lacking a debtor home address is dropped, not enriched.
- **DNC fail-closed.** No outreach code in this plan. Enrichment stores phone only; dialing/SMS is a gated follow-on and must preserve existing fail-closed DNC behavior.
- **No production outreach during local tests.** Do not call GHL, Bland, or Instantly from any test or smoke run in this plan.
- **Isolation.** All writes target `garnishment_orders` only. `filings` and `lead_contacts` are off-limits (not even SELECT is needed here).
- **Routing tag:** contacts destined for the Garnish Proof GHL subaccount carry the Supabase tag `garnish-proof-lead` (Jonas routes on it). The literal tag string is `garnish-proof-lead`.
- **Migration numbering:** next free migration is `023` (`022` is the highest present).

---

## Out of Scope (gated follow-on plans)

1. **Live Miami-Dade OCS scraper** — blocked on the portal verification (todo: confirm OCS exposes a wage-garnishment case type carrying the debtor's home address, date-enumerable). That verification produces the field map this plan's job harness consumes. The scraper class itself (`scrapers/florida/miami_dade_garnishment.py`) is a separate plan written against the captured payload/rows.
2. **GHL + Bland outreach wiring** — blocked on Jonas creating the Garnish Proof GHL subaccount (location ID/key). Mirrors `services/ists_ghl.py` / `services/ists_bland.py` when unblocked.

This plan delivers everything between those two gates: the table, model, store, classification/freshness gate, enrichment, and a fixture-driven job harness — all testable today without the live portal or any outreach system.

---

## File Structure

- `migrations/023_garnishment_orders.sql` — isolated table, mirrors `015_ists_judgments.sql`.
- `models/garnishment.py` — `GarnishmentRecord` dataclass + `to_row()`, mirrors `models/judgment.py`.
- `services/gp_classify.py` — wage-only filter + exemption-deadline computation (the genuinely new branch).
- `services/gp_store.py` — isolated persistence, mirrors `services/ists_store.py`.
- `services/gp_enrich.py` — SearchBug enrichment with exemption-window freshness gate, mirrors `services/ists_enrich.py` minus the rent call.
- `jobs/run_gp_miami.py` — scrape→classify→store harness; consumes a `GarnishmentScraper`-shaped object so the live scraper drops in later.
- `tests/test_garnishment_model.py`, `tests/test_gp_classify.py`, `tests/test_gp_enrich.py` — pure-logic tests.

---

### Task 1: Migration — `garnishment_orders` table

**Files:**
- Create: `migrations/023_garnishment_orders.sql`

**Interfaces:**
- Produces: table `garnishment_orders` with PK `case_number`, columns consumed by Tasks 2/3/5.

- [ ] **Step 1: Write the migration**

```sql
-- 023_garnishment_orders.sql
-- Garnish Proof vertical. Physically isolated from filings/lead_contacts.
-- The daily scheduler never reads this table. FL wage garnishment, debtor = lead.
CREATE TABLE IF NOT EXISTS garnishment_orders (
    case_number        TEXT PRIMARY KEY,
    debtor_name        TEXT NOT NULL,            -- the worker (the lead)
    debtor_address     TEXT NOT NULL,            -- debtor HOME address, never the garnishee's
    creditor_name      TEXT,                     -- plaintiff
    garnishee_name     TEXT,                     -- employer/bank; never the contact
    state              TEXT NOT NULL DEFAULT 'FL',
    county             TEXT NOT NULL DEFAULT 'Miami-Dade',
    filing_date        DATE,
    garnishment_type   TEXT NOT NULL DEFAULT 'wage',  -- 'wage' only is actionable
    exemption_deadline DATE,                     -- filing_date + claim-of-exemption window
    phone              TEXT,
    language_hint      TEXT,
    enriched_at        TIMESTAMPTZ,
    source_url         TEXT,
    selected_at        TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    confirmed_at       TIMESTAMPTZ
);

CREATE INDEX IF NOT EXISTS idx_garnishment_orders_filing_date
    ON garnishment_orders (filing_date);
```

- [ ] **Step 2: Commit**

```bash
git add migrations/023_garnishment_orders.sql
git commit -m "feat(garnish-proof): add isolated garnishment_orders table (migration 023)"
```

> Note: apply to the live DB is a deploy step, not part of this commit. Migration 015 was likewise written before being applied.

---

### Task 2: `GarnishmentRecord` model

**Files:**
- Create: `models/garnishment.py`
- Test: `tests/test_garnishment_model.py`

**Interfaces:**
- Produces: `GarnishmentRecord` dataclass; `.to_row() -> dict` with dates as ISO strings or `None`. Fields: `case_number, debtor_name, debtor_address, creditor_name, garnishee_name, state, county, filing_date, garnishment_type, exemption_deadline, source_url`. Consumed by Tasks 3, 5, 6.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_garnishment_model.py
from datetime import date
from models.garnishment import GarnishmentRecord


def test_to_row_serializes_dates_and_keys():
    rec = GarnishmentRecord(
        case_number="2026-001234-CC",
        debtor_name="MARIA GOMEZ",
        debtor_address="123 SW 8th St, Miami, FL 33130",
        creditor_name="MIDLAND CREDIT MGMT",
        garnishee_name="ACME LOGISTICS INC",
        filing_date=date(2026, 6, 15),
        exemption_deadline=date(2026, 7, 5),
        source_url="https://example/ocs/case/2026-001234-CC",
    )
    row = rec.to_row()
    assert row["case_number"] == "2026-001234-CC"
    assert row["debtor_name"] == "MARIA GOMEZ"
    assert row["state"] == "FL"
    assert row["county"] == "Miami-Dade"
    assert row["garnishment_type"] == "wage"
    assert row["filing_date"] == "2026-06-15"
    assert row["exemption_deadline"] == "2026-07-05"


def test_to_row_handles_null_dates():
    rec = GarnishmentRecord(
        case_number="X", debtor_name="A B", debtor_address="addr",
    )
    row = rec.to_row()
    assert row["filing_date"] is None
    assert row["exemption_deadline"] is None
    assert row["creditor_name"] is None
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_garnishment_model.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'models.garnishment'`

- [ ] **Step 3: Write minimal implementation**

```python
# models/garnishment.py
from __future__ import annotations
from dataclasses import dataclass
from datetime import date


@dataclass
class GarnishmentRecord:
    case_number: str
    debtor_name: str
    debtor_address: str
    creditor_name: str | None = None
    garnishee_name: str | None = None
    state: str = "FL"
    county: str = "Miami-Dade"
    filing_date: date | None = None
    garnishment_type: str = "wage"
    exemption_deadline: date | None = None
    source_url: str | None = None

    def to_row(self) -> dict:
        """Supabase-ready dict (dates as ISO strings)."""
        return {
            "case_number": self.case_number,
            "debtor_name": self.debtor_name,
            "debtor_address": self.debtor_address,
            "creditor_name": self.creditor_name,
            "garnishee_name": self.garnishee_name,
            "state": self.state,
            "county": self.county,
            "filing_date": self.filing_date.isoformat() if self.filing_date else None,
            "garnishment_type": self.garnishment_type,
            "exemption_deadline": self.exemption_deadline.isoformat() if self.exemption_deadline else None,
            "source_url": self.source_url,
        }
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_garnishment_model.py -v`
Expected: PASS (2 passed)

- [ ] **Step 5: Commit**

```bash
git add models/garnishment.py tests/test_garnishment_model.py
git commit -m "feat(garnish-proof): add GarnishmentRecord model"
```

---

### Task 3: Classification — wage-only filter + exemption deadline

**Files:**
- Create: `services/gp_classify.py`
- Test: `tests/test_gp_classify.py`

**Interfaces:**
- Consumes: `GarnishmentRecord` from Task 2.
- Produces:
  - `FL_EXEMPTION_WINDOW_DAYS: int = 20`
  - `classify(records: list[GarnishmentRecord]) -> list[GarnishmentRecord]` — drops non-wage and address-less records, and stamps `exemption_deadline = filing_date + FL_EXEMPTION_WINDOW_DAYS` when `filing_date` is set and deadline is unset.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_gp_classify.py
from datetime import date
from models.garnishment import GarnishmentRecord
from services.gp_classify import classify, FL_EXEMPTION_WINDOW_DAYS


def _rec(**kw):
    base = dict(case_number="C", debtor_name="A B", debtor_address="123 Main St, Miami, FL 33130")
    base.update(kw)
    return GarnishmentRecord(**base)


def test_drops_non_wage_garnishment():
    out = classify([_rec(garnishment_type="bank"), _rec(garnishment_type="wage")])
    assert len(out) == 1
    assert out[0].garnishment_type == "wage"


def test_drops_records_without_debtor_address():
    out = classify([_rec(debtor_address=""), _rec(debtor_address="   ")])
    assert out == []


def test_stamps_exemption_deadline_from_filing_date():
    out = classify([_rec(filing_date=date(2026, 6, 15))])
    assert out[0].exemption_deadline == date(2026, 7, 5)  # +20 days


def test_preserves_existing_deadline():
    out = classify([_rec(filing_date=date(2026, 6, 15), exemption_deadline=date(2026, 6, 30))])
    assert out[0].exemption_deadline == date(2026, 6, 30)


def test_window_constant_is_twenty():
    assert FL_EXEMPTION_WINDOW_DAYS == 20
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_gp_classify.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'services.gp_classify'`

- [ ] **Step 3: Write minimal implementation**

```python
# services/gp_classify.py
"""Garnish Proof classification: keep only actionable wage garnishments and
stamp the Claim-of-Exemption deadline. FL window = 20 days from filing."""
from __future__ import annotations
from datetime import timedelta
from models.garnishment import GarnishmentRecord

FL_EXEMPTION_WINDOW_DAYS = 20


def classify(records: list[GarnishmentRecord]) -> list[GarnishmentRecord]:
    kept: list[GarnishmentRecord] = []
    for r in records:
        if r.garnishment_type != "wage":
            continue
        if not r.debtor_address or not r.debtor_address.strip():
            continue
        if r.filing_date and not r.exemption_deadline:
            r.exemption_deadline = r.filing_date + timedelta(days=FL_EXEMPTION_WINDOW_DAYS)
        kept.append(r)
    return kept
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_gp_classify.py -v`
Expected: PASS (5 passed)

- [ ] **Step 5: Commit**

```bash
git add services/gp_classify.py tests/test_gp_classify.py
git commit -m "feat(garnish-proof): wage-only classification + exemption deadline"
```

---

### Task 4: `gp_store` — isolated persistence

**Files:**
- Create: `services/gp_store.py`

**Interfaces:**
- Consumes: `GarnishmentRecord.to_row()` from Task 2.
- Produces:
  - `async upsert_order(record: GarnishmentRecord) -> None`
  - `async existing_case_numbers(case_numbers: list[str]) -> set[str]`
  Consumed by Task 6.

- [ ] **Step 1: Write the implementation** (mirror of `services/ists_store.py`; DB-integration module verified by the smoke run in Task 6, not a unit test)

```python
# services/gp_store.py
"""Isolated persistence for Garnish Proof. Writes ONLY garnishment_orders."""
from __future__ import annotations
import asyncio
import os
from dotenv import load_dotenv
from supabase import create_client, Client
from models.garnishment import GarnishmentRecord

load_dotenv()

_client: Client = create_client(
    os.environ["SUPABASE_URL"],
    os.environ["SUPABASE_SERVICE_ROLE_KEY"],
)

_TABLE = "garnishment_orders"


async def upsert_order(record: GarnishmentRecord) -> None:
    def _do() -> None:
        _client.table(_TABLE).upsert(
            record.to_row(), on_conflict="case_number"
        ).execute()
    await asyncio.to_thread(_do)


async def existing_case_numbers(case_numbers: list[str]) -> set[str]:
    """Return case numbers already stored, for idempotent re-runs."""
    if not case_numbers:
        return set()
    def _q() -> set[str]:
        found: set[str] = set()
        for i in range(0, len(case_numbers), 200):
            chunk = case_numbers[i:i + 200]
            data = (_client.table(_TABLE).select("case_number")
                    .in_("case_number", chunk).execute().data or [])
            found.update(d["case_number"] for d in data)
        return found
    return await asyncio.to_thread(_q)
```

- [ ] **Step 2: Verify it imports**

Run: `python -c "from services import gp_store; print(gp_store._TABLE)"`
Expected: prints `garnishment_orders` (requires SUPABASE env vars present, as for ISTS)

- [ ] **Step 3: Commit**

```bash
git add services/gp_store.py
git commit -m "feat(garnish-proof): isolated gp_store persistence"
```

---

### Task 5: `gp_enrich` — SearchBug enrichment with exemption-window gate

**Files:**
- Create: `services/gp_enrich.py`
- Test: `tests/test_gp_enrich.py`

**Interfaces:**
- Consumes: `services.searchbug_service.search_tenant_detailed(first_name, last_name, city, state, postal, address) -> SearchBugResult` (`.phone`, `.status`); `services.name_utils.clean_tenant_name`, `parse_name`.
- Produces:
  - `GP_FRESHNESS_DAYS: int = 20`
  - `_split_name(full_name) -> tuple[str, str]`
  - `_parse_address_parts(address) -> tuple[str, str, str]` (city, state, zip)
  - `async enrich_batch(limit: int = 50, dry_run: bool = False) -> dict` returning metrics `{total, phone_found, no_records, ambiguous, errors, skipped}`.

  Note: garnishment leads have no rent dimension — unlike `ists_enrich`, there is no rent-estimate call (YAGNI).

- [ ] **Step 1: Write the failing test** (pure helpers — the batch loop is integration-verified in Task 6 smoke)

```python
# tests/test_gp_enrich.py
from services.gp_enrich import _split_name, _parse_address_parts, GP_FRESHNESS_DAYS


def test_freshness_window_is_twenty():
    assert GP_FRESHNESS_DAYS == 20


def test_split_name_first_last():
    first, last = _split_name("MARIA GOMEZ")
    assert first.upper() == "MARIA"
    assert last.upper() == "GOMEZ"


def test_parse_address_parts():
    city, state, zip_ = _parse_address_parts("123 SW 8th St, Miami, FL 33130")
    assert city == "Miami"
    assert state == "FL"
    assert zip_ == "33130"


def test_parse_address_parts_handles_missing():
    city, state, zip_ = _parse_address_parts("123 SW 8th St")
    assert (city, state, zip_) == ("", "", "")
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_gp_enrich.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'services.gp_enrich'`

- [ ] **Step 3: Write minimal implementation**

```python
# services/gp_enrich.py
"""Garnish Proof SearchBug enrichment for garnishment_orders.

Reads unenriched records (phone IS NULL), gated to those still inside the
Claim-of-Exemption window (filing_date >= today - GP_FRESHNESS_DAYS), calls
SearchBug with the debtor's home address, writes phone + language_hint back.
Writes ONLY garnishment_orders. No rent dimension."""
from __future__ import annotations

import asyncio
import logging
import os
import re
from datetime import date, datetime, timedelta, timezone

from dotenv import load_dotenv
from supabase import create_client, Client

from services.name_utils import clean_tenant_name, parse_name
from services.searchbug_service import search_tenant_detailed

load_dotenv()
log = logging.getLogger(__name__)

_client: Client = create_client(
    os.environ["SUPABASE_URL"],
    os.environ["SUPABASE_SERVICE_ROLE_KEY"],
)

_TABLE = "garnishment_orders"
GP_FRESHNESS_DAYS = 20  # only enrich records still inside the exemption window

_SPANISH_SURNAME_RE = re.compile(
    r"(ez|os|as|ia|io|ón|on|ar|er|ado|eda|ero|era|illo|ito|ita|uez|quez|ndo)$",
    re.IGNORECASE,
)


def _split_name(full_name: str) -> tuple[str, str]:
    return parse_name(clean_tenant_name(full_name))


def _parse_address_parts(address: str) -> tuple[str, str, str]:
    parts = [p.strip() for p in address.split(",")]
    city = parts[1] if len(parts) >= 2 else ""
    state_zip = parts[2].strip() if len(parts) >= 3 else ""
    tokens = state_zip.split()
    state = tokens[0] if tokens else ""
    zip_ = tokens[1] if len(tokens) >= 2 else ""
    return city, state, zip_


def _language_hint(last: str) -> str:
    return "spanish_likely" if _SPANISH_SURNAME_RE.search(last) else "english_likely"


async def enrich_batch(limit: int = 50, dry_run: bool = False) -> dict:
    cutoff = (date.today() - timedelta(days=GP_FRESHNESS_DAYS)).isoformat()

    def _fetch() -> list[dict]:
        return (
            _client.table(_TABLE)
            .select("case_number,debtor_name,debtor_address,state,county")
            .is_("phone", "null")
            .is_("enriched_at", "null")
            .gte("filing_date", cutoff)
            .limit(limit)
            .execute()
            .data or []
        )

    records = await asyncio.to_thread(_fetch)
    log.info("GP enrich: %d unenriched records fetched (limit=%d)", len(records), limit)

    metrics = {"total": len(records), "phone_found": 0, "no_records": 0,
               "ambiguous": 0, "errors": 0, "skipped": 0}

    for rec in records:
        case_number = rec["case_number"]
        debtor = rec["debtor_name"]
        address = rec["debtor_address"]

        first, last = _split_name(debtor)
        if not first or not last:
            log.info("GP enrich: skipping %s — bad name %r", case_number, debtor)
            metrics["skipped"] += 1
            continue

        city, state, zip_ = _parse_address_parts(address)
        hint = _language_hint(last)

        if dry_run:
            log.info("DRY ENRICH %s | %s %s | %s, %s %s | hint=%s",
                     case_number, first, last, city, state, zip_, hint)
            continue

        result = await search_tenant_detailed(
            first_name=first, last_name=last,
            city=city, state=state, postal=zip_, address=address,
        )
        phone = result.phone if result.status in ("phone_found", "name_mismatch") else None
        now = datetime.now(timezone.utc).isoformat()

        def _update(case=case_number, p=phone, h=hint, t=now):
            payload = {"enriched_at": t, "language_hint": h}
            if p:
                payload["phone"] = p
            _client.table(_TABLE).update(payload).eq("case_number", case).execute()

        await asyncio.to_thread(_update)

        if phone:
            metrics["phone_found"] += 1
            log.info("GP enrich: phone found %s → %s (%s)", case_number, phone[:4] + "****", result.status)
        elif result.status == "no_records":
            metrics["no_records"] += 1
        elif result.status == "ambiguous":
            metrics["ambiguous"] += 1
        else:
            metrics["errors"] += 1
            log.warning("GP enrich: %s %s (%s %s)", result.status, case_number, first, last)

    return metrics
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_gp_enrich.py -v`
Expected: PASS (4 passed)

- [ ] **Step 5: Commit**

```bash
git add services/gp_enrich.py tests/test_gp_enrich.py
git commit -m "feat(garnish-proof): SearchBug enrichment with exemption-window gate"
```

---

### Task 6: Job harness — `run_gp_miami`

**Files:**
- Create: `jobs/run_gp_miami.py`

**Interfaces:**
- Consumes: `services.gp_classify.classify`, `services.gp_store.{existing_case_numbers, upsert_order}`, a scraper object exposing `async scrape() -> list[GarnishmentRecord]` and a `last_error` attribute. The concrete `MiamiDadeGarnishmentScraper` is delivered by the gated follow-on plan; this harness imports it lazily so the rest of the vertical is testable without it.
- Produces: CLI `python -m jobs.run_gp_miami [--dry-run]`.

- [ ] **Step 1: Write the harness** (mirror of `jobs/run_ists_harris.py`)

```python
# jobs/run_gp_miami.py
"""Garnish Proof Miami-Dade run. NOT wired into daily_scheduler.

    python -m jobs.run_gp_miami --dry-run   # scrape + classify + metrics, no DB write
    python -m jobs.run_gp_miami             # also upserts to garnishment_orders

The scraper import is lazy: the live MiamiDadeGarnishmentScraper ships in the
gated follow-on plan once OCS verification confirms the field map.
"""
from __future__ import annotations
import argparse
import asyncio
import logging
from collections import Counter
from datetime import date

from services.gp_classify import classify
from services import gp_store

logging.basicConfig(level=logging.INFO)
log = logging.getLogger("gp.miami")


def _metrics(records) -> str:
    if not records:
        return "no wage-garnishment records found"
    buckets: Counter = Counter()
    today = date.today()
    for r in records:
        days = (today - r.filing_date).days if r.filing_date else -1
        b = ("0-7" if 0 <= days <= 7 else "8-14" if days <= 14
             else "15-20" if days <= 20 else "expired" if days > 20 else "unknown")
        buckets[b] += 1
    return f"records={len(records)} | filing-date buckets={dict(buckets)}"


async def main(dry_run: bool) -> None:
    from scrapers.florida.miami_dade_garnishment import MiamiDadeGarnishmentScraper

    scraper = MiamiDadeGarnishmentScraper()
    records = await scraper.scrape()
    if scraper.last_error:
        log.error("scrape error: %s", scraper.last_error)
    records = classify(records)
    log.info("METRICS: %s", _metrics(records))

    if dry_run:
        for r in records[:25]:
            log.info("DRY %s | %s | %s | filed=%s | deadline=%s",
                     r.case_number, r.debtor_name, r.debtor_address,
                     r.filing_date, r.exemption_deadline)
        log.info("dry-run: %d records NOT written", len(records))
        return

    existing = await gp_store.existing_case_numbers([r.case_number for r in records])
    new = [r for r in records if r.case_number not in existing]
    for r in new:
        await gp_store.upsert_order(r)
    log.info("stored %d new (skipped %d already present)", len(new), len(records) - len(new))


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true")
    asyncio.run(main(ap.parse_args().dry_run))
```

- [ ] **Step 2: Verify the harness imports without the (not-yet-existing) scraper**

Run: `python -c "import jobs.run_gp_miami; print('ok')"`
Expected: prints `ok` (the scraper import is inside `main`, so module import succeeds before the follow-on scraper lands)

- [ ] **Step 3: Commit**

```bash
git add jobs/run_gp_miami.py
git commit -m "feat(garnish-proof): Miami-Dade job harness (scraper drops in post-verification)"
```

---

## Routing tag note (for Jonas / the outreach follow-on)

When the gated outreach plan pushes a `garnishment_orders` row to GHL, it tags the contact `garnish-proof-lead` so Jonas's routing sends it to the Garnish Proof subaccount, kept separate from Cosner Drake. No code in this plan emits the tag — it is recorded here so the follow-on plan and Jonas stay aligned on the exact literal string `garnish-proof-lead`.

---

## Self-Review

- **Spec coverage:** isolated table (T1), model (T2), wage-only + exemption logic — the one genuinely new branch (T3), persistence (T4), enrichment reuse with freshness gate (T5), job harness (T6), routing tag recorded. Live scraper and outreach explicitly carved out as gated follow-ons with their blockers named.
- **Placeholder scan:** no TBD/TODO; every code step is complete. The one external unknown (OCS field map) is isolated behind the lazy scraper import, not faked inside this plan's code.
- **Type consistency:** `GarnishmentRecord` field names are identical across T2/T3/T5/T6; `to_row()` keys match the migration columns in T1; `search_tenant_detailed` call in T5 matches the signature read from `services/searchbug_service.py`; `gp_store` function names match their use in T6.

---

## Execution Handoff

Plan complete and saved to `docs/superpowers/plans/2026-06-18-garnish-proof-vertical-scaffolding.md`. Two execution options:

1. **Subagent-Driven (recommended)** — I dispatch a fresh subagent per task, review between tasks, fast iteration.
2. **Inline Execution** — Execute tasks in this session using executing-plans, batch execution with checkpoints.

Which approach?
