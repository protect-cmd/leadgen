from datetime import date

from pipeline.lead_score import score_lead

TODAY = date(2026, 6, 10)
RATES = {"Harris": 0.71, "Franklin": 0.80, "Maricopa": 0.0}


def _score(name, fdate, county, **kw):
    return score_lead(tenant_name=name, filing_date=fdate, county=county,
                      coverage_rates=RATES, today=TODAY, **kw)


def test_clean_fresh_high_coverage_scores_high():
    # uncommon name, filed today, best-coverage county
    s = _score("Ifeanyi Nwankwo", TODAY, "Franklin")
    assert s >= 90


def test_common_surname_scores_lower_than_uncommon():
    common = _score("John Smith", TODAY, "Harris")
    uncommon = _score("Ifeanyi Nwankwo", TODAY, "Harris")
    assert common < uncommon
    # ~18-point match penalty
    assert uncommon - common >= 15


def test_low_coverage_county_drags_score():
    good = _score("Ifeanyi Nwankwo", TODAY, "Franklin")   # 0.80
    bad = _score("Ifeanyi Nwankwo", TODAY, "Maricopa")    # 0.00
    assert good > bad
    assert good - bad >= 25  # full coverage weight gap


def test_stale_filing_loses_freshness_points():
    fresh = _score("Ifeanyi Nwankwo", TODAY, "Harris")
    stale = _score("Ifeanyi Nwankwo", date(2026, 5, 20), "Harris")  # 21 days old
    assert fresh > stale
    assert fresh - stale >= 20  # near-full freshness weight


def test_unknown_county_uses_neutral_prior():
    # county not in RATES -> 0.5 prior, between Maricopa(0) and Franklin(0.8)
    unknown = _score("Ifeanyi Nwankwo", TODAY, "Nowhere")
    maricopa = _score("Ifeanyi Nwankwo", TODAY, "Maricopa")
    franklin = _score("Ifeanyi Nwankwo", TODAY, "Franklin")
    assert maricopa < unknown < franklin


def test_score_bounded_0_100():
    assert 0 <= _score("John Smith", date(2025, 1, 1), "Maricopa") <= 100
    assert 0 <= _score("Ifeanyi Nwankwo", TODAY, "Franklin") <= 100
