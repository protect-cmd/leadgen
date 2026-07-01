"""Phase 6: per-business scoring profiles (Cosner debt-value, GP freshness)."""
from __future__ import annotations

from datetime import date, timedelta

from pipeline.lead_score import _PROFILES, score_lead

TODAY = date(2026, 6, 28)


def test_all_profiles_weights_sum_to_100():
    for name, p in _PROFILES.items():
        assert p["w_rent"] + p["w_match"] + p["w_fresh"] == 100, name


def test_cosner_is_value_first_higher_debt_scores_higher():
    big = score_lead(rent=20000, tenant_name="JOHN SMITH", lead_date=TODAY,
                     today=TODAY, profile="cosner")
    small = score_lead(rent=1000, tenant_name="JOHN SMITH", lead_date=TODAY,
                       today=TODAY, profile="cosner")
    assert big > small


def test_cosner_rewards_freshness_within_answer_window():
    fresh = score_lead(rent=5000, tenant_name="JOHN SMITH", lead_date=TODAY,
                       today=TODAY, profile="cosner")
    stale = score_lead(rent=5000, tenant_name="JOHN SMITH",
                       lead_date=TODAY - timedelta(days=60), today=TODAY, profile="cosner")
    assert fresh > stale


def test_garnish_proof_ignores_amount_and_rewards_fresh_writ():
    # w_rent=0 -> passing an amount must not change the score
    with_amt = score_lead(rent=9999, tenant_name="JANE DOE", lead_date=TODAY,
                          today=TODAY, profile="garnish_proof")
    no_amt = score_lead(rent=None, tenant_name="JANE DOE", lead_date=TODAY,
                        today=TODAY, profile="garnish_proof")
    assert with_amt == no_amt
    # fresher writ scores higher than an old one
    stale = score_lead(rent=None, tenant_name="JANE DOE",
                       lead_date=TODAY - timedelta(days=40), today=TODAY,
                       profile="garnish_proof")
    assert no_amt > stale
    assert 0 <= no_amt <= 100
