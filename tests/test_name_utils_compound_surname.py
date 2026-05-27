from services.name_utils import parse_name


def test_de_los_surname_kept_in_last_name():
    first, last = parse_name("Stephanie De Los Santos")
    assert first == "Stephanie"
    assert last == "De Los Santos"


def test_de_la_surname_kept_in_last_name():
    first, last = parse_name("Brenda De La Torre")
    assert first == "Brenda"
    assert last == "De La Torre"


def test_van_der_surname_kept_in_last_name():
    first, last = parse_name("Hans Van Der Berg")
    assert first == "Hans"
    assert last == "Van Der Berg"


def test_del_surname_kept_in_last_name():
    first, last = parse_name("Maria Del Rio")
    assert first == "Maria"
    assert last == "Del Rio"


def test_short_name_with_de_as_middle_token_treated_as_particle():
    # "John De Smith" — particle pattern: keep "De Smith" as last name.
    first, last = parse_name("John De Smith")
    assert first == "John"
    assert last == "De Smith"


def test_three_token_plain_name_unaffected():
    # No particle present — middle stripped.
    first, last = parse_name("John Robert Smith")
    assert first == "John"
    assert last == "Smith"


def test_two_token_name_unaffected():
    first, last = parse_name("John Smith")
    assert first == "John"
    assert last == "Smith"


def test_comma_form_still_works():
    first, last = parse_name("De La Cruz, Maria")
    assert first == "Maria"
    assert last == "De La Cruz"


def test_suffix_after_particle_surname():
    first, last = parse_name("Carlos De La Cruz Jr")
    assert first == "Carlos"
    assert last == "De La Cruz"
