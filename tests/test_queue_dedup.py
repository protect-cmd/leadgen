from pipeline.queue_builder import _person_key


def test_person_key_normalizes_name_and_zip():
    assert _person_key("SMITH, JOHN", "77002") == "john|smith|77002"
    assert _person_key("John Smith", "77002") == "john|smith|77002"


def test_person_key_matches_across_name_formats_same_zip():
    # "LAST, FIRST" (court) and "First Last" (filing) are the same person
    assert _person_key("Nwankwo, Ifeanyi", "77090") == _person_key("Ifeanyi Nwankwo", "77090")


def test_person_key_differs_by_zip():
    assert _person_key("John Smith", "77002") != _person_key("John Smith", "77004")


def test_person_key_none_when_unparseable():
    assert _person_key("Occupants", "77002") is None
    assert _person_key("", "77002") is None
