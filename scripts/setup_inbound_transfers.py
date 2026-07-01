"""Set up one dedicated inbound-transfer pathway per business (separate-number model).

Each of the four brands now dials out from its OWN Bland number, so a callback to
that number already identifies the brand — no IVR menu needed. This script gives
each number a tiny inbound pathway that greets the caller and immediately transfers
to that brand's RingCentral toll-free line.

Number -> RingCentral map (set in BRANDS below):
  VDG     +18186167276 -> +18882141711   (was the shared IVR number; IVR removed here)
  ISTS    +16506293987 -> +18883224034
  Cosner  +16507105017 -> +18883382915
  Garnish +16506093551 -> +18882242863

Removing the shared IVR: this script rebinds +18186167276 to VDG's own single-brand
transfer pathway, which detaches the "Shared Inbound IVR - Callback Router"
(57e5af09) from the only number it was on. The IVR pathway stays in the library
(unbound) in case we ever revert. See docs/bland_inbound_ivr.md.

Bland field quirks (same as setup_inbound_ivr.py — don't "fix" these):
  - Inbound routes by a NUMBERED VERSION, not the draft. POST /v1/pathway/{id}
    updates only the draft; mint a version with POST /v1/pathway/{id}/version and
    bind the number to THAT version number.
  - Edge routing condition must live in edge["data"]["label"].
  - Calls need a browser User-Agent or Cloudflare returns 1010.

Run: railway run --service leadgen python scripts/setup_inbound_transfers.py
Env: BLAND_API_KEY (required).
     BLAND_{ISTS,COSNER,GARNISH}_INBOUND_PATHWAY_ID (optional — update in place
     instead of creating a new pathway; printed on first create so you can persist).
"""
from __future__ import annotations

import os
import sys

import httpx
from dotenv import load_dotenv

load_dotenv()

BASE = "https://api.bland.ai"
IVR_VOICE = "june"  # warm receptionist preset (matches the prior shared IVR)

SHARED_IVR_PATHWAY_ID = "57e5af09-ecab-47d3-b51d-08d28a7cbef3"  # detached by this run

# brand -> dedicated Bland number, RingCentral line, display label, and the existing
# pathway id (prefilled where one already exists; "" means create on first run).
BRANDS = {
    "vantage": {
        "number": "+18186167276", "rc": "+18882141711",
        "label": "Vantage Defense Group",
        "pathway_id": "ee472a79-4d28-40f1-9b2d-3ac2a2d3fe30",
    },
    "ists": {
        "number": "+16506293987", "rc": "+18883224034",
        "label": "ISTS",
        "pathway_id": os.environ.get("BLAND_ISTS_INBOUND_PATHWAY_ID",
                                     "10a68966-7da1-4a6f-b87c-8e9c440621d5").strip(),
    },
    "cosner": {
        "number": "+16507105017", "rc": "+18883382915",
        "label": "Cosner Drake",
        "pathway_id": os.environ.get("BLAND_COSNER_INBOUND_PATHWAY_ID",
                                     "094caad7-0991-4b50-9d7c-4ddd07c2bdb5").strip(),
    },
    "garnish": {
        "number": "+16506093551", "rc": "+18882242863",
        "label": "Garnish Proof",
        "pathway_id": os.environ.get("BLAND_GARNISH_INBOUND_PATHWAY_ID",
                                     "aa44d792-6bef-4857-8959-fec9340ef3af").strip(),
    },
}


def _graph(rc: str) -> tuple[list, list]:
    """A two-node pathway: greet, then transfer to the brand's RingCentral line."""
    nodes = [
        {"id": "1", "type": "Default",
         "data": {"name": "Inbound Greeting", "isStart": True,
                  "prompt": "Thanks for calling back — connecting you to the team now, "
                            "one moment."}},
        {"id": "t", "type": "Transfer Call",
         "data": {"name": "Transfer To Closer", "transferNumber": rc}},
    ]
    edges = [
        {"id": "e_connect", "source": "1", "target": "t",
         "data": {"label": "connect caller", "name": "connect caller"}},
    ]
    return nodes, edges


def _headers() -> dict:
    key = os.environ.get("BLAND_API_KEY", "")
    if not key:
        sys.exit("BLAND_API_KEY not set")
    return {"authorization": key, "Content-Type": "application/json",
            "User-Agent": ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                           "(KHTML, like Gecko) Chrome/124.0 Safari/537.36")}


def main() -> int:
    h = _headers()
    with httpx.Client(timeout=30, headers=h) as c:
        for brand, cfg in BRANDS.items():
            label, number, rc = cfg["label"], cfg["number"], cfg["rc"]
            name = f"{label} Inbound Transfer"
            nodes, edges = _graph(rc)
            pid = cfg["pathway_id"]

            if not pid:
                r = c.post(f"{BASE}/v1/pathway/create",
                           json={"name": name,
                                 "description": f"Callback transfer to {label} RingCentral line."})
                r.raise_for_status()
                body = r.json()
                pid = body.get("pathway_id") or body.get("data", {}).get("pathway_id")
                print(f"[{brand}] created pathway: {pid}  "
                      f"(persist as BLAND_{brand.upper()}_INBOUND_PATHWAY_ID)")

            # keep the editable draft in sync with what we bind...
            r = c.post(f"{BASE}/v1/pathway/{pid}",
                       json={"name": name,
                             "description": f"Callback transfer to {label} RingCentral line.",
                             "nodes": nodes, "edges": edges})
            r.raise_for_status()

            # ...but bind the number to a freshly minted NUMBERED version.
            rv = c.post(f"{BASE}/v1/pathway/{pid}/version",
                        json={"name": "single-brand transfer", "nodes": nodes, "edges": edges})
            rv.raise_for_status()
            ver = rv.json().get("data", {}).get("version_number")

            ri = c.post(f"{BASE}/v1/inbound/{number}",
                        json={"pathway_id": pid, "pathway_version": ver,
                              "voice": IVR_VOICE, "record": True})
            ri.raise_for_status()
            print(f"[{brand}] {number} -> {rc}  (pathway {pid} v{ver})")

    print(f"\nDone. Each number now transfers callbacks straight to its brand's "
          f"RingCentral line.\nShared IVR ({SHARED_IVR_PATHWAY_ID}) is detached from "
          f"+18186167276 (left in library, unbound).")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
