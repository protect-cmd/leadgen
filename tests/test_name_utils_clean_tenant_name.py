from services.name_utils import clean_tenant_name


def test_strips_trailing_occupants_with_period():
    assert clean_tenant_name("Kenae Mayhorn and all other occupants.") == "Kenae Mayhorn"


def test_strips_trailing_occupants_no_other():
    assert clean_tenant_name("Vy Cao and all occupants") == "Vy Cao"


def test_strips_and_or_all_occupants():
    assert clean_tenant_name("Brenda V Villarreal and/or All Occupants") == "Brenda V Villarreal"


def test_strips_and_or_all_occupants_of_address():
    raw = "Dana Breyuntae Knighten and/or All Occupants of 3119 Peachstone Pl Spring, TX 7389-4688"
    assert clean_tenant_name(raw) == "Dana Breyuntae Knighten"


def test_strips_long_noise_tail():
    raw = "BRANDON SAUNDERS, AND ALL OCCUPANTS, UNKNOWN OCCUPANTS, TENANTS, AND SUBTENANTS"
    assert clean_tenant_name(raw) == "BRANDON SAUNDERS"


def test_strips_et_al():
    assert clean_tenant_name("John Smith, et al.") == "John Smith"


def test_returns_empty_for_john_doe_placeholder():
    assert clean_tenant_name("John Doe") == ""
    assert clean_tenant_name("Jane Doe") == ""


def test_returns_empty_for_unknown_tenant():
    assert clean_tenant_name("Unknown Tenant") == ""
    assert clean_tenant_name("All Occupants") == ""
    assert clean_tenant_name("Tenant in Possession") == ""


def test_returns_empty_for_squaters_typo():
    assert clean_tenant_name("Squaters") == ""


def test_returns_empty_for_blank_input():
    assert clean_tenant_name("") == ""
    assert clean_tenant_name("   ") == ""


def test_passes_clean_name_through_unchanged():
    assert clean_tenant_name("Sherrick Campbell") == "Sherrick Campbell"
