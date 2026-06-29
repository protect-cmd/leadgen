# Bland Inbound IVR — shared-number callback router

**Last updated:** 2026-06-29
**Pathway:** `Shared Inbound IVR - Callback Router` — `57e5af09-ecab-47d3-b51d-08d28a7cbef3`
**On number:** `+18186167276` (the single shared Bland outbound, all four brands)
**Setup script:** `scripts/setup_inbound_ivr.py`

## Why this exists

All four businesses dial out from **one** Bland number (`+18186167276`). The voicemail
each lead hears tells them to call back that brand's **RingCentral** toll-free line — but a
chunk of people just hit "call back" on the missed call, which rings the shared Bland
number. A Bland number can hold only **one** inbound pathway, so a single shared number
can't natively know which of the four brands the caller belongs to.

This IVR solves it conversationally: it answers, asks which company/situation, and
**transfers** to that brand's RingCentral line.

## Routing

| Caller says / mentions | Transfers to | Brand |
|---|---|---|
| Vantage, or eviction case just filed (no judgment) | `+18882141711` | Vantage / VDG |
| ISTS, or eviction judgment / writ / set-out / told to move out | `+18883224034` | ISTS |
| Cosner Drake, or sued/served over a debt (no judgment yet) | `+18883382915` | Cosner Drake |
| Garnish Proof, or default judgment / wage or bank garnishment | `+18882242863` | Garnish Proof |

Voice `june` (warm receptionist), call recording on. Each transfer node carries the
RingCentral number directly (`data.transferNumber`); the agent picks the branch from the
edge `data.label` conditions above.

## Bland field quirks (don't "fix" these)

- **Edge routing condition must live in `edge.data.label`.** Bland silently drops a
  top-level edge `label` and drops `edge.data.description`, so the *full* condition
  sentence has to be the `data.label` value itself — not a short tag with the detail in a
  description.
- **Publish/promote can't be driven via API** (`/promote` 404s, same as the outbound
  pathways). The inbound number is therefore pointed at the **staging** version number
  explicitly, which works for live calls. To get a clean production version, click
  **Publish** on the pathway in the Bland UI, then bump `pathway_version` in the script.
- **Browser User-Agent required** on all API calls or Cloudflare returns 1010.

## Pause / re-enable (the real on/off switch)

A pathway showing "Published" in the library **routes nothing on its own** — what routes
inbound calls is the *number assignment*. So:

- **Pause:** detach the pathway from the number — `POST /v1/inbound/+18186167276` with
  `{"pathway_id": null, "pathway_version": null, "prompt": null}`. Inbound reverts to its
  original do-nothing state; the pathway stays in the library as an editable draft. (Bland
  has no true "unpublish"; this is the equivalent.)
- **Re-enable:** re-run this script with `INBOUND_IVR_PATHWAY_ID=57e5af09-ecab-47d3-b51d-08d28a7cbef3`
  so it re-attaches the existing pathway instead of creating a new one.

> Status 2026-06-29: built, published, then **paused** (detached) pending go-live readiness.

## Limitations / future

- This is the cheaper single-number approach. The cleaner long-term option is one Bland
  number per business (dial-from + inbound transfer), which removes the IVR step entirely
  — revisit if callback volume or misroutes warrant it.
- No explicit fallback branch yet: if the agent can't classify the caller it will re-ask.
  If misroutes show up in recordings, add a "default / unsure" edge to a main line.
