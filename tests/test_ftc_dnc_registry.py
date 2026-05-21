from __future__ import annotations

from scripts.build_dnc_sqlite import build as build_dnc_sqlite
from scripts.check_dnc_download import _scrub_csv
from services.ftc_dnc_registry import FtcDncRegistry


def test_downloaded_dnc_number_is_blocked(tmp_path):
    dnc_file = tmp_path / "2026-5-21_615_TEST.txt"
    dnc_file.write_text("615,5551212\n615,0000201\n", encoding="utf-8")

    registry = FtcDncRegistry.from_directory(tmp_path)
    result = registry.check_phone("+1 (615) 555-1212")

    assert result.status == "blocked"
    assert result.phone == "6155551212"
    assert result.area_code == "615"
    assert result.reason == "Phone appears in FTC DNC download"


def test_subscribed_area_code_missing_from_download_is_clear(tmp_path):
    dnc_file = tmp_path / "2026-5-21_615_TEST.txt"
    dnc_file.write_text("615,5551212\n", encoding="utf-8")

    registry = FtcDncRegistry.from_directory(tmp_path)
    result = registry.check_phone("615-555-9999")

    assert result.status == "clear"
    assert result.phone == "6155559999"
    assert result.area_code == "615"
    assert result.reason == "Area code loaded and phone not found in FTC DNC download"


def test_unloaded_area_code_is_unknown(tmp_path):
    dnc_file = tmp_path / "2026-5-21_615_TEST.txt"
    dnc_file.write_text("615,5551212\n", encoding="utf-8")

    registry = FtcDncRegistry.from_directory(tmp_path)
    result = registry.check_phone("713-555-1212")

    assert result.status == "unknown"
    assert result.phone == "7135551212"
    assert result.area_code == "713"
    assert result.reason == "Area code not loaded from FTC DNC downloads"


def test_invalid_phone_is_unknown(tmp_path):
    dnc_file = tmp_path / "2026-5-21_615_TEST.txt"
    dnc_file.write_text("615,5551212\n", encoding="utf-8")

    registry = FtcDncRegistry.from_directory(tmp_path)
    result = registry.check_phone("not-a-phone")

    assert result.status == "unknown"
    assert result.phone is None
    assert result.area_code is None
    assert result.reason == "Phone is not a valid US 10-digit number"


def test_csv_scrub_adds_dnc_audit_columns(tmp_path):
    dnc_file = tmp_path / "2026-5-21_615_TEST.txt"
    dnc_file.write_text("615,5551212\n", encoding="utf-8")
    input_file = tmp_path / "phones.csv"
    input_file.write_text(
        "case_number,phone\n"
        "A,+1 (615) 555-1212\n"
        "B,615-555-9999\n",
        encoding="utf-8",
    )

    registry = FtcDncRegistry.from_directory(tmp_path)
    rows = _scrub_csv(registry, input_file, "phone")

    assert rows[0]["case_number"] == "A"
    assert rows[0]["dnc_status"] == "blocked"
    assert rows[0]["dnc_normalized_phone"] == "6155551212"
    assert rows[0]["dnc_area_code"] == "615"
    assert rows[0]["dnc_source"] == "ftc_download"
    assert rows[1]["case_number"] == "B"
    assert rows[1]["dnc_status"] == "clear"


def _setup_sqlite_registry(tmp_path):
    input_dir = tmp_path / "in"
    input_dir.mkdir()
    (input_dir / "2026-5-21_615_TEST.txt").write_text(
        "615,5551212\n615,0000201\n", encoding="utf-8"
    )
    (input_dir / "2026-5-21_713_TEST.txt").write_text(
        "713,5559999\n", encoding="utf-8"
    )
    db_path = tmp_path / "dnc.db"
    build_dnc_sqlite(input_dir, db_path)
    return FtcDncRegistry.from_sqlite(db_path)


def test_sqlite_backend_blocks_listed_phone(tmp_path):
    registry = _setup_sqlite_registry(tmp_path)
    try:
        result = registry.check_phone("+1 (615) 555-1212")
        assert result.status == "blocked"
        assert result.phone == "6155551212"
        assert result.area_code == "615"
    finally:
        registry.close()


def test_sqlite_backend_clears_unlisted_phone_in_loaded_area_code(tmp_path):
    registry = _setup_sqlite_registry(tmp_path)
    try:
        result = registry.check_phone("615-555-9999")
        assert result.status == "clear"
        assert result.area_code == "615"
    finally:
        registry.close()


def test_sqlite_backend_marks_unloaded_area_code_unknown(tmp_path):
    registry = _setup_sqlite_registry(tmp_path)
    try:
        result = registry.check_phone("512-555-9999")
        assert result.status == "unknown"
        assert result.area_code == "512"
        assert result.reason == "Area code not loaded from FTC DNC downloads"
    finally:
        registry.close()
