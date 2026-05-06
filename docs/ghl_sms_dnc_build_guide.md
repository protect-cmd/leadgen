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
| GHL English SMS workflows built in HighLevel | Not Done - requires GHL UI/plugin access |
| GHL Spanish SMS workflow built in HighLevel | Not Done - HighLevel connector returned 401 in Codex |
| A2P registration submitted | Not Done - requires GHL Trust Center |
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

## Vantage Defense Group SMS Sequence

Trigger: 2 hours after voicemail dropped, only after DNC Cleared is true.

SMS 1 - Day 1:

```text
Hi {{contact.first_name}}, Vantage Defense Group here. We called about your eviction notice at {{contact.property_address}}. We may be able to help you stay in your home longer. Free consultation: call {{custom_values.vdg_phone}} or reply YES to schedule. Reply STOP to opt out.
```

SMS 2 - Day 3, if no response:

```text
Hi {{contact.first_name}} - you may have a deadline to respond to your eviction filing. Missing it can lead to judgment against you. Vantage Defense Group can help. Call {{custom_values.vdg_phone}} or reply YES. Reply STOP to opt out.
```

SMS 3 - Day 5, final:

```text
Final message from Vantage Defense Group. Your eviction response window may be closing. Call us now at {{custom_values.vdg_phone}} for a free consultation, no obligation. Reply STOP to opt out.
```

## Vantage Defense Group Spanish SMS Sequence

Trigger: 2 hours after Spanish voicemail drop, only when all are true: contact is tagged `NG-New-Filing`, contact is tagged `Spanish-Likely`, and DNC Cleared is true.

SMS 1 - Day 1:

```text
Hola {{contact.first_name}}, le habla Vantage Defense Group. Le dejamos un mensaje sobre los papeles legales de su hogar en {{contact.property_address}}. Podemos ayudarle a quedarse en su hogar hasta 4 o 5 meses. Consulta gratis hoy - llame al {{custom_values.vdg_spanish_phone}} o responda SI para programar. Responda ALTO o STOP para no recibir mas mensajes.
```

SMS 2 - Day 3, if no response:

```text
Hola {{contact.first_name}} - Vantage Defense Group. Tiene una fecha limite para responder a su caso legal. Si no responde a tiempo la corte puede fallar automaticamente en su contra. Llamenos hoy al {{custom_values.vdg_spanish_phone}} - consulta gratis, sin obligacion. Responda ALTO o STOP para cancelar.
```

SMS 3 - Day 5, final:

```text
ULTIMO MENSAJE - Vantage Defense Group. Su tiempo para responder al caso legal de su hogar se esta terminando. Una vez que pase no podra presentarse en la corte. Llamenos ahora al {{custom_values.vdg_spanish_phone}} - consulta gratis. Responda ALTO o STOP para cancelar.
```

## Required Workflow Rules

- STOP reply: unsubscribe contact immediately.
- ALTO reply: unsubscribe Spanish-language contacts immediately.
- YES reply: create a task and notification for the correct closer.
- SI reply: pause Spanish automation and notify Sofia immediately.
- Any reply: pause the automated sequence and move contact to `Responded`.
- Contacts tagged `Spanish-Likely` route to Sofia only.
- After-hours reply: send the approved after-hours auto-reply and create an 8 AM next-day task.
- Never share sender numbers between Grant Ellis Group and Vantage Defense Group.

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
