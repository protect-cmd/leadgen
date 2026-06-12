from scripts.rent_targeting import zip_yield, select_targets, tier_of


def test_zip_yield_requires_min_samples():
    # ZIP A has 8 estimates (trusted), ZIP B has 3 (ignored).
    est = [("A", 2000)] * 6 + [("A", 1000)] * 2 + [("B", 5000)] * 3
    y = zip_yield(est, min_n=8)
    assert "A" in y and "B" not in y
    assert y["A"]["n"] == 8
    assert y["A"]["pct"] == 0.75          # 6 of 8 >= 1600
    assert y["A"]["median"] == 2000.0     # median of [1000,1000,2000,2000,2000,2000,2000,2000]


def test_select_targets_ranks_proven_by_pct_then_drops_tail():
    yields = {
        "HI": {"median": 2000.0, "pct": 0.90, "n": 10},   # proven, best yield
        "MID": {"median": 3000.0, "pct": 0.70, "n": 12},  # proven, higher median but lower %
    }
    priority = {"PRI"}
    cands = [
        {"case_number": "tail", "property_zip": "ZZZ", "score": 99},   # unproven, not priority -> dropped
        {"case_number": "pri",  "property_zip": "PRI", "score": 50},   # priority-only -> tier 1
        {"case_number": "mid",  "property_zip": "MID", "score": 10},   # proven
        {"case_number": "hi",   "property_zip": "HI",  "score": 10},   # proven, best
    ]
    out = select_targets(cands, yields, priority)
    assert [c["case_number"] for c in out] == ["hi", "mid", "pri"]   # tail dropped, proven first by %
    assert "tail" not in [c["case_number"] for c in out]


def test_select_targets_proven_beats_priority_even_with_lower_score():
    yields = {"HI": {"median": 1800.0, "pct": 0.80, "n": 9}}
    priority = {"PRI"}
    cands = [
        {"case_number": "pri_highscore", "property_zip": "PRI", "score": 95},
        {"case_number": "hi_lowscore",   "property_zip": "HI",  "score": 1},
    ]
    out = select_targets(cands, yields, priority)
    # proven ZIP wins the tier even though its lead score is much lower
    assert out[0]["case_number"] == "hi_lowscore"


def test_tier_labels():
    yields = {"HI": {"median": 2000.0, "pct": 0.90, "n": 10}}
    priority = {"PRI"}
    assert tier_of({"property_zip": "HI"}, yields, priority) == "proven"
    assert tier_of({"property_zip": "PRI"}, yields, priority) == "priority"
    assert tier_of({"property_zip": "ZZZ"}, yields, priority) == "tail"
