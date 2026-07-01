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

## Pathways (created live via the Bland API 2026-06-29)

| Pathway | Bland pathway ID | Persona | Railway var |
|---|---|---|---|
| `Cosner Drake English Outbound` | `92f5ae96-2d85-41d8-a495-75751be344e0` | Marcus (EN) | `BLAND_CD_AGENT_ID` |
| `Cosner Drake Spanish Outbound` | `ef717876-9113-4eba-86af-153811982d7a` | Daniel (ES) | `BLAND_CD_SPANISH_AGENT_ID` |

Each is a one-way voicemail drop: a single `Default` start node holding the script in the
**`text`** field (verbatim — spoken word-for-word, never paraphrased) with
`skipUserResponse` + `block_interruptions` ON and `modelOptions.voice` set
(`mason` EN / `Esteban` ES), edged straight to an `End Call` node. The agent reads the
message and hangs up — it lands identically on a live answer or a machine. Verified via
`GET /v1/pathway/{id}`: text, voice, and `skipUserResponse` all persisted.

Built and populated programmatically via `POST /v1/pathway/create` then
`POST /v1/pathway/{id}` (browser UA to clear Cloudflare). **One manual step remains:**
both show `published_at: null` and the `/promote` endpoint 404s via API (same limitation
GP hit), so click **Publish** on each in the Bland UI once — calls with `pathway_id` use
the published production version.

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

## Numbers (cleared up 2026-06-29)

There is **one Bland outbound number across all four businesses**: `+18186167276`
(labeled "EC - Outbound" in Bland → Phone Numbers). That is the dial-*from* caller ID for
CD too — `BLAND_CD_PHONE_NUMBER=+18186167276`.

The four RingCentral toll-free numbers (Cosner Drake `888 338-2915`, Garnish Proof
`888 224-2863`, ISTS `888 322-4034`, Vantage `888 214-1711`) are **callback** numbers the
lead dials back — they are *not* Bland numbers and cannot be a Bland dial-from. CD's goes
into the script as `{{cd_phone}}`: `BLAND_CD_CALLBACK_PHONE_NUMBER=+18883382915`.

(Caller ID is therefore an 818 number on Houston-bound calls — a known answer-rate tradeoff
of the single shared number, same as every other vertical. Bland "Local Presence" would fix
it if ever desired.)

## Status (2026-06-29) and remaining go-live steps

Done and verified:
- Both pathways created, populated (verbatim text + voice + one-way drop), live IDs above.
- Railway vars set: `BLAND_CD_AGENT_ID`, `BLAND_CD_SPANISH_AGENT_ID`,
  `BLAND_CD_PHONE_NUMBER=+18186167276`, `BLAND_CD_CALLBACK_PHONE_NUMBER=+18883382915`,
  `BLAND_CD_VOICE=mason`, `BLAND_CD_SPANISH_VOICE=Esteban`.
- `cd_bland.trigger_batch` wired into `jobs.run_cd_outreach` Step 3 with the shared DNC
  gate, the forward Answer-window gate, and the CT call-window.

Remaining before real dials:
1. **Publish both pathways** in the Bland UI (one click each — API `/promote` 404s).
2. **Have inventory:** `python -m jobs.run_cd_outreach --enrich-only` then `--ghl-only` so
   `cosner_filings` has rows with `phone` + `ghl_contact_id` + an open `answer_deadline`.
3. **QA** with `--dry-run`, then a single test call to an internal number, before real dials.
