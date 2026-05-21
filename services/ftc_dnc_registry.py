from __future__ import annotations

import sqlite3
import threading
from dataclasses import dataclass
from pathlib import Path
import re


_DIGITS_RE = re.compile(r"\D+")


@dataclass(frozen=True)
class FtcDncCheckResult:
    status: str
    reason: str
    phone: str | None
    area_code: str | None


class FtcDncRegistry:
    """Looks up phone numbers against FTC DNC download files.

    Two backends:
      - `from_directory(dir)` — loads all .txt files into in-memory frozenset.
        Used by tests and small CSV scrub workflows.
      - `from_sqlite(db_path)` — opens a read-only SQLite connection built by
        `scripts/build_dnc_sqlite.py`. Used in production to avoid loading
        millions of numbers into Python memory.
    """

    def __init__(
        self,
        *,
        numbers: frozenset[str] | None = None,
        area_codes: frozenset[str],
        sqlite_path: Path | None = None,
    ):
        self.area_codes = area_codes
        self._numbers = numbers
        self._sqlite_path = sqlite_path
        self._sqlite_local = threading.local()

    @classmethod
    def from_directory(cls, directory: str | Path) -> "FtcDncRegistry":
        root = Path(directory)
        if not root.exists():
            raise FileNotFoundError(f"DNC directory does not exist: {root}")
        if not root.is_dir():
            raise NotADirectoryError(f"DNC path is not a directory: {root}")

        numbers: set[str] = set()
        area_codes: set[str] = set()

        for path in sorted(root.glob("*.txt")):
            with path.open(encoding="utf-8-sig") as handle:
                for line in handle:
                    area_code, phone = _parse_download_line(line)
                    if area_code is None or phone is None:
                        continue
                    area_codes.add(area_code)
                    numbers.add(phone)

        return cls(numbers=frozenset(numbers), area_codes=frozenset(area_codes))

    @classmethod
    def from_sqlite(cls, db_path: str | Path) -> "FtcDncRegistry":
        path = Path(db_path)
        if not path.exists():
            raise FileNotFoundError(f"DNC SQLite database does not exist: {path}")

        uri = f"file:{path.as_posix()}?mode=ro"
        conn = sqlite3.connect(uri, uri=True, check_same_thread=False)
        try:
            rows = conn.execute("SELECT area_code FROM dnc_area_codes").fetchall()
        finally:
            conn.close()
        area_codes = frozenset(row[0] for row in rows)

        return cls(area_codes=area_codes, sqlite_path=path)

    def _sqlite_conn(self) -> sqlite3.Connection:
        conn = getattr(self._sqlite_local, "conn", None)
        if conn is None:
            uri = f"file:{self._sqlite_path.as_posix()}?mode=ro"
            conn = sqlite3.connect(uri, uri=True, check_same_thread=False)
            self._sqlite_local.conn = conn
        return conn

    def close(self) -> None:
        """Close any open SQLite connections. Safe to call on in-memory registries."""
        conn = getattr(self._sqlite_local, "conn", None)
        if conn is not None:
            conn.close()
            self._sqlite_local.conn = None

    def _phone_is_blocked(self, phone: str) -> bool:
        if self._sqlite_path is not None:
            cur = self._sqlite_conn().execute(
                "SELECT 1 FROM dnc_numbers WHERE phone = ? LIMIT 1",
                (int(phone),),
            )
            return cur.fetchone() is not None
        assert self._numbers is not None
        return phone in self._numbers

    def check_phone(self, raw_phone: str | None) -> FtcDncCheckResult:
        phone = normalize_us_phone(raw_phone)
        if phone is None:
            return FtcDncCheckResult(
                status="unknown",
                reason="Phone is not a valid US 10-digit number",
                phone=None,
                area_code=None,
            )

        area_code = phone[:3]
        if area_code not in self.area_codes:
            return FtcDncCheckResult(
                status="unknown",
                reason="Area code not loaded from FTC DNC downloads",
                phone=phone,
                area_code=area_code,
            )

        if self._phone_is_blocked(phone):
            return FtcDncCheckResult(
                status="blocked",
                reason="Phone appears in FTC DNC download",
                phone=phone,
                area_code=area_code,
            )

        return FtcDncCheckResult(
            status="clear",
            reason="Area code loaded and phone not found in FTC DNC download",
            phone=phone,
            area_code=area_code,
        )


def normalize_us_phone(raw_phone: str | None) -> str | None:
    digits = _DIGITS_RE.sub("", raw_phone or "")
    if len(digits) == 11 and digits.startswith("1"):
        digits = digits[1:]
    if len(digits) != 10:
        return None
    return digits


def _parse_download_line(line: str) -> tuple[str | None, str | None]:
    stripped = line.strip()
    if not stripped:
        return None, None

    parts = [part.strip() for part in stripped.split(",", maxsplit=1)]
    if len(parts) != 2:
        return None, None

    area_code, local_number = parts
    if not (area_code.isdigit() and len(area_code) == 3):
        return None, None
    if not (local_number.isdigit() and len(local_number) == 7):
        return None, None

    return area_code, f"{area_code}{local_number}"
