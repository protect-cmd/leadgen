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

# Callers rarely remember our brand names - they know their SITUATION ("I'm being
# evicted", "I got sued", "they're garnishing my check"). So the greeting leads with
# situation, treats the brand name as a bonus shortcut, and the router resolves the
# filed-vs-judgment split with one short follow-up question instead of demanding the
# caller know which of four brands applies.
GREETING = (
    "Thanks for calling back, and sorry we missed you. I'll get you to the right team in just "
    "a moment - can you tell me a little about what's going on? For example, are you dealing "
    "with an eviction, or with a debt or a lawsuit? And if you happen to remember the name of "
    "the company that called - Vantage, I.S.T.S., Cosner Drake, or Garnish Proof - that helps "
    "too, but no worries if not."
)

# Two clarifier prompts: only asked when the caller gives a situation word that is
# ambiguous on the one axis that actually decides the brand - whether a judgment (and,
# for debt, a garnishment) has happened yet.
CLARIFY_EVICTION = (
    "Okay, this is about an eviction. Just so I send you to the right team - has a judge "
    "already ruled against you, or have you gotten a writ, a set-out, or a move-out or lockout "
    "date? Or is it still early - you were served or got a notice, but no judgment yet?"
)
CLARIFY_DEBT = (
    "Okay, this is about a debt. One quick thing so I route you correctly - has a judgment "
    "already been entered against you, or are your wages, paycheck, or bank account being "
    "garnished or frozen? Or were you just recently sued or served, with no judgment yet?"
)

_MODEL_OPTS = {"temperature": 0.3, "interruptionThreshold": 350}

NODES = [
    {"id": "1", "type": "Default",
     "data": {"name": "Greet & Route", "text": GREETING, "isStart": True,
              "modelOptions": _MODEL_OPTS}},
    {"id": "ce", "type": "Default",
     "data": {"name": "Clarify Eviction", "text": CLARIFY_EVICTION,
              "modelOptions": _MODEL_OPTS}},
    {"id": "cd", "type": "Default",
     "data": {"name": "Clarify Debt", "text": CLARIFY_DEBT,
              "modelOptions": _MODEL_OPTS}},
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


def _edge(eid: str, source: str, target: str, label: str) -> dict:
    # Bland only persists the routing condition in data.label (top-level label and
    # data.description are silently dropped), so the full sentence lives there.
    return {"id": eid, "source": source, "target": target,
            "data": {"label": label, "name": label}}


EDGES = [
    # --- Brand-name shortcuts: if the caller actually names the company, go straight there.
    _edge("e_v_name", "1", "v", "Caller clearly names Vantage, or Vantage Defense Group, as "
          "the company that contacted them."),
    _edge("e_i_name", "1", "i", "Caller clearly names ISTS or I.S.T.S. as the company that "
          "contacted them."),
    _edge("e_c_name", "1", "c", "Caller clearly names Cosner Drake as the company that "
          "contacted them."),
    _edge("e_g_name", "1", "g", "Caller clearly names Garnish Proof as the company that "
          "contacted them."),
    # --- Situation catch-alls: route by what the caller is going through, then disambiguate.
    _edge("e_evict", "1", "ce", "Caller's situation involves an eviction, a landlord, an "
          "apartment or rental, being kicked out or removed from their home, or being told "
          "to move out - and they have NOT named one of the four companies."),
    _edge("e_debt", "1", "cd", "Caller's situation involves a debt, a lawsuit, being sued, a "
          "collection, a credit card, a judgment, or a garnishment - and they have NOT named "
          "one of the four companies."),
    # --- Eviction split: judgment/writ -> ISTS, otherwise still-early -> Vantage.
    _edge("e_ce_i", "ce", "i", "Caller indicates a judgment was already entered, they lost in "
          "court, a judge ordered them out, or they received a writ of possession, a set-out, "
          "or a move-out or lockout date."),
    _edge("e_ce_v", "ce", "v", "Caller indicates it is still early - they were served or got "
          "an eviction notice or a court date but no judgment has been entered yet, or they "
          "are unsure how far along it is."),
    # --- Debt split: judgment/garnishment -> Garnish Proof, otherwise just-sued -> Cosner.
    _edge("e_cd_g", "cd", "g", "Caller indicates a judgment was already entered against them, "
          "or their wages, paycheck, or bank account are being garnished, frozen, or seized."),
    _edge("e_cd_c", "cd", "c", "Caller indicates they were recently sued or served over a "
          "debt but no judgment has been entered yet, or they are unsure how far along it is."),
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

        # Keep the editable draft in sync (so the UI shows the same graph)...
        r = c.post(f"{BASE}/v1/pathway/{pid}",
                   json={"name": name, "description": "Callback router to RingCentral lines.",
                         "nodes": NODES, "edges": EDGES})
        r.raise_for_status()
        print("updated draft:", r.status_code)

        # ...but inbound calls route by a NUMBERED VERSION, and the version must be
        # minted directly from these nodes/edges. Two traps learned the hard way:
        #   1. POST /v1/pathway/{id} updates only the draft; the "is_staging" flag does
        #      not move to it, so binding to is_staging serves a stale snapshot.
        #   2. Reading the draft back returns edges with empty data.label, so a version
        #      built from a draft-read loses all routing conditions. Mint the version
        #      from the in-code EDGES (which carry data.label) instead.
        rv = c.post(f"{BASE}/v1/pathway/{pid}/version",
                    json={"name": "two-step situation router", "nodes": NODES, "edges": EDGES})
        rv.raise_for_status()
        ver = rv.json().get("data", {}).get("version_number")
        print("minted version:", ver)

        ri = c.post(f"{BASE}/v1/inbound/{SHARED_NUMBER}",
                    json={"pathway_id": pid, "pathway_version": ver,
                          "voice": IVR_VOICE, "record": True})
        ri.raise_for_status()
        print(f"assigned {SHARED_NUMBER} -> pathway {pid} v{ver}")
    print("\nDone. Inbound number is bound to the freshly minted version with routing labels.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
