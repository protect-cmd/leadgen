# tests/test_franklin_judgments.py
from datetime import date
from pathlib import Path

from scrapers.ohio.franklin_judgments import (
    parse_eviction_judgments_csv,
    filter_by_disposition_window,
)

FIXTURE = Path("tests/fixtures/franklin_judgments_sample.csv").read_text(encoding="utf-8")


def test_parse_keeps_only_tenant_lost_with_full_address():
    recs = parse_eviction_judgments_csv(FIXTURE)
    kept = {r.case_number for r in recs}
    assert kept == {
        "2026 CVG 025286",  # JUDGMENT HEARD BY MAGISTRATE
        "2026 CVG 027183",  # JUDGMENT FOR PLAINTIFF
        "2026 CVG 099001",  # AGREED JUDGMENT BOTH CAUSE OF ACTION
        "2026 CVG 099005",  # JUDGMENT HEARD BY MAGISTRATE, blank state -> OH fallback
    }, f"unexpected kept set: {kept}"


def test_parse_drops_dismissed_undisposed_otherterm_blankaddr_and_entities():
    recs = parse_eviction_judgments_csv(FIXTURE)
    dropped = {r.case_number for r in recs}
    assert "2026 CVG 025291" not in dropped, "NOTICE OF DISMISSAL FILED must be dropped"
    assert "2026 CVG 099002" not in dropped, "UNDISPOSED must be dropped"
    assert "2026 CVG 025290" not in dropped, "OTHER TERMINATION - ADMIN JUDGE must be dropped (v1)"
    assert "2026 CVG 099003" not in dropped, "blank-address row must be dropped"
    assert "2026 CVG 099004" not in dropped, "LLC entity defendant must be dropped"


def test_record_field_mapping_oh_franklin():
    recs = {r.case_number: r for r in parse_eviction_judgments_csv(FIXTURE)}
    r = recs["2026 CVG 025286"]
    assert r.state == "OH"
    assert r.county == "Franklin"
    assert r.window == "W1"
    assert r.defendant_name.upper().startswith("BRITTANY")
    assert r.property_address == "270 MAYFAIR BOULEVARD, APT D, COLUMBUS, OH 43213"
    assert r.plaintiff_name == "QUEST MANAGEMENT"
    assert r.judgment_date == date(2026, 5, 19)
    assert r.disposition_date == date(2026, 5, 19)
    assert r.disposition_desc == "JUDGMENT HEARD BY MAGISTRATE"


def test_blank_state_falls_back_to_oh_and_passes_address_gate():
    recs = {r.case_number: r for r in parse_eviction_judgments_csv(FIXTURE)}
    r = recs["2026 CVG 099005"]
    assert r.property_address == "4638 TAMARACK BOULEVARD, APT B12, COLUMBUS, OH 43229"


def test_filter_by_disposition_window_keeps_only_in_range():
    recs = parse_eviction_judgments_csv(FIXTURE)
    today = date(2026, 5, 26)  # 025286 disp 05/19 -> 7d old; 027183 disp 05/13 -> 13d old
    windowed = filter_by_disposition_window(recs, today=today, floor_days=3, ceiling_days=10)
    kept = {r.case_number for r in windowed}
    assert "2026 CVG 025286" in kept       # 7 days old, within [3,10]
    assert "2026 CVG 027183" not in kept    # 13 days old, beyond ceiling 10
