from services.ists_ghl import _split_name


def test_split_name_handles_comma_with_empty_first():
    # "Lastname," with nothing after the comma must not crash.
    assert _split_name("GAMEZ,") == ("", "Gamez")


def test_split_name_handles_plain_last_only():
    assert _split_name("GAMEZ") == ("Gamez", "")


def test_split_name_normal_comma_form():
    assert _split_name("GAMEZ, SILVIO") == ("Silvio", "Gamez")


def test_split_name_normal_space_form():
    assert _split_name("Silvio Gamez") == ("Silvio", "Gamez")


def test_split_name_strips_occupants_suffix():
    assert _split_name("Gamez, Silvio And All Other Occupants") == ("Silvio", "Gamez")
