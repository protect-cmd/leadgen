from services.dnc_service import result_code_verdict, row_verdict


def test_callable_codes():
    for c in ["C", "W", "G", "H", "B", "c", "b"]:
        assert result_code_verdict(c) == "callable"


def test_dnc_codes():
    for c in ["D", "L", "F", "d", "l"]:
        assert result_code_verdict(c) == "dnc"


def test_unknown_codes():
    for c in ["", None, "X", "I"]:
        assert result_code_verdict(c) == "unknown"


# ── row_verdict: Reason field is authoritative ───────────────────────────────

def test_row_wireless_clean_is_callable():
    # observed held-number shape: code B, Reason ";;;W" = clean of DNC, wireless
    assert row_verdict({"ResultCode": "B", "Reason": ";;;W"}) == "callable"


def test_row_on_national_dnc_is_dnc_even_if_code_clean():
    assert row_verdict({"ResultCode": "C", "Reason": "National (USA) 2003-06-01;;;"}) == "dnc"


def test_row_state_or_internal_dnc_is_dnc():
    assert row_verdict({"ResultCode": "W", "Reason": ";TX;;"}) == "dnc"
    assert row_verdict({"ResultCode": "W", "Reason": ";;Internal;"}) == "dnc"


def test_row_dnc_code_wins():
    assert row_verdict({"ResultCode": "D", "Reason": ";;;"}) == "dnc"


def test_row_invalid_is_unknown():
    assert row_verdict({"ResultCode": "I", "Reason": ";;;"}) == "unknown"
