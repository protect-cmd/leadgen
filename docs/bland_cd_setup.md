# Bland.ai Setup Guide — Cosner Drake (CD)

**Product:** Cosner Drake — document prep / response help for consumers who were just
sued in a debt-claim lawsuit and still have an open window to file a written Answer
before a default judgment is entered against them.
**Outbound number:** (888) 338-2915 → `+18883382915`
**Branch:** `feat/cosner-bland`
**Last updated:** 2026-06-29

---

## Stage framing (read first — this is NOT Garnish Proof)

Cosner Drake is the **pre-judgment** twin of Garnish Proof. The lead is a consumer who
has just been **sued** (a debt-claim petition was filed), not someone who already has a
judgment against them. The whole value is the **Answer window**: in a Texas justice-court
debt claim the defendant has roughly until the Monday after 14 days from service to file a
written Answer; miss it and the plaintiff can take a **default judgment**. Cosner Drake
helps them respond in time.

Because the value evaporates once that window closes, `services/cd_bland.py` gates
**forward**: it only dials records whose `answer_deadline` is **today or later**
(`answer_deadline >= today`). A passed or null deadline is excluded. This is different
from ISTS/GP, which gate on a backward freshness lookback from the judgment date.

The scripts below deliberately:
- say the person was **sued** / a case was **filed** (true), not that a judgment exists;
- **do not** quote the "25% of your paycheck" garnishment line (that is GP's post-judgment
  framing and is false here, and false for TX generally);
- make **no legal promises** and give **no legal advice** — they notify, state the
  time-sensitivity, offer help, and leave a callback number.

---

## Pathways (to be created live via the Bland API)

| Pathway | Bland pathway ID | Persona | Railway var |
|---|---|---|---|
| `Cosner Drake English Outbound` | _(set after creation)_ | Marcus (EN) | `BLAND_CD_AGENT_ID` |
| `Cosner Drake Spanish Outbound` | _(set after creation)_ | Daniel (ES) | `BLAND_CD_SPANISH_AGENT_ID` |

Build each the same way as the working GP/EC pathways: a single `Default` node → `End Call`,
with `skipUserResponse` + `block_interruptions` ON (agent reads, does not wait), and the
message node's `prompt` beginning *"Deliver the following message exactly. Do not
paraphrase…"* wrapping the script in quotes so the legally sensitive wording is delivered
word-for-word.

---

## Variables our code injects (`services/cd_bland.py` → `request_data`)

| Variable | Source |
|---|---|
| `{{first_name}}` | `defendant_name`, first token via `_split_name` |
| `{{county}}` | `cosner_filings.county` (e.g. "Harris") |
| `{{cd_phone}}` | `BLAND_CD_CALLBACK_PHONE_NUMBER` (or the outbound number), spoken-digit formatted |
| `{{answer_deadline}}` | `cosner_filings.answer_deadline` (sent; the scripts keep it general rather than speaking a raw date) |

`property_address` is also sent but the scripts don't speak it. Language routing:
`language_hint == "spanish_likely"` → Daniel pathway, else Marcus.

---

## Scripts (draft — review before publishing)

### Marcus — English
```
Hi {{first_name}}, this is Marcus calling from Cosner Drake.

We monitor public court records every single day, and we saw that a debt-collection lawsuit was recently filed against you in {{county}} County court.

Here's what matters right now. When you're sued on a debt like this, you only have a short window to file a written response with the court - it's called an Answer. If that deadline passes and no Answer is filed, the company suing you can ask the court for a default judgment against you automatically - without your side ever being heard.

The good news is the window is likely still open for you. We help people in exactly your situation prepare and file that Answer the right way, so you keep your right to be heard. The documents are reviewed by a licensed attorney, and we can move quickly.

The consultation is completely free. Please call us back today at {{cd_phone}}.

Don't let the clock run out on this one - once a default judgment is entered, it's much harder to undo. Again, that number is {{cd_phone}}.
```

### Daniel — Spanish
```
Hola {{first_name}}, le habla Daniel de Cosner Drake.

Revisamos los registros judiciales publicos todos los dias, y vimos que recientemente se presento una demanda de cobro de deuda en su contra en el tribunal del condado de {{county}}.

Esto es lo importante en este momento. Cuando lo demandan por una deuda asi, usted solo tiene un tiempo limitado para presentar una respuesta escrita ante el tribunal - se llama una Contestacion. Si ese plazo vence y no se presenta una Contestacion, la empresa que lo demanda puede pedirle al tribunal un fallo por falta de respuesta de forma automatica - sin que su version sea escuchada.

La buena noticia es que el plazo probablemente sigue abierto para usted. Ayudamos a personas en su misma situacion a preparar y presentar esa Contestacion correctamente, para que conserve su derecho a ser escuchado. Los documentos son revisados por un abogado autorizado, y podemos actuar rapido.

La consulta es completamente gratuita. Por favor llamenos hoy al {{cd_phone}}.

No deje que se le acabe el tiempo - una vez que se dicta un fallo por falta de respuesta, es mucho mas dificil revertirlo. De nuevo, ese numero es {{cd_phone}}.
```

---

## Global prompt (Bland UI)

Paste into each pathway's Global Prompt box, then Publish:
- **EN:** `You are Marcus calling from Cosner Drake. Speak calmly, clearly, and with steady confidence at a moderate pace. The person may be stressed because they were just sued over a debt. Do not make legal promises. Do not give legal advice. Deliver the message, give the callback number, and end politely.`
- **ES:** `Eres Daniel, llamando de parte de Cosner Drake. Habla en espanol latinoamericano neutral, con calidez, claridad y calma. La persona puede estar estresada porque acaba de ser demandada por una deuda. No hagas promesas legales. No des asesoria legal. Da el mensaje, comparte el numero de llamada, y termina amablemente.`

Voice + timing mirror GP: node `modelOptions` voice `mason` (EN) / `Esteban` (ES),
`wait_for_greeting: true`. The production call payload in `cd_bland.py` also sends
`voice` (`BLAND_CD_VOICE` / `BLAND_CD_SPANISH_VOICE`) and `wait_for_greeting: true`, which
is authoritative for real dials.

---

## Go-live prerequisites (calling, not just code)

The code path (`cd_bland.trigger_batch`) is built and wired into `jobs.run_cd_outreach`
(Step 3) with the shared DNC gate, the Answer-window gate, and the CT call-window. To
actually dial, this still needs:

1. **Create both pathways** in Bland (via API or UI) from the scripts above; record the IDs.
2. **Confirm the outbound number is Bland-usable.** (888) 338-2915 is shown as a RingEX
   (RingCentral) number — to dial *from* it in Bland it must be owned/verified in the Bland
   account (or buy a Houston-area DID and use that). Set `BLAND_CD_PHONE_NUMBER` +
   `BLAND_CD_CALLBACK_PHONE_NUMBER`.
3. **Set the agent IDs in Railway** (and `.env` for local QA):
   `BLAND_CD_AGENT_ID`, `BLAND_CD_SPANISH_AGENT_ID`.
4. **Publish both pathways** in the Bland UI (API calls with `pathway_id` use the published
   version).
5. **Have inventory:** run `jobs.run_cd_outreach --enrich-only` then `--ghl-only` so
   `cosner_filings` has rows with `phone` + `ghl_contact_id` + an open `answer_deadline`.
6. **QA** with `--dry-run`, then a single test call to an internal number, before real dials.

`cd_bland` already enforces the shared DNC gate, the Answer-window freshness gate, and the
CT call-window — the same guardrails as ISTS/GP.
