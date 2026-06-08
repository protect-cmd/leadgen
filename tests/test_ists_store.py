import asyncio
from pathlib import Path
from datetime import date
import pytest
import services.ists_store as store
from models.judgment import JudgmentRecord


class _FakeTable:
    def __init__(self): self.upserts = []
    def upsert(self, payload, on_conflict=None):
        self.upserts.append((payload, on_conflict)); return self
    def execute(self): return type("R", (), {"data": [self.upserts[-1][0]]})()


class _FakeClient:
    def __init__(self): self.t = _FakeTable(); self.tables = []
    def table(self, name): self.tables.append(name); return self.t


def test_upsert_writes_ists_judgments_only(monkeypatch):
    fake = _FakeClient()
    monkeypatch.setattr(store, "_client", fake)
    rec = JudgmentRecord("261100242063", "Mariah Taylor",
                         "1617 Fannin Street Apt 1811, Houston, TX 77002",
                         judgment_date=date(2026, 6, 1), judgment_against="Mariah Taylor")
    asyncio.run(store.upsert_judgment(rec))
    assert fake.tables == ["ists_judgments"]          # never touches filings/lead_contacts
    payload, on_conflict = fake.t.upserts[-1]
    assert on_conflict == "case_number"
    assert payload["case_number"] == "261100242063"


def test_store_module_references_no_prod_tables():
    src = Path("services/ists_store.py").read_text(encoding="utf-8")
    assert '"filings"' not in src and "'filings'" not in src
    assert '"lead_contacts"' not in src and "'lead_contacts'" not in src


@pytest.mark.parametrize("path", [
    "services/ists_store.py",
    "jobs/run_ists_harris.py",
    "scrapers/texas/harris_judgments.py",
])
def test_no_ists_module_writes_prod_tables(path):
    src = Path(path).read_text(encoding="utf-8")
    for forbidden in (".insert(", ".update(", ".upsert(", ".delete("):
        if forbidden in src:
            # The only allowed write is upsert to ists_judgments inside ists_store
            assert path == "services/ists_store.py" and 'on_conflict="case_number"' in src, (
                f"{path} performs a write ({forbidden}) — ISTS must only write ists_judgments"
            )
    # prior_work + scraper never reference prod tables as write targets
    if path != "services/ists_store.py":
        assert ".table(\"filings\")" not in src and ".table('filings')" not in src
