"""Create the 'Review - SearchBug Mismatch' stage in the NG GHL pipeline
and set GHL_NG_REVIEW_STAGE_ID on Railway. Idempotent - safe to re-run.

Usage:
    python scripts/ghl_create_review_stage.py            # prints what it would do
    python scripts/ghl_create_review_stage.py --apply    # actually creates + sets env
"""
from __future__ import annotations

import argparse
import asyncio
import os
import re
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from dotenv import load_dotenv

STAGE_NAME = "Review - SearchBug Mismatch"
ENV_KEY = "GHL_NG_REVIEW_STAGE_ID"


async def find_ng_pipeline_id() -> str:
    """Find the pipeline that contains GHL_NG_NEW_FILING_STAGE_ID."""
    from services.ghl_service import list_pipelines

    new_filing_stage = os.environ.get("GHL_NG_NEW_FILING_STAGE_ID")
    if not new_filing_stage:
        raise RuntimeError("GHL_NG_NEW_FILING_STAGE_ID not set; cannot locate NG pipeline")

    pipelines = await list_pipelines(track="ng")
    for pipe in pipelines:
        for stage in pipe.get("stages", []):
            if stage.get("id") == new_filing_stage:
                return pipe["id"]
    raise RuntimeError(
        f"No NG pipeline contains stage {new_filing_stage!r}; cannot create review stage."
    )


def _update_local_env(stage_id: str) -> None:
    """Append or update GHL_NG_REVIEW_STAGE_ID=<id> in local .env."""
    env_path = Path(__file__).parent.parent / ".env"
    if not env_path.exists():
        print(f"  (no local .env at {env_path}, skipping)")
        return
    contents = env_path.read_text(encoding="utf-8")
    new_line = f"{ENV_KEY}={stage_id}"
    pat = re.compile(rf"^{re.escape(ENV_KEY)}=.*$", re.MULTILINE)
    if pat.search(contents):
        contents = pat.sub(new_line, contents)
    else:
        if not contents.endswith("\n"):
            contents += "\n"
        contents += new_line + "\n"
    env_path.write_text(contents, encoding="utf-8")
    print(f"  updated local .env with {ENV_KEY}=...{stage_id[-6:]}")


def _set_railway_var(stage_id: str) -> None:
    """Run railway variable set <KEY>=<value> --skip-deploys."""
    cmd = ["railway", "variable", "set", f"{ENV_KEY}={stage_id}", "--skip-deploys"]
    print(f"  running: {' '.join(cmd[:4])} <value redacted> {cmd[-1]}")
    r = subprocess.run(" ".join(cmd), shell=True, capture_output=True, text=True, timeout=120)
    if r.returncode != 0:
        raise RuntimeError(f"railway variable set failed: {r.stderr.strip()}")


def _railway_redeploy() -> None:
    cmd = ["railway", "redeploy", "--service", "leadgen", "--yes"]
    print(f"  running: {' '.join(cmd)}")
    subprocess.run(" ".join(cmd), shell=True, capture_output=True, text=True, timeout=120)


async def main_async(apply: bool) -> int:
    load_dotenv()

    from services.ghl_service import create_pipeline_stage

    print("Discovering NG pipeline...")
    pipeline_id = await find_ng_pipeline_id()
    print(f"  NG pipeline id: {pipeline_id}")

    if not apply:
        print(f"\nWould create stage {STAGE_NAME!r} at position 0 in pipeline {pipeline_id}")
        print("Re-run with --apply to actually create + set env.")
        return 0

    print(f"\nCreating (or finding) stage {STAGE_NAME!r}...")
    stage_id = await create_pipeline_stage(
        track="ng",
        pipeline_id=pipeline_id,
        name=STAGE_NAME,
        position=0,
    )
    print(f"  stage id: {stage_id}")

    print("\nSetting Railway env...")
    _set_railway_var(stage_id)

    print("\nUpdating local .env...")
    _update_local_env(stage_id)

    print("\nTriggering Railway redeploy...")
    _railway_redeploy()

    print(f"\nDone. {ENV_KEY}={stage_id}")
    print("Run python scripts/verify_pipeline_health.py to confirm.")
    return 0


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--apply", action="store_true",
                   help="Actually create the stage + set env (default is dry-run)")
    args = p.parse_args()
    return asyncio.run(main_async(args.apply))


if __name__ == "__main__":
    raise SystemExit(main())
