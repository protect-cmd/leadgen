# Bland.ai Setup Guide — Garnish Proof (GP)

**Product:** Garnish Proof — document prep for debtors with a default judgment entered against them (bank-account seizure imminent).
**Branch:** `feat/garnish-proof-vertical`
**Last updated:** 2026-06-27

---

## State framing (read first)

The only built data source is **Harris County, Texas** debt-claim default judgments
(`scrapers/texas/harris_debt_judgments.py` → `state="TX", county="Harris"`). **Texas
legally bars wage garnishment** (bank-account seizure only), so the scripts below are
written for the TX reality: *default judgment → bank-account freeze/seizure → Motion to
Vacate + Claim of Exemption*. They deliberately do **not** use the "25% of your paycheck
in Florida" framing from the original brief, which would be false for TX leads. If a
Florida (or other wage-garnishment state) source is ever added, fork a state-specific
pathway then.

---

## Pathways (already created live via the Bland API)

| Pathway | Bland pathway ID | Persona | Railway var |
|---|---|---|---|
| `Garnish Proof English Outbound` | `b667895e-3369-45a5-8642-3db98f89322d` | Marcus (EN) | `BLAND_GP_AGENT_ID` |
| `Garnish Proof Spanish Outbound` | `b186df96-dc92-4857-a90c-8da8249188f5` | Daniel (ES) | `BLAND_GP_SPANISH_AGENT_ID` |

Each is a single-message outbound drop: one `Default` node → `End Call`, with
`skipUserResponse` + `block_interruptions` ON (agent reads and does not wait for the
callee). The message node uses the account's verbatim technique — the `prompt` field
begins with *"Deliver the following message exactly. Do not paraphrase…"* and wraps the
script in quotes — the same pattern as the working `EC Voicemail Drop` pathway, so the
legal wording is delivered word-for-word.

---

## Variables our code injects (`services/gp_bland.py` → `request_data`)

| Variable | Source |
|---|---|
| `{{first_name}}` | `debtor_name`, first token via `_split_name` |
| `{{county}}` | `garnishment_orders.county` (e.g. "Harris") |
| `{{gp_phone}}` | `BLAND_GP_CALLBACK_PHONE_NUMBER` (or the outbound number), spoken-digit formatted |

`property_address` is also sent but the GP scripts don't speak it. Language routing:
`language_hint == "spanish_likely"` → Daniel pathway, else Marcus.

---

## Scripts (as published)

### Marcus — English (`b667895e…`)
```
Hi {{first_name}}, this is Marcus calling from Garnish Proof.

We monitor public court records every single day, and we saw that a default judgment was recently entered against you in {{county}} County court.

Here's what that means right now. With a judgment in hand, a debt collector can move to freeze your bank account and pull the money straight out of it - and in Texas they can do that without warning you first.

What most people don't know is that you still have a short window to fight back. You can file a motion to vacate that default judgment, or a claim of exemption that protects your money from seizure. We prepare those documents for you - reviewed by a licensed attorney - and we can have them ready within forty-eight hours.

The consultation is completely free. Please call us back today at {{gp_phone}}.

Don't wait on this one. Once a collector acts on the judgment, it is much harder to undo. Again, that number is {{gp_phone}}.
```

### Daniel — Spanish (`b186df96…`)
```
Hola {{first_name}}, le habla Daniel de Garnish Proof.

Revisamos los registros judiciales publicos todos los dias, y vimos que recientemente se dicto un fallo en su contra en el tribunal del condado de {{county}}.

Esto es lo que significa ahora mismo. Con un fallo en su contra, una agencia de cobranza puede congelar su cuenta bancaria y sacar el dinero directamente de ella - y en Texas pueden hacerlo sin avisarle primero.

Lo que la mayoria de las personas no sabe es que usted todavia tiene un tiempo limitado para defenderse. Puede presentar una mocion para anular ese fallo, o una reclamacion de exencion que protege su dinero de ser embargado. Nosotros preparamos esos documentos - revisados por un abogado autorizado - y podemos tenerlos listos en cuarenta y ocho horas.

La consulta es completamente gratuita. Por favor llamenos hoy al {{gp_phone}}.

No espere con esto. Una vez que la agencia actua sobre el fallo, es mucho mas dificil revertirlo. De nuevo, ese numero es {{gp_phone}}.
```

---

## Voice and call timing (fixed 2026-06-27)

The pathways defaulted to a **female** voice and started speaking on connect (so the intro
was half-gone before the callee got the phone to their ear). Both are fixed at two layers:

- **Pathway (node `modelOptions`)** — set via API on the start node of each pathway:
  - Marcus EN → `mason` (id `90295ec4-f0fe-4783-ab33-8b997ddc3ae4`), `wait_for_greeting: true`
  - Daniel ES → `Esteban` (id `60974bf8-151e-44e2-812e-4dc958aac5f3`), `wait_for_greeting: true`
- **Production call payload (`gp_bland.py`)** — sends `voice` (`BLAND_GP_VOICE` /
  `BLAND_GP_SPANISH_VOICE`, default `mason` / `Esteban`) and `wait_for_greeting: true`.
  This is authoritative for real dials.

`wait_for_greeting: true` keeps the agent silent until the callee speaks ("hello?"), then it
delivers the intro from the top.

If a **dashboard test call** still uses a female voice, the pathway's top-level Voice
selector in the UI is overriding — set it to `mason` / `Esteban` there and Publish (the
top-level voice setting can't be written via the update API; only the node `modelOptions`
voice can). Marcus alt: `matt`. Daniel alt: `Martin`.

## Global prompt (Bland UI)

Optional; behavior is already governed by `skipUserResponse`. Paste into each pathway's
Global Prompt box:
   - EN: `You are Marcus calling from Garnish Proof. Speak calmly, clearly, and with steady confidence at a moderate pace. The person may be stressed because a debt judgment was recently entered against them. Do not make legal promises. Do not give legal advice. Deliver the message, give the callback number, and end politely.`
   - ES: `Eres Daniel, llamando de parte de Garnish Proof. Habla en espanol latinoamericano neutral, con calidez, claridad y calma. La persona puede estar estresada porque recientemente se dicto un fallo de deuda en su contra. No hagas promesas legales. No des asesoria legal. Da el mensaje, comparte el numero de llamada, y termina amablemente.`

After setting voice + global prompt, **Publish** each pathway.

---

## Naturalness notes (best practice)

- `skipUserResponse: true` + `block_interruptions: true` → the agent reads the full message
  without waiting or being derailed — correct for a voicemail/answer drop.
- The verbatim-instruction-plus-quoted-script pattern keeps the LLM from paraphrasing
  legally sensitive lines while still using a natural ElevenLabs voice.
- Pacing is carried by punctuation: hyphens and short paragraphs create the pauses the
  ElevenLabs direction asks for. Keep the line breaks intact.
- `wait_for_greeting: true` + `answered_by_enabled` + `voicemail.action=leave_message`
  (set in `gp_bland.py`) make the same script land cleanly on a live answer or a machine.
- **Local Presence** add-on (Bland → Add-ons) matches the outbound area code to the callee
  and materially lifts answer rates — recommended once a GP number is live.

---

## Remaining go-live prerequisites (calling, not just pathways)

The pathways exist and the code passes the right data. To actually dial, this still needs:

1. **A Garnish Proof outbound number.** The account currently owns only `+18186167276`
   (EC/Grant Ellis). Buy a GP number (Bland → Phone Numbers → Buy Number, ~$15/mo; Houston
   area codes 281/713/832 for Harris). Set `BLAND_GP_PHONE_NUMBER` and
   `BLAND_GP_CALLBACK_PHONE_NUMBER`.
2. **Set the agent IDs in Railway** (and `.env` for local QA):
   - `BLAND_GP_AGENT_ID=b667895e-3369-45a5-8642-3db98f89322d`
   - `BLAND_GP_SPANISH_AGENT_ID=b186df96-dc92-4857-a90c-8da8249188f5`
3. **Top up the Bland balance** (was ~-$1.47 at setup).
4. **Apply migration 024** (outreach columns) + run `jobs.run_gp_harris` (ingest) +
   `gp_enrich` (phones) so `garnishment_orders` has rows with `phone` + `ghl_contact_id`.
   `gp_bland.trigger_batch` reads those.
5. **Publish both pathways** in the Bland UI (currently `published_at: null`). API calls with
   `pathway_id` use the published production version; the publish endpoint couldn't be driven
   via API, so click Publish on each after confirming voice.
6. QA with `--dry-run`, then a test call to an internal number, before any real dialing.

`gp_bland` already enforces the shared DNC gate, the 30-day freshness window, and the
CT call-window — same guardrails as ISTS.
