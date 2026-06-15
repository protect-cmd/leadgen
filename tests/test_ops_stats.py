from datetime import date

from services import ops_stats as ops


# --------------------------------------------------------------------------- #
# pure helpers
# --------------------------------------------------------------------------- #
def test_pct_drop_stages():
    stages = [{"label": "Scraped", "count": 100},
              {"label": "Enrichable", "count": 60},
              {"label": "Rent>=1600", "count": 30}]
    out = ops.with_pct(stages)
    assert out[0]["pct"] is None
    assert out[1]["pct"] == 60
    assert out[2]["pct"] == 50


def test_with_pct_handles_zero_previous():
    out = ops.with_pct([{"label": "a", "count": 0}, {"label": "b", "count": 0}])
    assert out[1]["pct"] is None


def test_sparkline_maps_to_blocks():
    assert ops.sparkline([]) == ""
    s = ops.sparkline([0, 5, 10])
    assert len(s) == 3
    assert s[0] != s[2]
    assert ops.sparkline([4, 4, 4]) == "▅▅▅"   # all-equal -> mid block (index 4 of 8)


# --------------------------------------------------------------------------- #
# fakes
# --------------------------------------------------------------------------- #
class _Result:
    def __init__(self, data): self.data = data


class _Query:
    """Filters are no-ops; the test supplies the exact rows a table should return.
    range() serves all rows once then empty (stops pagination)."""
    def __init__(self, rows): self._rows = rows; self._served = False
    def select(self, *a, **k): return self
    def eq(self, *a, **k): return self
    def gte(self, *a, **k): return self
    def lte(self, *a, **k): return self
    def lt(self, *a, **k): return self
    def in_(self, *a, **k): return self
    def order(self, *a, **k): return self
    @property
    def not_(self): return self
    def is_(self, *a, **k): return self
    def limit(self, *a, **k): return self
    def range(self, lo, hi):
        self._served = not self._served
        return self
    def execute(self):
        return _Result(self._rows if self._served else [])


class FakeSB:
    def __init__(self, tables): self.tables = tables
    def table(self, name): return _Query(list(self.tables.get(name, [])))


class _FakeCache:
    def __init__(self, counts, ops_kv):
        self._counts = counts; self._kv = ops_kv
    def check_daily_cap(self, cap, kind="searchbug"): return self._counts.get(kind, 0) < cap
    def daily_count(self, kind="searchbug"): return self._counts.get(kind, 0)
    def get_ops_value_with_ts(self, key): return self._kv.get(key, (None, None))


# --------------------------------------------------------------------------- #
# funnel
# --------------------------------------------------------------------------- #
def test_funnel_counts_and_pct_drop():
    today = date(2026, 6, 15)
    filings = [
        {"case_number": "V1", "is_enrichable": True, "estimated_rent": 2000},
        {"case_number": "V2", "is_enrichable": True, "estimated_rent": 1700},
        {"case_number": "V3", "is_enrichable": False, "estimated_rent": 2500},
        {"case_number": "V4", "is_enrichable": True, "estimated_rent": 1200},
    ]
    lead_contacts = [
        {"case_number": "V1", "phone": "1", "dnc_status": "callable",
         "bland_call_id": "b1", "ghl_contact_id": "g1"},
        {"case_number": "V2", "phone": "2", "dnc_status": "dnc",
         "bland_call_id": None, "ghl_contact_id": "g2"},
    ]
    sb = FakeSB({"filings": filings, "lead_contacts": lead_contacts, "ists_judgments": []})
    v = ops.funnel(sb, today=today)["vantage"]
    by = {s["label"]: s["count"] for s in v["stages"]}
    assert by["Scraped"] == 4
    assert by["Enrichable"] == 3
    assert by["Rent >= $1600"] == 2
    assert by["Phone found"] == 2
    assert by["Callable"] == 1
    assert "Fired" not in by and "Staged to GHL" not in by   # outcomes, not stages
    assert v["outcomes"] == {"fired": 1, "staged": 2}
    assert next(s for s in v["stages"] if s["label"] == "Enrichable")["pct"] == 75


# --------------------------------------------------------------------------- #
# spend / health / isolation
# --------------------------------------------------------------------------- #
def test_spend_reports_today_counts_and_caps(monkeypatch):
    cache = _FakeCache({"searchbug": 73, "bland": 99},
                       {"rentometer_credits": ("263", "2026-06-14T10:00:00Z")})
    monkeypatch.setenv("SEARCHBUG_DAILY_CAP", "100")
    monkeypatch.setenv("BLAND_DAILY_CAP", "100")
    s = ops.spend(cache)
    assert s["bland_today"] == 99 and s["bland_cap"] == 100
    assert s["searchbug_today"] == 73
    assert s["rentometer_credits"] == 263


def test_health_flags_dark_scraper(monkeypatch):
    today = date(2026, 6, 15)
    filings = [
        {"county": "Harris", "scraped_at": "2026-06-15T13:00:00Z", "enrichable_checked_at": "2026-06-15T15:00:00Z"},
        {"county": "Hamilton", "scraped_at": "2026-05-17T13:00:00Z", "enrichable_checked_at": None},
    ]
    sb = FakeSB({"filings": filings})
    cache = _FakeCache({"bland": 0}, {})
    monkeypatch.setenv("DNCSCRUB_LOGIN_ID", "x")  # avoid the DNCScrub warn flag
    flags = ops.health_flags(sb, cache, today=today)["flags"]
    msgs = " ".join(f["msg"] for f in flags)
    assert "Hamilton" in msgs and "dark" in msgs.lower()


def test_montgomery_is_an_expected_scraped_county():
    # Montgomery (Dayton) is scheduled (daily_scheduler) and must appear on /ops
    # health + scrapes so it can be flagged dark like the others.
    assert "Montgomery" in ops._EXPECTED_COUNTIES


def test_trend_survives_missing_bland_triggered_at(monkeypatch):
    # Pre-migration: lead_contacts.bland_triggered_at doesn't exist -> the fired
    # query raises, but filings/phones (from run_metrics) must still populate.
    today = date(2026, 6, 15)
    monkeypatch.setattr(ops, "_paginate_rm",
                        lambda sb, gte: [{"run_at": "2026-06-15T13:00:00Z",
                                          "filings_received": 10, "phones_found": 3}])
    def _boom(*a, **k):
        raise RuntimeError("column lead_contacts.bland_triggered_at does not exist")
    monkeypatch.setattr(ops, "_paginate", _boom)
    out = ops.trend(FakeSB({}), today=today)
    assert sum(out["filings"]) == 10        # from run_metrics, unaffected
    assert sum(out["phones"]) == 3
    assert out["fired"] == [0, 0, 0, 0, 0, 0, 0]   # degraded to zeros, no crash


def test_get_ops_stats_isolates_section_errors(monkeypatch):
    monkeypatch.setattr(ops, "funnel", lambda sb, today=None: (_ for _ in ()).throw(RuntimeError("boom")))
    monkeypatch.setattr(ops, "scrapes", lambda sb, today=None: {"rows": []})
    monkeypatch.setattr(ops, "spend", lambda cache: {"bland_today": 0})
    monkeypatch.setattr(ops, "health_flags", lambda sb, cache, today=None: {"flags": []})
    monkeypatch.setattr(ops, "trend", lambda sb, today=None: {"filings": []})
    out = ops.get_ops_stats(FakeSB({}), _FakeCache({}, {}))
    assert out["funnel"]["error"]
    assert out["scrapes"] == {"rows": []}
    assert "as_of" in out
