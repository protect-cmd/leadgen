"""Set up the shared-number inbound IVR that routes callbacks to the right brand.

All four businesses dial out from ONE shared Bland number (+18186167276). When a
lead calls that number back (instead of the RingCentral number the voicemail told
them), this IVR answers, asks which brand/situation, and transfers to that
business's RingCentral line. A single Bland number can hold only one inbound
pathway, so this one conversational router fans out to all four.

Bland field quirks learned the hard way (don't "fix" these):
  - Edge routing condition MUST live in edge["data"]["label"]. Bland silently
    drops a top-level edge "label" and drops edge["data"]["description"], so the
    full condition sentence has to be the data.label itself.
  - The publish/promote endpoint 404s via API, so the inbound number is pointed at
    the STAGING version number explicitly. Publish in the UI to get a production
    version, then bump pathway_version here.
  - Calls need a browser User-Agent or Cloudflare returns 1010.

Run: railway run --service leadgen python scripts/setup_inbound_ivr.py
Env: BLAND_API_KEY (required). INBOUND_IVR_PATHWAY_ID (optional — update in place
     instead of creating a new pathway).
"""
from __future__ import annotations

import os
import sys

import httpx
from dotenv import load_dotenv

load_dotenv()

BASE = "https://api.bland.ai"
SHARED_NUMBER = "+18186167276"   # the one Bland outbound number, all 4 brands
IVR_VOICE = "june"               # warm receptionist preset

# Business -> RingCentral human line the voicemail tells leads to call.
RC = {
    "vantage": "+18882141711",
    "ists":    "+18883224034",
    "cosner":  "+18883382915",
    "garnish": "+18882242863",
}

GREETING = (
    "Thanks for calling back, and sorry we missed you. So I can connect you with the right "
    "team, could you tell me the name of the company that left you a message - was it Vantage, "
    "I.S.T.S., Cosner Drake, or Garnish Proof? If you're not sure of the name, just tell me "
    "what the call was about."
)

NODES = [
    {"id": "1", "type": "Default",
     "data": {"name": "Greet & Route", "text": GREETING, "isStart": True,
              "modelOptions": {"temperature": 0.3, "interruptionThreshold": 350}}},
    {"id": "v", "type": "Transfer Call",
     "data": {"name": "Transfer Vantage", "transferNumber": RC["vantage"],
              "prompt": "Say: Got it, connecting you now. One moment."}},
    {"id": "i", "type": "Transfer Call",
     "data": {"name": "Transfer ISTS", "transferNumber": RC["ists"],
              "prompt": "Say: Got it, connecting you now. One moment."}},
    {"id": "c", "type": "Transfer Call",
     "data": {"name": "Transfer Cosner Drake", "transferNumber": RC["cosner"],
              "prompt": "Say: Got it, connecting you now. One moment."}},
    {"id": "g", "type": "Transfer Call",
     "data": {"name": "Transfer Garnish Proof", "transferNumber": RC["garnish"],
              "prompt": "Say: Got it, connecting you now. One moment."}},
]


def _edge(eid: str, target: str, label: str) -> dict:
    return {"id": eid, "source": "1", "target": target,
            "data": {"label": label, "name": label}}


EDGES = [
    _edge("e_v", "v", "Caller says Vantage, OR says they were recently served with an eviction "
          "case or eviction lawsuit that was just filed against them (no judgment yet)."),
    _edge("e_i", "i", "Caller says ISTS or I.S.T.S., OR mentions an eviction judgment, a writ of "
          "possession, a set-out, or a court telling them they have to move out."),
    _edge("e_c", "c", "Caller says Cosner Drake, OR mentions being sued or served over a debt, a "
          "credit card, or a debt-collection lawsuit, with no judgment entered yet."),
    _edge("e_g", "g", "Caller says Garnish Proof, OR mentions a default judgment, a wage or bank "
          "garnishment, or their bank account being frozen or seized."),
]


def _headers() -> dict:
    key = os.environ.get("BLAND_API_KEY", "")
    if not key:
        sys.exit("BLAND_API_KEY not set")
    return {"authorization": key, "Content-Type": "application/json",
            "User-Agent": ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                           "(KHTML, like Gecko) Chrome/124.0 Safari/537.36")}


def main() -> int:
    h = _headers()
    name = "Shared Inbound IVR - Callback Router"
    pid = os.environ.get("INBOUND_IVR_PATHWAY_ID", "").strip()
    with httpx.Client(timeout=30, headers=h) as c:
        if not pid:
            r = c.post(f"{BASE}/v1/pathway/create",
                       json={"name": name, "description": "Callback router to RingCentral lines."})
            r.raise_for_status()
            pid = r.json().get("pathway_id") or r.json().get("data", {}).get("pathway_id")
            print("created pathway:", pid)

        r = c.post(f"{BASE}/v1/pathway/{pid}",
                   json={"name": name, "description": "Callback router to RingCentral lines.",
                         "nodes": NODES, "edges": EDGES})
        r.raise_for_status()
        print("populated nodes/edges:", r.status_code)

        vers = c.get(f"{BASE}/v1/pathway/{pid}/versions").json()
        staging = [v for v in vers if v.get("is_staging")]
        ver = (staging[0] if staging else max(vers, key=lambda v: v["version_number"]))["version_number"]

        ri = c.post(f"{BASE}/v1/inbound/{SHARED_NUMBER}",
                    json={"pathway_id": pid, "pathway_version": ver,
                          "voice": IVR_VOICE, "record": True})
        ri.raise_for_status()
        print(f"assigned {SHARED_NUMBER} -> pathway {pid} v{ver}")
    print("\nDone. Publish the pathway in the Bland UI to create a production version.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
