"""Business-entity filtering is now centralized in pipeline.gates.gate_name.
These tests pin the regex coverage that previously lived in runner._is_business_name."""
from pipeline.gates import gate_name


def test_estate_of_treated_as_business():
    assert gate_name("Estate of John Doe") is False


def test_dba_treated_as_business():
    assert gate_name("John Smith DBA Acme Diner") is False


def test_co_treated_as_business():
    assert gate_name("Properties LLC c/o Jane Doe") is False


def test_bank_treated_as_business():
    assert gate_name("First National Bank") is False


def test_individual_not_business():
    assert gate_name("Maria Garcia") is True


def test_llc_still_flagged():
    assert gate_name("Pure Auto Spa, LLC") is False
