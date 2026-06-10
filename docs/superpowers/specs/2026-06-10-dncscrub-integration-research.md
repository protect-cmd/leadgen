# DNCScrub.com Integration — Research + Design

**Date:** 2026-06-10
**Status:** Research complete; integration scaffold ready (needs API key + cost go-ahead)
**Problem:** ~28% of SearchBug-found phones land in `held` because their area code has no local
FTC DNC file. We pay to enrich them and can't dial. DNCScrub's API scrubs *every* area code.

## What DNCScrub.com offers (vs our local files)

| | Local FTC files (today) | DNCScrub API |
|---|---|---|
| Coverage | 10 area codes we downloaded | **National + all 50 states**, real-time |
| Lists | Federal DNC only | Federal + State + Internal + **litigator** + EBR + wireless/VoIP + time restrictions |
| "Held" outcome | ~28% of found phones | **0** — every number gets a verdict |
| Freshness | manual monthly re-download | live |

## API contract (from docs.dncscrub.com)

**Full scrub:** `GET https://www.dncscrub.com/app/main/rpc/scrub`
- Params: `loginId=<API key>`, `phoneList=<comma-separated>`, `version=5`, `output=json`,
  optional `projId`, `campaignId`
- Response (array): `{Phone, ResultCode, Reason, RegionAbbrev, Country, IsWirelessOrVoIP,
  CallingTimeRestrictions, EBRType}`
- **ResultCode:** `C`=Clean(callable) · `D`=Do-Not-Call · `W`=Wireless(not blocked) ·
  `L`=Wireless prohibited in state · `G`/`H`=EBR override (callable)

**Litigator-only (cheaper, optional add-on):** `GET https://dataapi.dncscrub.com/v1.4/scrub/litigator`
→ `{Phone, IsLitigator}`. Useful as a TCPA-litigator guard even if we keep local DNC.

## Verdict mapping (our `on_dnc` semantics)

| ResultCode | Verdict | Action |
|---|---|---|
| C, W, G, H | `callable` | dial |
| D, L | `dnc` | drop |
| (error / unknown) | `unknown` | honor `DNC_FAIL_CLOSED` (currently `true` → treat as drop) |

This replaces the three-way `callable / on_dnc / held` with a clean two-way `callable / dnc`
across all area codes.

## Integration design

`services/dnc_service.py` (scaffolded):
- `verdict(phone) -> "callable" | "dnc" | "unknown"`:
  1. If `DNCSCRUB_LOGIN_ID` set → call the scrub API, map ResultCode.
  2. Else / on API error → fall back to the **local FTC files** (current behavior), then
     `DNC_FAIL_CLOSED` for anything still unknown.
- Pure `result_code_verdict(code)` (unit-tested, no network).
- Batch helper `verdict_many(phones)` (API takes comma-separated lists — scrub in chunks).

Then the enrich/stage scripts call `dnc_service.verdict()` instead of their local `on_dnc()`,
so held numbers convert to a real callable/dnc decision. **Inert until the key is added** — with
no key it behaves exactly as today (local files).

## What's needed to go live
1. **`DNCSCRUB_LOGIN_ID`** (API key) in `.env` / Railway. (`DNC_PROVIDER=batchdata` in env is dead
   config from the removed 2026-05-28 DNC path — repurpose or ignore.)
2. **Cost confirmation** — DNCScrub bills per scrub. At ~1,000 found phones/cycle this is a small
   per-lookup cost; confirm the rate and whether to scrub at enrich-time (every found phone) or
   only at fire-time (only numbers we're about to dial — cheaper).
3. Optional `projId`/`campaignId` for their reporting.

## Recommendation
Wire it at **fire-time** first (scrub only numbers we're about to dial) to minimize cost while
eliminating held-number risk on actual calls — then expand to enrich-time if the rate is trivial.
Keep the local files as the offline fallback. Add the litigator check regardless (cheap TCPA guard).
