from __future__ import annotations

import argparse
import csv
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from services.ftc_dnc_registry import FtcDncRegistry


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Check phone numbers against FTC DNC area-code download files."
    )
    parser.add_argument(
        "--dnc-dir",
        required=True,
        help="Directory containing FTC DNC .txt downloads.",
    )
    parser.add_argument("--phone", help="Single phone number to check.")
    parser.add_argument("--input", help="CSV file with phone numbers to check.")
    parser.add_argument(
        "--phone-column",
        default="phone",
        help="Phone column name for CSV input. Defaults to 'phone'.",
    )
    parser.add_argument("--output", help="Output CSV path for CSV input.")
    args = parser.parse_args()

    if bool(args.phone) == bool(args.input):
        parser.error("Provide exactly one of --phone or --input.")

    registry = FtcDncRegistry.from_directory(args.dnc_dir)

    if args.phone:
        result = registry.check_phone(args.phone)
        print(
            f"{result.status}\tphone={result.phone or ''}\t"
            f"area_code={result.area_code or ''}\treason={result.reason}"
        )
        return 0

    input_path = Path(args.input)
    output_path = Path(args.output) if args.output else None
    rows = _scrub_csv(registry, input_path, args.phone_column)

    if output_path:
        _write_csv(output_path, rows)
    else:
        _write_csv(sys.stdout, rows)

    return 0


def _scrub_csv(
    registry: FtcDncRegistry,
    input_path: Path,
    phone_column: str,
) -> list[dict[str, str]]:
    with input_path.open(newline="", encoding="utf-8-sig") as handle:
        reader = csv.DictReader(handle)
        if reader.fieldnames is None:
            raise ValueError(f"CSV has no header row: {input_path}")
        if phone_column not in reader.fieldnames:
            raise ValueError(f"CSV is missing phone column: {phone_column}")

        rows: list[dict[str, str]] = []
        for row in reader:
            result = registry.check_phone(row.get(phone_column))
            output = dict(row)
            output["dnc_status"] = result.status
            output["dnc_normalized_phone"] = result.phone or ""
            output["dnc_area_code"] = result.area_code or ""
            output["dnc_source"] = "ftc_download"
            output["dnc_reason"] = result.reason
            rows.append(output)
        return rows


def _write_csv(output, rows: list[dict[str, str]]) -> None:
    if not rows:
        return

    close_handle = False
    if isinstance(output, Path):
        handle = output.open("w", newline="", encoding="utf-8")
        close_handle = True
    else:
        handle = output

    try:
        writer = csv.DictWriter(handle, fieldnames=list(rows[0].keys()))
        writer.writeheader()
        writer.writerows(rows)
    finally:
        if close_handle:
            handle.close()


if __name__ == "__main__":
    raise SystemExit(main())
