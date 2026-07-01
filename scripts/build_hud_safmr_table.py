"""Build the ZIP-level HUD Small Area FMR lookup table used by the rent precheck.

HUD publishes one national SAFMR workbook per fiscal year (ZIP-level 0-4BR rents,
the 40th-percentile voucher standard — see docs/hud_fmr_vs_rentometer_research.md).
This script downloads that workbook and flattens it to a compact CSV the
`hud`/`safmr` rent provider loads at runtime.

Refresh annually when HUD posts the new FY file (each fall):

    python scripts/build_hud_safmr_table.py \
        --url https://www.huduser.gov/portal/datasets/fmr/fmr2026/fy2026_safmrs_revised.xlsx \
        --out resources/hud_safmr_fy2026.csv

If --xlsx points at an already-downloaded workbook, the download is skipped.
"""

from __future__ import annotations

import argparse
import csv
import sys
import tempfile
import urllib.request
from pathlib import Path

import openpyxl

DEFAULT_URL = "https://www.huduser.gov/portal/datasets/fmr/fmr2026/fy2026_safmrs_revised.xlsx"
DEFAULT_OUT = Path(__file__).resolve().parent.parent / "resources" / "hud_safmr_fy2026.csv"

# huduser.gov returns an empty body to the default urllib/curl User-Agent.
_BROWSER_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/126 Safari/537.36"
)

# Column positions in the FY2026 SAFMR workbook (0-based).
_ZIP_COL = 0
_BR_COLS = {0: 3, 1: 6, 2: 9, 3: 12, 4: 15}  # bedrooms -> column index for SAFMR rent


def _download(url: str) -> Path:
    tmp = Path(tempfile.gettempdir()) / "hud_safmr_download.xlsx"
    req = urllib.request.Request(url, headers={"User-Agent": _BROWSER_UA})
    with urllib.request.urlopen(req) as resp, open(tmp, "wb") as out:
        out.write(resp.read())
    if tmp.stat().st_size == 0:
        raise RuntimeError(f"Downloaded an empty file from {url}")
    return tmp


def build(xlsx_path: Path, out_path: Path) -> int:
    wb = openpyxl.load_workbook(xlsx_path, read_only=True)
    ws = wb.active
    rows = ws.iter_rows(values_only=True)
    next(rows)  # discard header

    out_path.parent.mkdir(parents=True, exist_ok=True)
    written = 0
    with open(out_path, "w", newline="", encoding="utf-8") as handle:
        writer = csv.writer(handle)
        writer.writerow(["zip", "br0", "br1", "br2", "br3", "br4"])
        for row in rows:
            zip_raw = row[_ZIP_COL]
            if zip_raw is None:
                continue
            zip_code = str(zip_raw).split(".")[0].zfill(5)
            rents = [row[_BR_COLS[b]] for b in range(5)]
            if all(r is None for r in rents):
                continue
            writer.writerow([zip_code] + [("" if r is None else int(r)) for r in rents])
            written += 1
    return written


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--url", default=DEFAULT_URL, help="SAFMR workbook URL to download")
    parser.add_argument("--xlsx", help="Use an existing local workbook instead of downloading")
    parser.add_argument("--out", type=Path, default=DEFAULT_OUT, help="Output CSV path")
    args = parser.parse_args(argv)

    xlsx_path = Path(args.xlsx) if args.xlsx else _download(args.url)
    count = build(xlsx_path, args.out)
    print(f"Wrote {count} ZIP rows to {args.out}", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
