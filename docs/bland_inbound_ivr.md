# Bland Inbound IVR — shared-number callback router

> **SUPERSEDED 2026-07-01 — separate-number model approved.** Each brand now has its
> own dedicated Bland number, so the shared IVR menu is retired. Each number gets a
> tiny single-transfer inbound pathway (greet → transfer to that brand's RingCentral
> line) instead of the conversational menu. Live setup is now
> `scripts/setup_inbound_transfers.py`; see "Separate-number model" below. The shared
> IVR pathway (`57e5af09`) stays in the library, **unbound**, in case we ever revert.
> The rest of this doc is kept for that fallback and for the Bland version/quirk notes.

## Separate-number model (current, live 2026-07-01)

| Brand | Dedicated Bland number | Inbound pathway | Transfers to (RingCentral) |
|---|---|---|---|
| Vantage / VDG | `+18186167276` | `ee472a79-…` Vantage Defense Group Inbound Transfer | `+18882141711` |
| ISTS | `+16506293987` | `10a68966-…` ISTS Inbound Transfer | `+18883224034` |
| Cosner Drake | `+16507105017` | `094caad7-…` Cosner Drake Inbound Transfer | `+18883382915` |
| Garnish Proof | `+16506093551` | `aa44d792-…` Garnish Proof Inbound Transfer | `+18882242863` |

Each inbound pathway is two nodes: a greeting (`"Thanks for calling back — connecting
you to the team now, one moment."`) and a `Transfer Call` node to the RC line. Re-run
`scripts/setup_inbound_transfers.py` to rebuild/rebind (idempotent; pathway IDs are
pinned in the script). Same Bland version-binding rules apply (see quirks below).

Outbound still TODO: repoint each brand's `BLAND_*_PHONE_NUMBER` dial-from to its
dedicated number (currently all still `+18186167276`); note the legacy `EC`/`NG` vs
`ISTS`/`GP` env-var naming mismatch before changing live caller IDs.

---

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

This IVR solves it conversationally: it answers, asks about the caller's **situation**
(not the brand name, which callers rarely remember), and **transfers** to that brand's
RingCentral line.

## Routing — two-step, situation-first

Callers know their situation ("I'm being evicted", "I got sued", "they're garnishing my
check"), not our brand names, so the greeting leads with situation and treats the brand
name as a bonus shortcut. The hard part is the business split: "eviction" alone is
ambiguous (Vantage = just filed, ISTS = judgment/writ) and "debt" alone is ambiguous
(Cosner = sued, Garnish = judgment + garnishment). So the router resolves it in two steps:

1. **Greet & Route** — caller names a brand *or* describes a situation.
   - Names a brand -> straight to that transfer.
   - Eviction-ish, no brand -> **Clarify Eviction** node.
   - Debt-ish, no brand -> **Clarify Debt** node.
2. **Clarify** asks the one question that decides the brand:
   - Eviction: judgment / writ / set-out / move-out date -> ISTS; still early (served /
     notice, no judgment) or unsure -> Vantage.
   - Debt: judgment entered or wages/bank garnished -> Garnish Proof; just sued/served,
     no judgment, or unsure -> Cosner Drake.

| Final branch | Transfers to | Brand |
|---|---|---|
| Eviction + judgment/writ/set-out | `+18883224034` | ISTS |
| Eviction, still early / unsure | `+18882141711` | Vantage / VDG |
| Debt + judgment or garnishment | `+18882242863` | Garnish Proof |
| Debt, just sued / unsure | `+18883382915` | Cosner Drake |

Voice `june` (warm receptionist), call recording on. Each transfer node carries the
RingCentral number directly (`data.transferNumber`); the agent picks the branch from the
edge `data.label` conditions.

## Bland field quirks (don't "fix" these)

- **Edge routing condition must live in `edge.data.label`.** Bland silently drops a
  top-level edge `label` and drops `edge.data.description`, so the *full* condition
  sentence has to be the `data.label` value itself — not a short tag with the detail in a
  description.
- **Inbound routes by a numbered VERSION, and the version must be minted from the
  in-code nodes/edges.** Two traps, both hit during the two-step rebuild:
  - `POST /v1/pathway/{id}` updates only the editable **draft**. The `is_staging` flag
    does *not* follow it, so binding the number to the `is_staging` version serves a
    stale graph. Mint an explicit version with `POST /v1/pathway/{id}/version` and bind
    the number to *that* version number.
  - **Reading the draft back returns edges with empty `data.label`.** A version built
    from a draft-read therefore loses every routing condition (all labels `None`). Build
    the version from the in-code `EDGES` (which still carry `data.label`), not from a GET.
- **Publish/promote can't be driven via API** — `/promote` 404s and `/publish` 400s
  ("Error publishing pathway"). The `POST /v1/pathway/{id}/version` route above is the
  working substitute: it creates a routable numbered version without the UI. Click
  **Publish** in the UI only if you want that version marked as the production default.
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

> Status 2026-07-01: **RETIRED** — detached from `+18186167276` (rebound to VDG's own
> single-brand transfer pathway `ee472a79`). Pathway stays unbound in the library as a
> revert option. Superseded by the separate-number model at the top of this doc.
>
> Status 2026-06-30 (later): **LIVE, two-step situation router** — bound to **v6**, which
> carries all 10 routing labels. Rebuilt after a test showed bare brand words ("vantage")
> failed: the flat 4-way menu over-indexed on brand names and couldn't split filed-vs-
> judgment. Now situation-first with two clarifier nodes (see Routing above).
>
> Prior 2026-06-30: re-attached as the original flat 4-way menu (bound to v3).
> Prior 2026-06-29: built, published, then paused (detached) pending go-live readiness.

## Limitations / future

- This is the cheaper single-number approach. The cleaner long-term option is one Bland
  number per business (dial-from + inbound transfer), which removes the IVR step entirely
  — revisit if callback volume or misroutes warrant it.
- No explicit fallback branch yet: if the agent can't classify the caller it will re-ask.
  If misroutes show up in recordings, add a "default / unsure" edge to a main line.
