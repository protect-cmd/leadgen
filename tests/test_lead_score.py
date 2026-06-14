from datetime import date

from pipeline.lead_score import score_lead

TODAY = date(2026, 6, 10)


def _s(rent, name="Ifeanyi Nwankwo", d=TODAY, window=21):
    return score_lead(
        rent=rent,
        tenant_name=name,
        lead_date=d,
        today=TODAY,
        fresh_window_days=window,
    )


def test_rent_is_the_dominant_factor():
    # full rent ($3500+) clean fresh name -> ~100; no rent -> drops by ~50
    assert _s(3500) >= 95
    assert _s(3500) - _s(None) >= 45


def test_rent_scales_linearly_between_floor_and_cap():
    low, mid, high = _s(800), _s(2150), _s(3500)  # floor, midpoint, cap
    assert high > mid > low
    assert abs((mid - low) - (high - mid)) <= 2  # roughly linear


def test_rent_clamped_outside_band():
    assert _s(500) == _s(800)  # below floor clamps to 0 rent-pts
    assert _s(9000) == _s(3500)  # above cap clamps to full


def test_common_surname_loses_match_points():
    common = _s(2000, name="John Smith")
    uncommon = _s(2000, name="Ifeanyi Nwankwo")
    assert uncommon - common >= 12  # ~13.5-pt match penalty


def test_stale_loses_freshness_points():
    fresh = _s(2000, d=TODAY)
    stale = _s(2000, d=date(2026, 5, 20))  # 21 days old (Vantage window)
    assert fresh - stale >= 18


def test_ists_uses_tighter_window():
    # 7-day-old judgment with window=7 -> 0 freshness; same age window=21 -> some
    assert _s(2000, d=date(2026, 6, 3), window=7) < _s(
        2000, d=date(2026, 6, 3), window=21
    )


def test_score_bounded_0_100():
    assert 0 <= _s(None, name="John Smith", d=date(2025, 1, 1)) <= 100
    assert 0 <= _s(3500) <= 100


def test_ists_profile_ignores_freshness():
    fresh = score_lead(rent=2500, tenant_name="Maria Lopez",
                       lead_date=date(2026, 6, 11), today=TODAY, profile="ists")
    stale = score_lead(rent=2500, tenant_name="Maria Lopez",
                       lead_date=date(2026, 5, 1), today=TODAY, profile="ists")
    assert fresh == stale          # freshness weight is 0 for ISTS


def test_ists_profile_spreads_low_rents_above_floor():
    low = score_lead(rent=1600, tenant_name="John Q", lead_date=TODAY,
                     today=TODAY, profile="ists")
    high = score_lead(rent=2400, tenant_name="John Q", lead_date=TODAY,
                      today=TODAY, profile="ists")
    assert 0 < low < high          # $1600 floor still scores; higher rent ranks higher


def test_vantage_profile_unchanged_default():
    # rent 50 (at cap) + match 30 (non-common) + fresh 20 (age 0) = 100
    assert score_lead(rent=3500, tenant_name="John Q",
                      lead_date=TODAY, today=TODAY) == 100
