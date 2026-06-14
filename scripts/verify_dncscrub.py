"""Ops check: is the DNCScrub API live, and what verdict does it give a test number?

    python scripts/verify_dncscrub.py [--phone 6155551234]

Exit 0 if the API is configured and answered; 1 if it is in local-files-only mode
(callable leads in uncovered area codes get suppressed as DNC by fail-closed).
"""
from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from dotenv import load_dotenv

load_dotenv()
from services import dnc_service


def check(test_phone: str = "2025551234") -> dict:
    configured = bool(os.getenv("DNCSCRUB_LOGIN_ID", "").strip())
    report = {
        "api_configured": configured,
        "mode": "dncscrub_api" if configured else "local_files_only",
        "fail_closed": dnc_service._fail_closed(),
    }
    if configured:
        api = dnc_service._api_verdicts([test_phone])
        report["api_answered"] = bool(api)
        report["test_verdict"] = api.get(dnc_service._digits(test_phone) or "")
    return report


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--phone", default="2025551234")
    a = ap.parse_args(argv)
    report = check(a.phone)
    for k, val in report.items():
        print(f"{k}: {val}")
    return 0 if report["api_configured"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
