from datetime import datetime, timezone

from services.call_window import in_call_window, tz_for_state

UTC = timezone.utc
# 2026-06-15 is a Monday; 2026-06-14 is a Sunday.
MON_14Z = datetime(2026, 6, 15, 14, 0, tzinfo=UTC)   # Central 09:00, Eastern 10:00, Phoenix 07:00
SUN_14Z = datetime(2026, 6, 14, 14, 0, tzinfo=UTC)   # Central 09:00 Sunday, Eastern 10:00 Sunday


def test_in_window_by_local_timezone():
    assert in_call_window("TX", MON_14Z) is True    # 09:00 CT
    assert in_call_window("TN", MON_14Z) is True     # 09:00 CT
    assert in_call_window("OH", MON_14Z) is True     # 10:00 ET
    assert in_call_window("AZ", MON_14Z) is False    # 07:00 MST — before 8am


def test_evening_cutoff_is_local_and_exclusive():
    # 2026-06-16 02:00Z -> Central 21:00 (Mon) -> excluded; Eastern 22:00 -> excluded
    dt = datetime(2026, 6, 16, 2, 0, tzinfo=UTC)
    assert in_call_window("TX", dt) is False
    assert in_call_window("OH", dt) is False


def test_sunday_before_10am_blocked_local():
    # Sunday 09:00 CT blocked; same instant is 10:00 ET -> allowed.
    assert in_call_window("TX", SUN_14Z) is False
    assert in_call_window("OH", SUN_14Z) is True


def test_env_override_tightens_window(monkeypatch):
    monkeypatch.setenv("CALL_WINDOW_START_HOUR", "10")
    assert in_call_window("TX", MON_14Z) is False     # 09:00 CT now before the 10am start


def test_unknown_state_falls_back_to_central():
    assert str(tz_for_state("ZZ")) == "America/Chicago"
    assert str(tz_for_state(None)) == "America/Chicago"
