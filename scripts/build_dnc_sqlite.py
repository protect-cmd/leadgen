"""Convert FTC DNC area-code .txt downloads into a single SQLite database.

Usage:
    python scripts/build_dnc_sqlite.py --input-dir ~/Downloads/dnc --output dnc.db

The output file should be uploaded to the Railway persistent volume mounted
at /data/dnc, and the env var FTC_DNC_DB_PATH should point at it.
"""
from __future__ import annotations

import argparse
import sqlite3
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from services.ftc_dnc_registry import _parse_download_line


SCHEMA = """
CREATE TABLE IF NOT EXISTS dnc_numbers (
    phone INTEGER PRIMARY KEY
) WITHOUT ROWID;

CREATE TABLE IF NOT EXISTS dnc_area_codes (
    area_code TEXT PRIMARY KEY
);
"""


def build(input_dir: Path, output_path: Path) -> tuple[int, int]:
    if not input_dir.is_dir():
        raise NotADirectoryError(f"Input directory does not exist: {input_dir}")

    txt_files = sorted(input_dir.glob("*.txt"))
    if not txt_files:
        raise FileNotFoundError(f"No .txt files found in {input_dir}")

    if output_path.exists():
        output_path.unlink()

    conn = sqlite3.connect(output_path)
    try:
        conn.executescript(SCHEMA)
        conn.execute("PRAGMA journal_mode = OFF")
        conn.execute("PRAGMA synchronous = OFF")

        area_codes: set[str] = set()
        total_inserted = 0

        for txt_path in txt_files:
            batch: list[tuple[int]] = []
            file_inserted = 0
            with txt_path.open(encoding="utf-8-sig") as handle:
                for line in handle:
                    area_code, phone = _parse_download_line(line)
                    if area_code is None or phone is None:
                        continue
                    area_codes.add(area_code)
                    batch.append((int(phone),))
                    if len(batch) >= 50_000:
                        conn.executemany(
                            "INSERT OR IGNORE INTO dnc_numbers(phone) VALUES (?)",
                            batch,
                        )
                        file_inserted += len(batch)
                        batch.clear()
            if batch:
                conn.executemany(
                    "INSERT OR IGNORE INTO dnc_numbers(phone) VALUES (?)",
                    batch,
                )
                file_inserted += len(batch)
            print(f"  {txt_path.name}: {file_inserted:,} numbers")
            total_inserted += file_inserted

        conn.executemany(
            "INSERT OR IGNORE INTO dnc_area_codes(area_code) VALUES (?)",
            [(code,) for code in sorted(area_codes)],
        )
        conn.commit()

        (unique_count,) = conn.execute("SELECT COUNT(*) FROM dnc_numbers").fetchone()
        return unique_count, len(area_codes)
    finally:
        conn.close()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input-dir", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()

    started = time.time()
    print(f"Building DNC SQLite database from {args.input_dir}")
    unique_count, area_code_count = build(args.input_dir, args.output)
    elapsed = time.time() - started
    size_mb = args.output.stat().st_size / (1024 * 1024)
    print(
        f"Done in {elapsed:.1f}s - {unique_count:,} unique phones across "
        f"{area_code_count} area codes ({size_mb:.1f} MB) -> {args.output}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
