# Bland.ai Setup Guide — All Agents (GEG + VDG)

**Last updated: 2026-05-09. Scripts sourced from Zee Complete Scripts V2.**

---

## How Our Integration Works

Our Python service (`services/bland_service.py`) calls `POST /v1/calls` for each lead. The Bland pathway controls live-answer behavior; the `voicemail` payload handles answering machines automatically. Variables are injected via `request_data` and referenced in pathway nodes as `{{variable_name}}`.

**Flow per call:**
```
Pipeline → DNC gate → bland_service.trigger_voicemail() → Bland API
    → Live answer: pathway runs, agent speaks script
    → Voicemail/machine: leave_message fires automatically after beep
    → No answer (rings out): retry once after 4 hours
```

---

## Variable Reference (what our code sends)

These are injected in every call via `request_data`. Use `{{variable_name}}` syntax in Bland pathway nodes and prompts.

| Variable | EC (Grant) | NG (Vantage) |
|---|---|---|
| `{{first_name}}` | Landlord first name | Tenant first name |
| `{{county}}` | Filing county | — (not used in NG script) |
| `{{property_address}}` | Property street address | Sent but not in NG script |
| `{{ec_phone}}` | GEG callback number | — |
| `{{ng_phone}}` | — | VDG callback number |
| `{{language_hint}}` | — | `"spanish_likely"` or `""` |

---

## Agent 1 — Grant Ellis Group Outbound (EC)

**Railway var:** `BLAND_EC_AGENT_ID` — currently set to `5b217638-3ee7-4cee-9e9c-f9e40a388ffc`

**Status:** Pathway exists in Bland. Verify script matches V2 below.

### Pathway Configuration

**Go to:** app.bland.ai → Conversational Pathways → Grant Ellis Group

**Voice:** Professional male, neutral American accent. Try `mason` or `derek` in the Voices tab. Preview first.

**Global Prompt** (add to all nodes):
```
You are Alex, a professional representative from Grant Ellis Group. Speak clearly and
professionally at a moderate pace. If someone answers and interrupts, pause politely,
then continue or offer to let them call back. Do not engage in extended conversation.
End the call after the message.
```

**Node 1 — Message (Start Node)**
- Type: `Default`
- Toggle: **Static Text ON** (not AI-generated — we want word-for-word delivery)
- Text:
```
Hi, this message is for {{first_name}}. This is Alex calling from Grant Ellis Group.
We noticed a recent filing in {{county}} County associated with your property at
{{property_address}}. If you need county-specific eviction documents prepared —
notices, UD packages, or serving instructions — we deliver them in 24 hours starting
at $297. Attorney reviewed and county specific. Call us back at {{ec_phone}} or visit
grantellisgroup.com. That number again is {{ec_phone}}. Have a great day.
```

**Node 2 — End Call**
- Type: `End Call`
- Connect Node 1 → Node 2. Edge label: `"message complete"`

**Voicemail message** (sent by our code, not configured in Bland UI):
Same script as Node 1 — our code renders it from `bland_service.py` and sends it in the `voicemail.message` payload field.

---

## Agent 2 — Vantage Defense Group English Outbound (NG)

**Railway var:** `BLAND_NG_AGENT_ID` — **currently empty, must be created**

**Status:** Pathway does not exist yet. Create it.

### Create the Pathway

Go to: app.bland.ai → Conversational Pathways → **+ Create Pathway**

Name it: `Vantage Defense Group English Outbound`

**Voice:** Warm, empathetic female. Neutral American accent. Try `Sarah` or `Emily`. Preview to confirm warmth — not robotic.

**Global Prompt** (add to all nodes):
```
You are a compassionate representative from Vantage Defense Group calling tenants who
may be facing eviction. Speak warmly and calmly. These people are stressed. If they
engage, listen briefly, reassure them help is available, and give the callback number.
Do not make legal promises. Do not discuss case details. End politely.
```

**Node 1 — Message (Start Node)**
- Type: `Default`
- Toggle: **Static Text ON**
- Text:
```
Hi, this message is for {{first_name}}. This is an important call from Vantage Defense
Group. You may have recently received legal papers about your home. Do not ignore them —
you have rights and you have options. We are here to help protect you and keep you in
your home. Call us today at {{ng_phone}} for a free consultation. Someone is standing by
right now to help you. If you prefer to continue in Spanish — hola {{first_name}}, le
llama Vantage Defense Group. Usted tiene derechos. Estamos aqui para protegerle y
ayudarle a quedarse en su hogar. Llamenos al {{ng_phone}}. La consulta es gratis y
estamos aqui para usted ahora mismo. Again that number is {{ng_phone}}. We are on your
side. Call us now.
```

**Node 2 — End Call**
- Type: `End Call`
- Connect Node 1 → Node 2. Edge label: `"message complete"`

**After creating:**
1. Copy the full pathway ID from the list
2. Set in Railway: `BLAND_NG_AGENT_ID=<paste-id>`

---

## Agent 3 — Vantage Defense Group Spanish Outbound (NG Spanish)

**Railway var:** `BLAND_NG_SPANISH_AGENT_ID` — **currently missing, must be created**

**Trigger condition in our code:** `language_hint == "spanish_likely"`

### Create the Pathway

Name it: `Vantage Defense Group Spanish Outbound`

**Voice:** Warm, empathetic female. Native Spanish speaker. Neutral Latin American accent — NOT robotic. Try `Isabella` or any Spanish-language voice in the Voices tab. Preview it with Spanish text.

**Global Prompt:**
```
Eres una representante compasiva de Vantage Defense Group que llama a inquilinos que
pueden estar enfrentando un desalojo. Habla con calidez y calma. Estas personas estan
estresadas. Si interactuan, escuchalos brevemente, transquilizalos y da el numero de
contacto. No hagas promesas legales. Termina amablemente.
```

**Node 1 — Message (Start Node)**
- Type: `Default`
- Toggle: **Static Text ON**
- Text:
```
Hola, este mensaje es para {{first_name}}. Le llama Vantage Defense Group. Es posible
que usted haya recibido papeles legales sobre su hogar. No los ignore — usted tiene
derechos y tiene opciones. Estamos aqui para protegerle y ayudarle a quedarse en su
hogar. Llamenos hoy al {{ng_phone}} para una consulta gratuita. Alguien esta disponible
ahora mismo para ayudarle. Ese numero es {{ng_phone}}. Estamos de su lado. Llamenos ahora.
```

**Node 2 — End Call**
- Type: `End Call`
- Connect Node 1 → Node 2. Edge label: `"mensaje completo"`

**After creating:**
1. Copy the full pathway ID
2. Set in Railway: `BLAND_NG_SPANISH_AGENT_ID=<paste-id>`

---

## Phone Numbers Still Needed in Railway

| Variable | Status | Action |
|---|---|---|
| `BLAND_EC_PHONE_NUMBER` | ✅ Set (`+18186167276`) | Verify this is the GEG outbound number |
| `BLAND_EC_CALLBACK_PHONE_NUMBER` | ❌ Missing | Add the GEG number tenants call back to |
| `BLAND_NG_PHONE_NUMBER` | ❌ Empty | Buy NG number in Bland → set here |
| `BLAND_NG_CALLBACK_PHONE_NUMBER` | ❌ Missing | Same as NG phone or a different line |
| `BLAND_NG_SPANISH_PHONE_NUMBER` | ❌ Missing | Buy Spanish NG number → set here |
| `BLAND_NG_SPANISH_CALLBACK_PHONE_NUMBER` | ❌ Missing | Same as Spanish phone or different line |

**To buy numbers:** app.bland.ai → Phone Numbers → Buy Number
- GEG: pick area code matching target market (LA: 213, 310, 818; Dallas: 214, 972)
- VDG English: same area code strategy
- VDG Spanish: same — Bland handles language via agent, not number
- Cost: $15/month per number

**Local Presence add-on** (recommended): app.bland.ai → Add-ons → Local Dialing. Bland auto-matches outbound number to callee's area code. Improves answer rates significantly.

---

## Enabling/Disabling Calls (Railway vars)

| Variable | Current | Required for QA | Required for live |
|---|---|---|---|
| `BLAND_ENABLED` | `false` | `true` | `true` |
| `BLAND_TEST_CALLS_ENABLED` | not set | `true` | `false` |
| `AUTO_BLAND_CALLS_ENABLED` | not set | `false` | Zee/Chris decision |

Set `BLAND_TEST_CALLS_ENABLED=true` and `BLAND_ENABLED=true` before QA test calls via the dashboard.

**Never** set `AUTO_BLAND_CALLS_ENABLED=true` without explicit Chris/Zee approval.

---

## QA Test Calls (Internal Numbers Only)

Use the dashboard QA buttons (after deploying the dashboard branch) or run locally:

```powershell
# From project root — change phone to your internal test number
python -c "
import asyncio, os
from dotenv import load_dotenv
load_dotenv()
from datetime import date
from models.filing import Filing
from models.contact import EnrichedContact
from services import bland_service

filing = Filing(
    case_number='QA-GEG-001',
    tenant_name='QA Tenant',
    landlord_name='Your Name',
    property_address='123 Main St, Los Angeles, CA 90001',
    filing_date=date.today(),
    state='CA',
    county='Los Angeles',
    notice_type='UD',
    source_url='http://test.com',
)
contact = EnrichedContact(
    filing=filing,
    track='ec',           # change to 'ng' for Vantage test
    phone='+1YOURCELL',   # internal test number only
    property_type='residential',
    dnc_status='clear',
)
asyncio.run(bland_service.trigger_voicemail(contact))
"
```

**QA acceptance checklist per agent:**
- [ ] Call connects (not error)
- [ ] Correct brand name spoken
- [ ] Correct callback number spoken
- [ ] Correct language (English/Spanish)
- [ ] Call ends cleanly after message
- [ ] `call_id` returned in logs
- [ ] No Supabase production row created
- [ ] No GHL contact created

---

## Max Attempts Per Lead

Our code sends `retry.wait = 14400` (4 hours). Bland retries once if first attempt goes to voicemail. The plan calls for max 2 attempts (initial at 9 AM + retry at 4 PM same day for VDG, or initial + 1 retry for GEG). This is handled by our `retry` payload, not the Bland UI.

---

## Callback Phone Numbers (not yet wired in code)

The `BLAND_EC_CALLBACK_PHONE_NUMBER` and `BLAND_NG_CALLBACK_PHONE_NUMBER` vars are reserved for a dedicated inbound line separate from the outbound calling number, if that's the setup. If the outbound number IS the callback number (same line), just set both to the same value.

Code currently uses the outbound number as the callback number in the script — update these Railway vars once numbers are confirmed.
