# Current Schedule Hardening Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Bring the four flagged scheduled jobs (Maricopa, Tarrant, Cobb + the missing GHL Review stage) up to Spec 1's gold standard, using GHL API + Railway CLI for operational changes.

**Architecture:** Three code units (GHL service helpers, Maricopa formatter, scraper diagnostic) plus two one-shot operational scripts (Review stage creation, scraper diagnosis runs). All driven by Spec 1's `scripts/verify_pipeline_health.py` as the acceptance gate.

**Tech Stack:** Python 3.13, httpx (GHL HTTPS), Railway CLI, pytest + pytest-asyncio, dotenv. All dependencies already in repo.

**Spec reference:** [docs/superpowers/specs/2026-05-29-current-schedule-hardening-design.md](../specs/2026-05-29-current-schedule-hardening-design.md)

---

## File Structure

**To create:**
- `scripts/ghl_create_review_stage.py` — operational one-shot
- `scripts/diagnose_scraper_silence.py` — diagnostic for FLAG'd scrapers
- `tests/test_ghl_pipeline_stages.py` — tests for new ghl_service functions
- `tests/test_maricopa_address_format.py` — tests for the new formatter
- `tests/test_diagnose_scraper_silence.py` — tests for the classifier function

**To modify:**
- `services/ghl_service.py` — add `list_pipelines()` and `create_pipeline_stage()`
- `scrapers/arizona/maricopa.py:161-166` — rewrite `_property_address()` using assessor structured fields

**Possibly modified (depends on diagnosis outcome in Task 7):**
- `services/daily_scheduler.py` — remove Tarrant/Cobb from SCHEDULED_JOBS if rebuild needed
- Tarrant/Cobb scraper files — small fixes if diagnosis surfaces them

---

## Task 1: Add `list_pipelines()` async helper to ghl_service

**Why first:** `create_pipeline_stage()` needs to find the pipeline first, and the existing `_get_pipeline_id()` helper only looks up by stage ID. We need a generic pipelines lister.

**Files:**
- Modify: `services/ghl_service.py` (add new function near existing `_get_pipeline_id`)
- Create: `tests/test_ghl_pipeline_stages.py`

- [ ] **Step 1: Write the failing test**

Create `tests/test_ghl_pipeline_stages.py`:

```python
"""Tests for services.ghl_service pipeline helpers added in Spec 2."""
from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import AsyncMock, patch

import httpx
import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))

from services.ghl_service import list_pipelines


@pytest.fixture(autouse=True)
def env(monkeypatch):
    monkeypatch.setenv("GHL_API_KEY", "test-key")
    monkeypatch.setenv("GHL_NG_LOCATION_ID", "loc-ng")
    monkeypatch.setenv("GHL_EC_LOCATION_ID", "loc-ec")


def _ok(payload):
    return httpx.Response(200, json=payload)


@pytest.mark.asyncio
async def test_list_pipelines_ng_returns_parsed_payload():
    payload = {
        "pipelines": [
            {"id": "pip1", "name": "Main", "stages": [
                {"id": "s1", "name": "New Filing", "position": 0},
            ]},
            {"id": "pip2", "name": "Other", "stages": []},
        ]
    }
    with patch("httpx.AsyncClient.get", new=AsyncMock(return_value=_ok(payload))) as mock_get:
        result = await list_pipelines(track="ng")
    assert len(result) == 2
    assert result[0]["id"] == "pip1"
    assert result[0]["stages"][0]["name"] == "New Filing"
    # Verify it called with the NG location
    call_kwargs = mock_get.call_args.kwargs
    assert call_kwargs["params"]["locationId"] == "loc-ng"


@pytest.mark.asyncio
async def test_list_pipelines_returns_empty_on_http_error():
    with patch("httpx.AsyncClient.get", new=AsyncMock(return_value=httpx.Response(500, text="boom"))):
        result = await list_pipelines(track="ng")
    assert result == []
```

- [ ] **Step 2: Run test to verify it fails**

```bash
cd "d:\Freelance Projects\EvictionCommand\leadgen"
python -m pytest tests/test_ghl_pipeline_stages.py -v
```

Expected: ImportError on `list_pipelines`.

- [ ] **Step 3: Add `list_pipelines()` to ghl_service**

Edit `services/ghl_service.py`. Find the section after `_get_pipeline_id()` (around line 80) and add:

```python
async def list_pipelines(track: str = "ng") -> list[dict]:
    """Return the location's pipelines as a list of dicts.

    Each entry contains: id, name, stages (list of {id, name, position, ...}).
    Returns [] on HTTP error rather than raising — callers decide whether
    that's a hard failure.
    """
    headers = _headers(track)
    location_id = _location_id(track)
    async with httpx.AsyncClient(timeout=30) as client:
        r = await client.get(
            f"{BASE}/opportunities/pipelines",
            params={"locationId": location_id},
            headers=headers,
        )
    if r.status_code != 200:
        log.warning(
            "list_pipelines failed: %s %s", r.status_code, r.text[:200]
        )
        return []
    return r.json().get("pipelines", []) or []
```

- [ ] **Step 4: Run test to verify it passes**

```bash
python -m pytest tests/test_ghl_pipeline_stages.py -v
```

Expected: 2 passed.

- [ ] **Step 5: Commit**

```bash
git add services/ghl_service.py tests/test_ghl_pipeline_stages.py
git commit -m "feat: ghl_service.list_pipelines() async helper"
```

---

## Task 2: Add `create_pipeline_stage()` with idempotency

**Why this design:** GHL v2 API doesn't have a dedicated stage-creation endpoint. The pattern is "fetch the pipeline, mutate its stages array, PUT it back." We make it idempotent by checking for an existing stage with the same name before mutating.

**Files:**
- Modify: `services/ghl_service.py` (add `create_pipeline_stage()`)
- Modify: `tests/test_ghl_pipeline_stages.py` (add tests)

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_ghl_pipeline_stages.py`:

```python
from services.ghl_service import create_pipeline_stage


@pytest.mark.asyncio
async def test_create_pipeline_stage_returns_existing_id_when_name_matches():
    """Idempotency: if a stage with the same name already exists, return its
    ID without making a PUT call."""
    existing = {
        "pipelines": [{
            "id": "pip1", "name": "Main",
            "stages": [
                {"id": "stage-review", "name": "Review - SearchBug Mismatch", "position": 0},
                {"id": "stage-new", "name": "New Filing", "position": 1},
            ],
        }]
    }
    get_mock = AsyncMock(return_value=_ok(existing))
    put_mock = AsyncMock()
    with patch("httpx.AsyncClient.get", new=get_mock), \
         patch("httpx.AsyncClient.put", new=put_mock):
        sid = await create_pipeline_stage(
            track="ng",
            pipeline_id="pip1",
            name="Review - SearchBug Mismatch",
            position=0,
        )
    assert sid == "stage-review"
    put_mock.assert_not_called()


@pytest.mark.asyncio
async def test_create_pipeline_stage_creates_new_at_position():
    """Inserts the new stage at the requested position and returns the new ID."""
    existing = {
        "pipelines": [{
            "id": "pip1", "name": "Main",
            "stages": [
                {"id": "s-new", "name": "New Filing", "position": 0},
                {"id": "s-won", "name": "Won", "position": 1},
            ],
        }]
    }
    # PUT response includes the updated pipeline with the new stage
    updated = {
        "pipeline": {
            "id": "pip1", "name": "Main",
            "stages": [
                {"id": "s-review-NEW", "name": "Review - SearchBug Mismatch", "position": 0},
                {"id": "s-new", "name": "New Filing", "position": 1},
                {"id": "s-won", "name": "Won", "position": 2},
            ],
        }
    }
    get_mock = AsyncMock(return_value=_ok(existing))
    put_mock = AsyncMock(return_value=_ok(updated))
    with patch("httpx.AsyncClient.get", new=get_mock), \
         patch("httpx.AsyncClient.put", new=put_mock):
        sid = await create_pipeline_stage(
            track="ng",
            pipeline_id="pip1",
            name="Review - SearchBug Mismatch",
            position=0,
        )
    assert sid == "s-review-NEW"
    put_mock.assert_called_once()
    # The PUT body should include the new stage at position 0
    sent_stages = put_mock.call_args.kwargs["json"]["stages"]
    assert sent_stages[0]["name"] == "Review - SearchBug Mismatch"
    assert sent_stages[1]["id"] == "s-new"


@pytest.mark.asyncio
async def test_create_pipeline_stage_raises_when_pipeline_not_found():
    payload = {"pipelines": [{"id": "other", "name": "Other", "stages": []}]}
    with patch("httpx.AsyncClient.get", new=AsyncMock(return_value=_ok(payload))):
        with pytest.raises(RuntimeError, match="pipeline.*pip1.*not found"):
            await create_pipeline_stage(
                track="ng", pipeline_id="pip1",
                name="Review - SearchBug Mismatch", position=0,
            )
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
python -m pytest tests/test_ghl_pipeline_stages.py -v
```

Expected: ImportError on `create_pipeline_stage`.

- [ ] **Step 3: Add `create_pipeline_stage()` to ghl_service**

Edit `services/ghl_service.py`. Add right after `list_pipelines()`:

```python
async def create_pipeline_stage(
    *,
    track: str,
    pipeline_id: str,
    name: str,
    position: int,
) -> str:
    """Idempotently create (or find) a pipeline stage by name.

    GHL v2 has no dedicated create-stage endpoint; the pattern is to fetch
    the pipeline, append/insert the new stage in the stages array, and PUT
    the pipeline back.

    Returns the stage ID — either an existing stage that matched the name,
    or the newly-created stage from the PUT response.

    Raises RuntimeError if the pipeline_id is not found in the location.
    """
    headers = _headers(track)
    pipelines = await list_pipelines(track=track)
    target = next((p for p in pipelines if p.get("id") == pipeline_id), None)
    if target is None:
        raise RuntimeError(f"pipeline {pipeline_id!r} not found in track {track!r}")

    # Idempotency: if a stage with this name already exists, return its ID.
    for stage in target.get("stages", []):
        if (stage.get("name") or "").strip() == name.strip():
            return stage["id"]

    # Insert the new stage at the requested position and PUT the whole pipeline.
    new_stage = {"name": name, "position": position}
    stages = list(target.get("stages", []))
    insert_at = max(0, min(position, len(stages)))
    stages.insert(insert_at, new_stage)
    # Renumber positions so the API doesn't reject duplicate positions
    for idx, s in enumerate(stages):
        s["position"] = idx

    body = {"name": target.get("name") or "Pipeline", "stages": stages}
    async with httpx.AsyncClient(timeout=30) as client:
        r = await client.put(
            f"{BASE}/opportunities/pipelines/{pipeline_id}",
            json=body,
            headers=headers,
        )
    if r.status_code != 200:
        raise RuntimeError(
            f"create_pipeline_stage PUT failed: {r.status_code} {r.text[:300]}"
        )
    updated_stages = r.json().get("pipeline", {}).get("stages", [])
    match = next(
        (s for s in updated_stages if (s.get("name") or "").strip() == name.strip()),
        None,
    )
    if match is None or not match.get("id"):
        raise RuntimeError(
            "create_pipeline_stage: PUT succeeded but new stage not present in response"
        )
    return match["id"]
```

- [ ] **Step 4: Run tests to verify they pass**

```bash
python -m pytest tests/test_ghl_pipeline_stages.py -v
```

Expected: 5 passed.

- [ ] **Step 5: Commit**

```bash
git add services/ghl_service.py tests/test_ghl_pipeline_stages.py
git commit -m "feat: ghl_service.create_pipeline_stage() with idempotency"
```

---

## Task 3: Operational script — create NG Review stage + update Railway env

**Files:**
- Create: `scripts/ghl_create_review_stage.py`

This is a one-shot operational script. No unit test needed — the underlying functions (`list_pipelines`, `create_pipeline_stage`) are tested. The script is glue: find the right pipeline, call `create_pipeline_stage`, push the ID to Railway, update `.env`, redeploy.

- [ ] **Step 1: Write the script**

Create `scripts/ghl_create_review_stage.py`:

```python
"""Create the 'Review - SearchBug Mismatch' stage in the NG GHL pipeline
and set GHL_NG_REVIEW_STAGE_ID on Railway. Idempotent — safe to re-run.

Usage:
    python scripts/ghl_create_review_stage.py            # prints what it would do
    python scripts/ghl_create_review_stage.py --apply    # actually creates + sets env
"""
from __future__ import annotations

import argparse
import asyncio
import os
import re
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from dotenv import load_dotenv

STAGE_NAME = "Review - SearchBug Mismatch"
ENV_KEY = "GHL_NG_REVIEW_STAGE_ID"


async def find_ng_pipeline_id() -> str:
    """Find the pipeline that contains GHL_NG_NEW_FILING_STAGE_ID."""
    from services.ghl_service import list_pipelines

    new_filing_stage = os.environ.get("GHL_NG_NEW_FILING_STAGE_ID")
    if not new_filing_stage:
        raise RuntimeError("GHL_NG_NEW_FILING_STAGE_ID not set; cannot locate NG pipeline")

    pipelines = await list_pipelines(track="ng")
    for pipe in pipelines:
        for stage in pipe.get("stages", []):
            if stage.get("id") == new_filing_stage:
                return pipe["id"]
    raise RuntimeError(
        f"No NG pipeline contains stage {new_filing_stage!r}; cannot create review stage."
    )


def _update_local_env(stage_id: str) -> None:
    """Append or update GHL_NG_REVIEW_STAGE_ID=<id> in local .env."""
    env_path = Path(__file__).parent.parent / ".env"
    if not env_path.exists():
        print(f"  (no local .env at {env_path}, skipping)")
        return
    contents = env_path.read_text(encoding="utf-8")
    new_line = f"{ENV_KEY}={stage_id}"
    pat = re.compile(rf"^{re.escape(ENV_KEY)}=.*$", re.MULTILINE)
    if pat.search(contents):
        contents = pat.sub(new_line, contents)
    else:
        if not contents.endswith("\n"):
            contents += "\n"
        contents += new_line + "\n"
    env_path.write_text(contents, encoding="utf-8")
    print(f"  updated local .env with {ENV_KEY}=...{stage_id[-6:]}")


def _set_railway_var(stage_id: str) -> None:
    """Run railway variable set <KEY>=<value> --skip-deploys."""
    cmd = ["railway", "variable", "set", f"{ENV_KEY}={stage_id}", "--skip-deploys"]
    print(f"  running: {' '.join(cmd[:4])} <value redacted> {cmd[-1]}")
    r = subprocess.run(cmd, shell=True, capture_output=True, text=True, timeout=120)
    if r.returncode != 0:
        raise RuntimeError(f"railway variable set failed: {r.stderr.strip()}")


def _railway_redeploy() -> None:
    cmd = ["railway", "redeploy", "--service", "leadgen", "--yes"]
    print(f"  running: {' '.join(cmd)}")
    subprocess.run(cmd, shell=True, capture_output=True, text=True, timeout=120)


async def main_async(apply: bool) -> int:
    load_dotenv()

    from services.ghl_service import create_pipeline_stage

    print("Discovering NG pipeline...")
    pipeline_id = await find_ng_pipeline_id()
    print(f"  NG pipeline id: {pipeline_id}")

    if not apply:
        print(f"\nWould create stage {STAGE_NAME!r} at position 0 in pipeline {pipeline_id}")
        print("Re-run with --apply to actually create + set env.")
        return 0

    print(f"\nCreating (or finding) stage {STAGE_NAME!r}...")
    stage_id = await create_pipeline_stage(
        track="ng",
        pipeline_id=pipeline_id,
        name=STAGE_NAME,
        position=0,
    )
    print(f"  stage id: {stage_id}")

    print("\nSetting Railway env...")
    _set_railway_var(stage_id)

    print("\nUpdating local .env...")
    _update_local_env(stage_id)

    print("\nTriggering Railway redeploy...")
    _railway_redeploy()

    print(f"\nDone. {ENV_KEY}={stage_id}")
    print("Run python scripts/verify_pipeline_health.py to confirm.")
    return 0


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--apply", action="store_true",
                   help="Actually create the stage + set env (default is dry-run)")
    args = p.parse_args()
    return asyncio.run(main_async(args.apply))


if __name__ == "__main__":
    raise SystemExit(main())
```

- [ ] **Step 2: Run it in dry-run mode to confirm pipeline discovery works**

```bash
python scripts/ghl_create_review_stage.py
```

Expected output:
```
Discovering NG pipeline...
  NG pipeline id: <some-uuid>

Would create stage 'Review - SearchBug Mismatch' at position 0 in pipeline <uuid>
Re-run with --apply to actually create + set env.
```

- [ ] **Step 3: Commit**

```bash
git add scripts/ghl_create_review_stage.py
git commit -m "feat: ghl_create_review_stage.py operational one-shot"
```

---

## Task 4: Live — run the script with --apply

**This is an operational step that mutates production state.** Only run when you're ready to actually create the GHL stage and trigger a Railway redeploy.

- [ ] **Step 1: Run with --apply**

```bash
python scripts/ghl_create_review_stage.py --apply
```

Expected output:
```
Discovering NG pipeline...
  NG pipeline id: <uuid>

Creating (or finding) stage 'Review - SearchBug Mismatch'...
  stage id: <new-uuid>

Setting Railway env...
  running: railway variable set GHL_NG_REVIEW_STAGE_ID=<...> --skip-deploys

Updating local .env...
  updated local .env with GHL_NG_REVIEW_STAGE_ID=...XXXXXX

Triggering Railway redeploy...
  running: railway redeploy --service leadgen --yes

Done. GHL_NG_REVIEW_STAGE_ID=<new-uuid>
Run python scripts/verify_pipeline_health.py to confirm.
```

- [ ] **Step 2: Verify in GHL UI**

Open the NG subaccount in GHL → Opportunities → Pipelines → confirm the "Review - SearchBug Mismatch" stage exists as the first stage of the pipeline that contains "New Filing".

- [ ] **Step 3: Verify env propagated to Railway**

```bash
railway variables --kv 2>&1 | grep GHL_NG_REVIEW_STAGE_ID
```

Expected: `GHL_NG_REVIEW_STAGE_ID=<uuid>`

- [ ] **Step 4: Run the verifier and confirm the GHL FAILs cleared**

```bash
python scripts/verify_pipeline_health.py 2>&1 | grep -E "GHL_NG_REVIEW_STAGE_ID|^---|FAIL"
```

Expected: `GHL_NG_REVIEW_STAGE_ID` shows `[OK]` in both the env and ghl sections.

- [ ] **Step 5: Commit (the local .env update was made but `.env` is gitignored so nothing to commit)**

No-op. The operational changes live in Railway + GHL + your local `.env`; the code change for this work was already committed in Tasks 1-3.

---

## Task 5: Maricopa `_property_address()` rewrite

**Files:**
- Modify: `scrapers/arizona/maricopa.py:161-166` (the `_property_address` static method)
- Create: `tests/test_maricopa_address_format.py`

- [ ] **Step 1: Write the failing tests**

Create `tests/test_maricopa_address_format.py`:

```python
"""Maricopa property_address formatter — must emit gate-passing strings."""
from __future__ import annotations

import sys
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from pipeline import gates
from scrapers.arizona.maricopa import MaricopaJusticeCourtScraper
from scrapers.arizona.maricopa_assessor import AddressMatchResult, ParcelRecord


def _detail(record: ParcelRecord | None, status: str = "single_match"):
    """Build a minimal MaricopaCaseDetail with the given assessor result."""
    from scrapers.arizona.maricopa import MaricopaCaseDetail
    records = [record] if record else []
    if record is None:
        status = "no_match"
    return MaricopaCaseDetail(
        filing_date=date(2026, 5, 25),
        address_match=AddressMatchResult(status=status, records=records),
    )


def _record(address: str, city: str, zip_: str) -> ParcelRecord:
    return ParcelRecord(
        apn="123-45-678",
        owner_name="OWNER",
        physical_address=address,
        mailing_address="",
        physical_city=city,
        physical_zip=zip_,
        jurisdiction="MARICOPA",
    )


def test_single_word_city_formatted_with_commas_and_state():
    record = _record("310 S 3RD AVE AVONDALE 85323", "AVONDALE", "85323")
    detail = _detail(record)
    result = MaricopaJusticeCourtScraper._property_address(detail)
    assert result == "310 S 3RD AVE, Avondale, AZ 85323"


def test_multi_word_city_handled_via_structured_field():
    record = _record("100 W MAIN ST QUEEN CREEK 85142", "QUEEN CREEK", "85142")
    detail = _detail(record)
    result = MaricopaJusticeCourtScraper._property_address(detail)
    assert result == "100 W MAIN ST, Queen Creek, AZ 85142"


def test_result_passes_gate_address():
    record = _record("310 S 3RD AVE AVONDALE 85323", "AVONDALE", "85323")
    detail = _detail(record)
    result = MaricopaJusticeCourtScraper._property_address(detail)
    assert gates.gate_address(result), f"gate_address rejected {result!r}"


def test_no_match_returns_unknown():
    detail = _detail(None)
    assert MaricopaJusticeCourtScraper._property_address(detail) == "Unknown"


def test_empty_record_returns_unknown():
    record = _record("", "", "")
    detail = _detail(record)
    assert MaricopaJusticeCourtScraper._property_address(detail) == "Unknown"


def test_joined_string_does_not_end_with_structured_suffix_returns_raw():
    """Defensive: if the assessor's physical_address doesn't end with the
    structured ' {city} {zip}' suffix, fall through to returning a sensibly-
    formatted version using structured fields anyway (don't drop the lead)."""
    # raw is unrelated to city/zip — likely data drift
    record = _record("DIFFERENT FORMAT 99999", "AVONDALE", "85323")
    detail = _detail(record)
    result = MaricopaJusticeCourtScraper._property_address(detail)
    # Should still produce a usable formatted string
    assert "Avondale" in result
    assert "AZ 85323" in result
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
python -m pytest tests/test_maricopa_address_format.py -v
```

Expected: 6 failures — the current `_property_address` returns the raw joined string, which doesn't match the expected formatted output.

- [ ] **Step 3: Rewrite `_property_address()`**

Edit `scrapers/arizona/maricopa.py`. Find the existing method (line ~161):

```python
@staticmethod
def _property_address(detail: MaricopaCaseDetail) -> str:
    match = detail.address_match
    if match and match.status == "single_match" and match.records:
        return match.records[0].physical_address or "Unknown"
    return "Unknown"
```

Replace with:

```python
@staticmethod
def _property_address(detail: MaricopaCaseDetail) -> str:
    """Build a gate-passing 'street, city, AZ zip' string from the
    assessor's structured fields. The assessor's `physical_address`
    is space-joined ('310 S 3RD AVE AVONDALE 85323') which fails
    `gate_address`; we use the separate `physical_city` + `physical_zip`
    fields to rebuild a properly-comma-and-state-separated string.
    """
    match = detail.address_match
    if not (match and match.status == "single_match" and match.records):
        return "Unknown"
    rec = match.records[0]
    raw = (rec.physical_address or "").strip()
    city = (rec.physical_city or "").strip()
    zip_ = (rec.physical_zip or "").strip()
    if not raw or not city or not zip_:
        return "Unknown"
    suffix = f" {city} {zip_}"
    street = raw[: -len(suffix)].strip() if raw.endswith(suffix) else raw.strip()
    return f"{street}, {city.title()}, AZ {zip_}"
```

- [ ] **Step 4: Run tests to verify they pass**

```bash
python -m pytest tests/test_maricopa_address_format.py -v
```

Expected: 6 passed.

- [ ] **Step 5: Run the full suite to ensure nothing else broke**

```bash
python -m pytest --tb=short -q
```

Expected: same baseline (one pre-existing DeKalb failure) plus the 6 new tests pass.

- [ ] **Step 6: Commit**

```bash
git add scrapers/arizona/maricopa.py tests/test_maricopa_address_format.py
git commit -m "fix: Maricopa _property_address uses assessor structured fields (gate_address passes)"
```

---

## Task 6: Scraper-silence diagnostic script

**Files:**
- Create: `scripts/diagnose_scraper_silence.py`
- Create: `tests/test_diagnose_scraper_silence.py`

The script's heavy work is just running scrapers + capturing exceptions; the only logic worth unit-testing is the classifier function that maps `(filings_count, exception)` to a class string.

- [ ] **Step 1: Write the failing test for the classifier**

Create `tests/test_diagnose_scraper_silence.py`:

```python
"""Tests for the silence-classifier in diagnose_scraper_silence.py."""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from scripts.diagnose_scraper_silence import classify_silence


def test_zero_filings_no_exception_is_no_volume():
    assert classify_silence(filings_count=0, exception=None, pass_rate=0.0) == "no_volume"


def test_connectivity_exception_classified_as_connectivity():
    exc = ConnectionError("timeout reaching portal")
    assert classify_silence(filings_count=0, exception=exc, pass_rate=0.0) == "connectivity"


def test_runtime_error_with_parsing_in_message_is_parsing():
    exc = RuntimeError("Failed to parse calendar PDF: missing case_number")
    assert classify_silence(filings_count=0, exception=exc, pass_rate=0.0) == "parsing"


def test_generic_exception_classified_as_connectivity():
    """Unknown exception type defaults to connectivity (most common cause)."""
    exc = ValueError("unexpected")
    assert classify_silence(filings_count=0, exception=exc, pass_rate=0.0) == "connectivity"


def test_filings_with_zero_pass_rate_is_format_mismatch():
    """Scraper returned filings but all fail gate_address — Maricopa-class issue."""
    assert classify_silence(filings_count=10, exception=None, pass_rate=0.0) == "format_mismatch"


def test_filings_with_low_pass_rate_is_format_mismatch():
    assert classify_silence(filings_count=50, exception=None, pass_rate=0.3) == "format_mismatch"


def test_filings_with_good_pass_rate_is_no_volume_fixed():
    """If filings come through and pass rate is good, the scraper isn't silent."""
    assert classify_silence(filings_count=10, exception=None, pass_rate=0.9) == "fixed_now"
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
python -m pytest tests/test_diagnose_scraper_silence.py -v
```

Expected: ModuleNotFoundError.

- [ ] **Step 3: Write the script**

Create `scripts/diagnose_scraper_silence.py`:

```python
"""Diagnose why a scheduled scraper isn't producing filings.

For each requested scraper, runs it standalone with a wider lookback,
captures the result, and classifies the failure mode into one of:

    fixed_now       - scraper produced filings with good pass rate; not silent
    no_volume       - clean run, 0 filings (legitimate quiet period)
    connectivity    - exception during fetch (portal down, network, Bright Data)
    parsing         - fetch succeeded but extraction returned 0 filings
    format_mismatch - filings returned but >50% fail gate_address

Usage:
    python scripts/diagnose_scraper_silence.py --scraper tarrant
    python scripts/diagnose_scraper_silence.py --scraper cobb --lookback 14
"""
from __future__ import annotations

import argparse
import asyncio
import inspect
import sys
import traceback
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from dotenv import load_dotenv


SCRAPER_FACTORIES: dict[str, callable] = {}


def _register_scrapers() -> None:
    """Lazy import so a broken scraper module doesn't crash the diagnostic."""
    global SCRAPER_FACTORIES
    if SCRAPER_FACTORIES:
        return
    try:
        from scrapers.texas.tarrant import TarrantCountyJPScraper
        SCRAPER_FACTORIES["tarrant"] = lambda lookback: TarrantCountyJPScraper(lookback_days=lookback)
    except Exception as e:
        SCRAPER_FACTORIES["tarrant"] = e
    try:
        from scrapers.georgia.cobb import CobbMagistrateCourtScraper
        SCRAPER_FACTORIES["cobb"] = lambda lookback: CobbMagistrateCourtScraper(lookback_days=lookback)
    except Exception as e:
        SCRAPER_FACTORIES["cobb"] = e


def classify_silence(
    *,
    filings_count: int,
    exception: BaseException | None,
    pass_rate: float,
) -> str:
    """Classify a scraper run into the gold-standard buckets."""
    if exception is not None:
        msg = str(exception).lower()
        if "pars" in msg or "extract" in msg or "selector" in msg:
            return "parsing"
        return "connectivity"
    if filings_count == 0:
        return "no_volume"
    if pass_rate < 0.5:
        return "format_mismatch"
    return "fixed_now"


def _compute_pass_rate(filings) -> float:
    """Use the same gate check the verifier uses."""
    from pipeline import gates
    if not filings:
        return 0.0
    passed = 0
    for f in filings:
        addr = getattr(f, "property_address", "") or ""
        name = getattr(f, "tenant_name", "") or ""
        if gates.gate_address(addr) and gates.gate_name(name):
            passed += 1
    return passed / len(filings)


async def _run_scraper(factory, lookback: int):
    """Run a scraper (sync or async) and return (filings, exception)."""
    try:
        scraper = factory(lookback)
        result = scraper.scrape()
        if inspect.isawaitable(result):
            result = await result
        return result or [], None
    except Exception as e:
        return [], e


async def main_async(scraper_names: list[str], lookback: int) -> int:
    load_dotenv()
    _register_scrapers()

    for name in scraper_names:
        print(f"\n=== {name} ===")
        factory = SCRAPER_FACTORIES.get(name)
        if factory is None:
            print(f"  ERROR: unknown scraper {name!r}; known: {list(SCRAPER_FACTORIES)}")
            continue
        if isinstance(factory, BaseException):
            print(f"  ERROR: scraper module failed to import: {factory!r}")
            print(f"  -> class=connectivity (or rebuild needed)")
            continue

        print(f"  running with lookback={lookback}d...")
        filings, exc = await _run_scraper(factory, lookback)

        if exc is not None:
            print(f"  exception: {type(exc).__name__}: {exc}")
            print("  --- traceback (last 5 lines) ---")
            print("\n".join(traceback.format_exception(type(exc), exc, exc.__traceback__))[-1000:])

        rate = _compute_pass_rate(filings)
        klass = classify_silence(filings_count=len(filings), exception=exc, pass_rate=rate)
        print(
            f"  result: filings={len(filings)}  gate_address+gate_name pass={100*rate:.0f}%  "
            f"class={klass}"
        )

        if klass == "fixed_now":
            print("  -> scraper appears to be working now; no action needed.")
        elif klass == "no_volume":
            print("  -> legitimate quiet period; leave scheduled.")
        elif klass == "format_mismatch":
            print("  -> Maricopa-class issue; fix the scraper's address formatter.")
        elif klass == "parsing":
            print("  -> selectors / extractor drift; inspect output above + portal.")
        elif klass == "connectivity":
            print("  -> portal / network / Bright Data issue; check infra before assuming code bug.")

    return 0


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--scraper", action="append", default=[],
                   help="Scraper name to diagnose (tarrant, cobb). Repeatable.")
    p.add_argument("--lookback", type=int, default=7,
                   help="Lookback days (default 7).")
    args = p.parse_args()
    names = args.scraper or ["tarrant", "cobb"]
    return asyncio.run(main_async(names, args.lookback))


if __name__ == "__main__":
    raise SystemExit(main())
```

- [ ] **Step 4: Run tests to verify they pass**

```bash
python -m pytest tests/test_diagnose_scraper_silence.py -v
```

Expected: 7 passed.

- [ ] **Step 5: Commit**

```bash
git add scripts/diagnose_scraper_silence.py tests/test_diagnose_scraper_silence.py
git commit -m "feat: diagnose_scraper_silence.py — classifier + per-scraper diagnostic runner"
```

---

## Task 7: Run diagnostics on Tarrant + Cobb (operational + branching)

**Files:** none initially. May modify `services/daily_scheduler.py` or scraper files based on diagnosis output.

- [ ] **Step 1: Run the diagnostic**

```bash
python scripts/diagnose_scraper_silence.py --scraper tarrant --scraper cobb --lookback 7
```

Wait for both to finish (Tarrant uses Bright Data so can take 1-2 minutes; Cobb is faster).

- [ ] **Step 2: Read each scraper's classification + next-action recommendation**

For each scraper, follow the recommended action from the script's output:

**If `fixed_now`** — already producing data with good pass rate. The verifier's FLAG was stale. Skip remaining steps for this scraper.

**If `no_volume`** — legitimate quiet period. Skip remaining steps; leave scheduled. The FLAG is informational.

**If `format_mismatch`** — Maricopa-class issue. Add a similar `_property_address`-style fix in the scraper. Out of scope for this plan if non-trivial; file Spec 2b.

**If `connectivity`** — most often Bright Data zone or env var issue. Quickly check:
  - `BRIGHTDATA_SB_WS` set (for Tarrant)
  - Portal URL reachable from your machine via `curl -I <url>`
  - Recently-changed credentials
  If fixable in <30 minutes → fix here. Otherwise → Step 4.

**If `parsing`** — selector drift. If easy (a known label changed), patch the scraper. Otherwise → Step 4.

- [ ] **Step 3: Apply any cheap fixes inline**

For any small fix identified (one-line selector update, env var tweak, etc.):

1. Make the change
2. Re-run `python scripts/diagnose_scraper_silence.py --scraper <name>` to confirm class is now `fixed_now` or `no_volume`
3. Commit with a message like `fix: <scraper> <small-thing>`

- [ ] **Step 4: For rebuild-class findings — deschedule + file follow-up spec**

If the diagnosis says `parsing` (major drift) or `connectivity` (Bright Data needs new zone), and fixing is more than a quick patch, remove the scraper from `services/daily_scheduler.SCHEDULED_JOBS` so the verifier stops flagging it. Document the deschedule decision in commit message and a follow-up spec stub.

Edit `services/daily_scheduler.py`:

```python
# Find the line for the broken scraper (e.g.):
# ScheduledJob("tarrant", 13, 10, "run_tarrant.py", args=("--pipe",)),
# Comment it out with a TODO:
# # DESCHEDULED 2026-05-29 — see docs/superpowers/specs/2026-05-29-tarrant-rebuild-design.md
# # ScheduledJob("tarrant", 13, 10, "run_tarrant.py", args=("--pipe",)),
```

Also update `scripts/verify_pipeline_health.py`:`SCHEDULED_JOB_COUNTIES` to drop the entry (otherwise the verifier still tries to query it).

Create a stub spec at `docs/superpowers/specs/2026-05-29-<scraper>-rebuild-design.md` capturing the diagnosis output (paste it in verbatim) so the rebuild work has context.

Commit:

```bash
git add services/daily_scheduler.py scripts/verify_pipeline_health.py docs/superpowers/specs/2026-05-29-<scraper>-rebuild-design.md
git commit -m "chore: deschedule <scraper> pending rebuild (Spec 2b stub)"
```

---

## Task 8: Final verification

- [ ] **Step 1: Run the verifier**

```bash
python scripts/verify_pipeline_health.py
echo "exit=$?"
```

Expected outcome:

| Layer | State |
|-------|-------|
| env | all OK (GHL_NG_REVIEW_STAGE_ID now set from Task 4) |
| schema | all OK (unchanged from Spec 1) |
| scrapers | Harris/Davidson/Franklin/Hamilton OK as before; Maricopa OK over the next 1-3 cron runs as fresh filings replace the stale rows; Tarrant/Cobb either OK (cheap-fix path) or absent from report (descheduled path) |
| searchbug | OK |
| ghl | GHL_NG_REVIEW_STAGE_ID OK |

Exit code: 0 in the happy path. Exit code 1 only if Tarrant/Cobb diagnoses surfaced a rebuild-class issue that was descheduled — in which case the "1" comes from no remaining issues but the descheduled scraper's follow-up spec is the next action.

- [ ] **Step 2: Run the full pytest suite to confirm no regressions**

```bash
python -m pytest --tb=short -q
```

Expected: baseline pass rate plus the new tests from this plan. Pre-existing DeKalb scraper failure is the only known unrelated failure.

- [ ] **Step 3: Push everything to origin**

```bash
git push origin main
```

- [ ] **Step 4 (optional): Wait for the next Maricopa cron tick and re-verify**

Maricopa runs at 13:40 UTC (6:40 AM PDT). Within 1-3 days of the fix landing, the most-recent-100 sample the verifier uses should be entirely new-format addresses, and Maricopa flips from FAIL → OK without further intervention.

---

## Final review checklist

- [ ] GHL `list_pipelines()` and `create_pipeline_stage()` exist + tested
- [ ] `scripts/ghl_create_review_stage.py` creates the stage idempotently and updates Railway + local env
- [ ] Maricopa `_property_address()` uses structured fields + tests pass + result passes `gate_address`
- [ ] `scripts/diagnose_scraper_silence.py` produces a classified report for any scraper passed via `--scraper`
- [ ] Tarrant + Cobb either fixed-and-still-scheduled, or descheduled with a follow-up spec stub
- [ ] `verify_pipeline_health.py` final run shows all FAILs cleared
- [ ] Full pytest suite still passes (baseline + new tests)
- [ ] No changes to `pipeline/runner.py`, `pipeline/gates.py`, or `models/` (Spec 2 is operational/scraper-only)
