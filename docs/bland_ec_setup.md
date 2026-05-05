# Bland.ai — Grant Ellis Group EC Setup Guide

## Status Check Before Anything Else

- **Credits:** Account shows -0.81 credits — **top up before testing or calls will fail silently.**
- **Existing pathway:** "Grant Ellis Group" pathway already exists in Bland — ID starts with `efedc52f`. Use this, don't create a new one.
- **Phone number:** None purchased yet — see Step 3.

---

## What We're Building

A voicemail-drop pipeline. When our code runs, it triggers an outbound call to the landlord's cell. If answered, the agent reads the script live. If voicemail, it leaves the message automatically. One retry after 4 hours if no answer on the first attempt.

---

## Step 1 — Configure the Grant Ellis Group Pathway

Go to: **app.bland.ai/dashboard/convo-pathways** → click **Grant Ellis Group**

### Pathway structure (very simple — this is a voicemail drop, not a conversation)

Build two nodes:

**Node 1 — Intro / Message (Start Node)**
- Type: `Default`
- Prompt (set as static text, not AI-generated):
```
Hi, this message is for {{first_name}}. My name is Alex calling from Grant Ellis Group.
We noticed a recent unlawful detainer filing in {{county}} County associated with your
property at {{property_address}}. We specialize in preparing county-specific eviction
documents — notices, UD packages, and serving instructions — delivered in 24 hours
starting at $297. If you still need documents for your case, call us back at
{{ec_phone}}. Again that number is {{ec_phone}}.
Have a great day.
```
- Set this node as the **Start Node**

**Node 2 — End Call**
- Type: `End Call`
- Connect Node 1 → Node 2 (edge label: anything like "message delivered")

### Global Pathway Settings
- **Voice:** Choose a professional male voice (e.g., `mason` or `derek` — preview in Voices tab)
- **Global Prompt:** "You are Alex, a professional representative from Grant Ellis Group. Speak clearly and professionally. If someone answers and interrupts, pause politely, then continue the message."

### Get the Full Pathway ID
- In the pathway list, click the copy icon next to "Grant Ellis Group"
- Full ID goes into `.env` as `BLAND_EC_AGENT_ID`

### Easiest Alternative — Use the AI Builder
Instead of manually building nodes, click **"+ Create Pathway"** → paste this prompt:

```
Build an outbound voicemail-drop pathway for a legal document company called Grant Ellis Group.
The agent's name is Alex. When the call is answered (live or voicemail), deliver this exact script:

"Hi, this message is for {{first_name}}. My name is Alex calling from Grant Ellis Group.
We noticed a recent unlawful detainer filing in {{county}} County associated with your
property at {{property_address}}. We specialize in preparing county-specific eviction
documents — notices, UD packages, and serving instructions — delivered in 24 hours
starting at $297. If you still need documents for your case, call us back at {{ec_phone}}.
Again that number is {{ec_phone}}. Have a great day."

After the message, end the call. If interrupted politely by a live person, acknowledge briefly 
and offer to let them call back, then end the call. Do not engage in extended conversation.
```

This generates the pathway automatically. Then copy its ID for `.env`.

---

## Step 2 — Set the Voicemail Message (in our API call, not Bland UI)

Our code sends the voicemail message directly in the API payload — no separate Bland config needed. The message is already wired in `bland_service.py` using the same script variables.

---

## Step 3 — Buy an Outbound Phone Number

Go to: **app.bland.ai/dashboard/phone-numbers** → **Buy Number**

- Pick an area code that matches your target market (LA landlords → try 213, 310, 818, 323)
- Cost: **$15/month**
- After purchase, copy the number (e.g., `+12135550000`)
- Add to `.env` as `BLAND_EC_PHONE_NUMBER`

> **Tip:** Consider "Local Dialing" add-on (app.bland.ai/dashboard/add-ons) — Bland auto-picks a number matching the callee's area code, which improves answer rates. If you enable this, set `BLAND_EC_PHONE_NUMBER=local` and we'll update the code.

---

## Step 4 — Fill in .env

```
BLAND_API_KEY=          # Settings → API Keys in Bland dashboard
BLAND_EC_AGENT_ID=      # The full pathway ID copied from Step 1 (efedc52f...)
BLAND_EC_PHONE_NUMBER=  # The +1... number purchased in Step 3
```

---

## Step 5 — Test with a Single Call

Once the above is filled in, run this from the project root to fire a test call:

```bash
python - <<'EOF'
import asyncio, os
from dotenv import load_dotenv
load_dotenv()
from datetime import date
from models.filing import Filing
from models.contact import EnrichedContact
from services import bland_service

filing = Filing(
    case_number="TEST-001",
    tenant_name="Test Tenant",
    landlord_name="Your Name",          # <- change to your name
    property_address="123 Main St, Los Angeles, CA 90001",
    filing_date=date.today(),
    state="CA",
    county="Los Angeles",
    notice_type="UD",
    source_url="http://test.com",
)
contact = EnrichedContact(
    filing=filing,
    track="ec",
    phone="+1YOUR_CELL_HERE",           # <- change to your cell
    property_type="residential",
    estimated_rent=2500,
)

async def main():
    call_id = await bland_service.trigger_voicemail(contact)
    print("Call dispatched:", call_id)

asyncio.run(main())
EOF
```

---

## Variable Reference

These are the `request_data` keys our code sends. Reference them in Bland as `{{variable_name}}`:

| Variable | Value | Example |
|---|---|---|
| `first_name` | Landlord's first name | `Jane` |
| `county` | Filing county | `Los Angeles` |
| `property_address` | Property street address | `123 Main St, LA, CA 90001` |
| `ec_phone` | EC business phone number | `+12135550000` |

---

## What Happens on a Live Call vs Voicemail

| Scenario | What Bland does |
|---|---|
| Live answer | Pathway runs — Alex delivers the script live |
| Voicemail / answering machine | Leaves the message automatically after the beep |
| No answer (rings out) | Retries once after 4 hours, then stops |
| DNC number | Call blocked — 400 error returned, we log and skip |

---

## Remaining NG Setup (deferred)

NG (Vantage Defense Group) needs its own:
- Separate Bland.ai workspace or separate pathway ("NG-Tenant-Outreach")
- Warm empathetic female voice
- Different script (tenant-facing)
- Its own phone number
- `BLAND_NG_AGENT_ID` and `BLAND_NG_PHONE_NUMBER` in `.env`

Not started until the NG GHL subaccount is created.
