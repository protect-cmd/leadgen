from services.dnc_service import result_code_verdict


def test_callable_codes():
    for c in ["C", "W", "G", "H", "c", "w"]:
        assert result_code_verdict(c) == "callable"


def test_dnc_codes():
    for c in ["D", "L", "d", "l"]:
        assert result_code_verdict(c) == "dnc"


def test_unknown_codes():
    for c in ["", None, "X", "?"]:
        assert result_code_verdict(c) == "unknown"


def test_takes_first_char_only():
    # ResultCode is single-char; tolerate stray trailing content
    assert result_code_verdict("C ") == "callable"
    assert result_code_verdict("Dnc") == "dnc"
