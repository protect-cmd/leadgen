from pipeline.runner import _is_business_name


def test_estate_of_treated_as_business():
    assert _is_business_name("Estate of John Doe") is True


def test_dba_treated_as_business():
    assert _is_business_name("John Smith DBA Acme Diner") is True


def test_co_treated_as_business():
    assert _is_business_name("Properties LLC c/o Jane Doe") is True


def test_bank_treated_as_business():
    assert _is_business_name("First National Bank") is True


def test_individual_not_business():
    assert _is_business_name("Maria Garcia") is False


def test_llc_still_flagged():
    assert _is_business_name("Pure Auto Spa, LLC") is True
