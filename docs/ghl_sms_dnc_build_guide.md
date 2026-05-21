# GHL SMS and DNC Build Guide

## Brand Rule

Use Grant Ellis Group for landlord-facing outreach and Vantage Defense Group for tenant-facing outreach.

## Current Local Status

| Item | Status |
|---|---|
| Grant Ellis Group Bland voicemail copy in code | Done |
| Vantage Defense Group Bland voicemail copy in code | Done |
| Vantage Defense Group Spanish Bland voicemail copy in code | Done |
| Spanish-Likely tenant surname detection in code | Done |
| Spanish-Likely dashboard views | Done |
| DNC metadata stored from BatchData selected phone | Done |
| Auto Bland gate blocks non-clear DNC status | Done |
| Dashboard approval blocks non-clear DNC status | Done |
| GHL English SMS workflows built in HighLevel | Not Done - use AI Builder prompts in this doc |
| GHL Spanish SMS workflow built in HighLevel | Not Done - use AI Builder prompts in this doc |
| A2P registration approved | Done - approved 2026-05-20, SMS live |
| Instantly.ai sequence connected | Not Done - no Instantly credentials/API in this repo |

## GHL Workflow Gate

No GHL SMS or Bland-trigger workflow should fire unless all conditions are true:

- Contact has the correct brand tag: `EC-New-Filing` for Grant Ellis Group, `NG-New-Filing` for Vantage Defense Group.
- Spanish-language VDG contacts also have the `Spanish-Likely` tag.
- Contact is not tagged `Below-Threshold`.
- Contact has a usable phone number.
- `DNC Cleared` is checked.
- Contact is not opted out.
- Sending window is 8:00 AM to 7:00 PM in the contact's local time.

If DNC is not clear, route to manual review and do not call or text.

## Grant Ellis Group SMS Sequence

Trigger: 2 hours after voicemail dropped, only after DNC Cleared is true.

SMS 1 - Day 1:

```text
Hi {{contact.first_name}}, this is Alex from Grant Ellis Group. We left you a voicemail about your {{contact.filing_county}} County eviction filing. We prepare county-specific eviction docs in 24hrs from $297. Ready to move forward? Reply YES or call {{custom_values.geg_phone}}. Reply STOP to opt out.
```

SMS 2 - Day 3, if no response:

```text
Hi {{contact.first_name}} - Grant Ellis Group here. Still need eviction documents for your {{contact.filing_county}} property? We handle 3-day notices, 30/60-day notices, and full UD packages. 24hr turnaround. Reply YES to get started or STOP to opt out.
```

SMS 3 - Day 7, final:

```text
Last message from Grant Ellis Group. If you still need county-specific eviction documents, we are here. Starting at $297, delivered in 24 hours. Reply YES to talk with us or STOP to opt out.
```

## Vantage Defense Group SMS Sequence — English

Trigger: tag `NG-New-Filing` added AND `DNC Cleared` is true AND contact does NOT have tag `Spanish-Likely`. Send at 11 AM contact local time (2 hours after voicemail window opens at 9 AM).

SMS 1 — Day 1, 11 AM:

```text
Hi {{contact.first_name}} — Vantage Defense Group here. We just called about your home. You may have received legal papers and you have rights. We can help protect you and keep you in your home for months longer. Free consultation — call {{custom_values.vdg_phone}} now or reply YES to schedule. Reply STOP to opt out.
```

SMS 2 — Day 2, 9 AM (if no response):

```text
Hi {{contact.first_name}} — Vantage Defense Group. You have a deadline to respond to your legal papers. Missing it means automatic judgment against you. We can help. Free call today at {{custom_values.vdg_phone}} — no obligation. Reply STOP to opt out.
```

SMS 3 — Day 3, 9 AM (FINAL):

```text
FINAL MESSAGE from Vantage Defense Group. Your window to respond to your legal papers is closing fast. Once it passes you lose your chance to fight back. Call us now at {{custom_values.vdg_phone}}. Free consultation. We are on your side. Reply STOP to opt out.
```

### GHL AI Builder Prompt — VDG English SMS

Go to: **Automation → Workflows → Build using AI** (or open a new workflow and use the AI prompt box).

Paste this prompt:

```
Create a workflow named "VDG English SMS Sequence". This workflow sends 3 SMS messages to tenants facing eviction on behalf of Vantage Defense Group.

Trigger: Contact tag is added — trigger when the tag "NG-New-Filing" is added to a contact.

Entry conditions (add as filters on the trigger):
- Contact custom field "DNC Cleared" equals true
- Contact does NOT have tag "Spanish-Likely"
- Contact is not opted out of SMS

Step 1: Wait until 11:00 AM in the contact's local timezone on the same day the trigger fires. If it is already past 11 AM, send at 11 AM the following business day.

Step 2: Send SMS — message body:
Hi {{contact.first_name}} — Vantage Defense Group here. We just called about your home. You may have received legal papers and you have rights. We can help protect you and keep you in your home for months longer. Free consultation — call {{custom_values.vdg_phone}} now or reply YES to schedule. Reply STOP to opt out.

Step 3: Add an If/Else condition. Check if the contact has replied (inbound reply received OR tag "Responded" exists). If YES, stop the workflow. If NO, continue.

Step 4: Wait 1 day.

Step 5: Wait until 9:00 AM in the contact's local timezone.

Step 6: Send SMS — message body:
Hi {{contact.first_name}} — Vantage Defense Group. You have a deadline to respond to your legal papers. Missing it means automatic judgment against you. We can help. Free call today at {{custom_values.vdg_phone}} — no obligation. Reply STOP to opt out.

Step 7: Add an If/Else condition. Check if the contact has replied (inbound reply received OR tag "Responded" exists). If YES, stop the workflow. If NO, continue.

Step 8: Wait 1 day.

Step 9: Wait until 9:00 AM in the contact's local timezone.

Step 10: Send SMS — message body:
FINAL MESSAGE from Vantage Defense Group. Your window to respond to your legal papers is closing fast. Once it passes you lose your chance to fight back. Call us now at {{custom_values.vdg_phone}}. Free consultation. We are on your side. Reply STOP to opt out.

Step 11: End workflow.

Reply handling (add as a separate trigger or note for manual setup):
- STOP reply → unsubscribe contact from SMS immediately, remove from workflow
- YES reply → create a task assigned to the VDG closer, add tag "Responded", stop workflow
- Any inbound reply → add tag "Responded", pause and stop workflow

Do not allow re-entry to this workflow if the contact already has tag "Responded" or is opted out.

Sending window: Only send SMS between 8:00 AM and 7:00 PM contact local time. Do not send outside this window.
```

**After generation — review checklist:**
- [ ] Trigger set to tag added: `NG-New-Filing`
- [ ] Filter: DNC Cleared = true
- [ ] Filter: does NOT have tag `Spanish-Likely`
- [ ] SMS sender number is the VDG dedicated line (not GEG)
- [ ] `{{custom_values.vdg_phone}}` is set in GHL Settings → Custom Values
- [ ] Time window restricted to 8 AM–7 PM local
- [ ] STOP handling unsubscribes the contact
- [ ] YES reply creates a task for the VDG closer
- [ ] Re-entry disabled if contact is already tagged `Responded`

---

## Vantage Defense Group SMS Sequence — Spanish

Trigger: contact has tag `NG-New-Filing` AND tag `Spanish-Likely` AND `DNC Cleared` is true. Send at 11 AM contact local time. Use ALTO for opt-out (not STOP). Route all SI and ALTO replies to Sofia only.

SMS 1 — Day 1, 11 AM:

```text
Hola {{contact.first_name}} — le habla Vantage Defense Group. Acabamos de llamarle sobre su hogar. Es posible que haya recibido papeles legales y usted tiene derechos. Podemos protegerle y ayudarle a quedarse en su hogar por meses. Consulta gratis — llame al {{custom_values.vdg_spanish_phone}} ahora o responda SI para programar. Responda ALTO para cancelar.
```

SMS 2 — Day 2, 9 AM (if no response):

```text
Hola {{contact.first_name}} — Vantage Defense Group. Tiene una fecha limite para responder a sus papeles legales. Si no responde a tiempo la corte puede fallar automaticamente en su contra. Podemos ayudarle. Llamenos hoy al {{custom_values.vdg_spanish_phone}} — gratis, sin obligacion. Responda ALTO para cancelar.
```

SMS 3 — Day 3, 9 AM (FINAL):

```text
ULTIMO MENSAJE de Vantage Defense Group. Su tiempo para responder a sus papeles legales se esta terminando rapidamente. Una vez que pase pierde su oportunidad de defenderse. Llamenos ahora al {{custom_values.vdg_spanish_phone}}. Consulta gratis. Estamos de su lado. Responda ALTO para cancelar.
```

### GHL AI Builder Prompt — VDG Spanish SMS

Go to: **Automation → Workflows → Build using AI**.

Paste this prompt:

```
Create a workflow named "VDG Spanish SMS Sequence". This workflow sends 3 Spanish-language SMS messages to Spanish-speaking tenants facing eviction on behalf of Vantage Defense Group.

Trigger: Contact tag is added — trigger when the tag "Spanish-Likely" is added to a contact.

Entry conditions (add as filters on the trigger):
- Contact also has tag "NG-New-Filing"
- Contact custom field "DNC Cleared" equals true
- Contact is not opted out of SMS

Step 1: Wait until 11:00 AM in the contact's local timezone on the same day the trigger fires. If it is already past 11 AM, send at 11 AM the following business day.

Step 2: Send SMS — message body (Spanish):
Hola {{contact.first_name}} — le habla Vantage Defense Group. Acabamos de llamarle sobre su hogar. Es posible que haya recibido papeles legales y usted tiene derechos. Podemos protegerle y ayudarle a quedarse en su hogar por meses. Consulta gratis — llame al {{custom_values.vdg_spanish_phone}} ahora o responda SI para programar. Responda ALTO para cancelar.

Step 3: Add an If/Else condition. Check if the contact has replied (inbound reply received OR tag "Responded" exists). If YES, stop the workflow. If NO, continue.

Step 4: Wait 1 day.

Step 5: Wait until 9:00 AM in the contact's local timezone.

Step 6: Send SMS — message body (Spanish):
Hola {{contact.first_name}} — Vantage Defense Group. Tiene una fecha limite para responder a sus papeles legales. Si no responde a tiempo la corte puede fallar automaticamente en su contra. Podemos ayudarle. Llamenos hoy al {{custom_values.vdg_spanish_phone}} — gratis, sin obligacion. Responda ALTO para cancelar.

Step 7: Add an If/Else condition. Check if the contact has replied (inbound reply received OR tag "Responded" exists). If YES, stop the workflow. If NO, continue.

Step 8: Wait 1 day.

Step 9: Wait until 9:00 AM in the contact's local timezone.

Step 10: Send SMS — message body (Spanish):
ULTIMO MENSAJE de Vantage Defense Group. Su tiempo para responder a sus papeles legales se esta terminando rapidamente. Una vez que pase pierde su oportunidad de defenderse. Llamenos ahora al {{custom_values.vdg_spanish_phone}}. Consulta gratis. Estamos de su lado. Responda ALTO para cancelar.

Step 11: End workflow.

Reply handling (add as a separate trigger or note for manual setup):
- ALTO reply → unsubscribe contact from SMS immediately, remove from workflow, notify Sofia
- SI reply → create a task assigned to Sofia, add tag "Responded", stop workflow and notify Sofia immediately
- Any inbound reply → add tag "Responded", pause and stop workflow, route to Sofia

Important: This is a fully Spanish-language workflow for tenants tagged Spanish-Likely. Do not use STOP as the opt-out keyword — use ALTO. All replies route to Sofia only, not the general closer queue.

Do not allow re-entry if the contact already has tag "Responded" or is opted out.

Sending window: Only send SMS between 8:00 AM and 7:00 PM contact local time.
```

**After generation — review checklist:**
- [ ] Trigger set to tag added: `Spanish-Likely`
- [ ] Filter: also has tag `NG-New-Filing`
- [ ] Filter: DNC Cleared = true
- [ ] SMS sender number is the VDG dedicated line (not GEG, not VDG English line)
- [ ] `{{custom_values.vdg_spanish_phone}}` is set in GHL Settings → Custom Values
- [ ] Opt-out keyword is ALTO (not STOP) — verify in GHL compliance settings
- [ ] SI reply creates a task assigned to Sofia only
- [ ] ALTO reply unsubscribes and notifies Sofia
- [ ] Re-entry disabled if contact is already tagged `Responded`
- [ ] Sending window restricted to 8 AM–7 PM local

## Required Workflow Rules

- STOP reply: unsubscribe contact immediately (English workflows).
- ALTO reply: unsubscribe Spanish-language contacts immediately; also notify Sofia.
- YES reply (English): create a task for the VDG English closer; add tag `Responded`; stop workflow.
- SI reply (Spanish): create a task assigned to Sofia only; add tag `Responded`; stop workflow; notify Sofia immediately.
- Any inbound reply: add tag `Responded`; pause and stop the automated sequence.
- Contacts tagged `Spanish-Likely` route to Sofia only — never the general closer queue.
- After-hours reply: send the approved after-hours auto-reply and create an 8 AM next-day task.
- Never share sender numbers between Grant Ellis Group and Vantage Defense Group.
- Never share sender numbers between VDG English and VDG Spanish workflows.

## Sunshine Manual DNC Protocol

Until a scrub vendor is automated:

1. Export all new contacts tagged `EC-New-Filing` or `NG-New-Filing`.
2. Scrub phone numbers with the approved DNC service.
3. Remove any DNC hits from outbound batches.
4. Mark `DNC Cleared` only for cleared contacts in GHL.
5. Keep daily volume between 300 and 400 total calls across both brands.
6. Send the daily 9 AM summary: contacts added, voicemails dropped, SMS sent, replies received, consultations booked.

## A2P Setup Notes

Use the current brand names in every A2P field, sample message, opt-in page, privacy policy, and terms page. Do not submit sample copy with old brand names.

The FTC DNC Reported Calls Data API is a complaint-monitoring API, not a lead scrub API. Use it only to monitor whether outbound caller IDs appear in complaint data. For pre-call DNC scrubbing, use official telemarketing registry access or a scrub vendor.
