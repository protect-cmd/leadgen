# Tenant-First Enrichment Design

## Goal

Reorient the production pipeline around **tenant contactability** while preserving the ability to run landlord enrichment later when explicitly enabled.

The business KPI for this design is:

> **Cost per DNC-clear tenant phone**

The system should reduce unnecessary paid enrichment, prioritize the highest-confidence tenant paths, and keep non-clear phone numbers out of automated outreach.

## Product Decisions

1. **Tenant enrichment is the default production path.**
2. **Landlord enrichment remains production-capable behind an independent feature switch.**
3. **Green sources remain preferred inventory** because they already include a reliable defendant/property address.
4. **Yellow sources remain useful but use a stricter low-cost path** because they begin without a reliable street address.
5. **One paid enrichment call per filing is the default budget rule.**
6. **A second paid call is an explicit escalation path, not the default.**
7. **Phone outreach remains fail-closed** unless DNC status is explicitly clear.

## Terminology

- **Green lead**: tenant/defendant name plus a reliable defendant/property street address.
- **Yellow lead**: tenant/defendant name plus case context, but no reliable street address.
- **Tenant track**: Vantage Defense Group-oriented enrichment and downstream processing.
- **Landlord track**: Grant Ellis Group-oriented enrichment and downstream processing.

## Runtime Configuration

Recommended configuration:

```env
TENANT_TRACK_ENABLED=true
LANDLORD_TRACK_ENABLED=false
YELLOW_SECOND_CALL_ENABLED=false
MAX_PAID_ENRICHMENT_CALLS_PER_FILING=1
YELLOW_PEOPLE_SEARCH_VENDOR=searchbug
```

### Track behavior

| Tenant enabled | Landlord enabled | Behavior |
|---|---|---|
| yes | no | Default production mode: tenant-only |
| yes | yes | Dual-track mode for green leads; tenant-first remains standard |
| no | yes | Landlord-only mode remains technically possible |
| no | no | Invalid operational mode; job should fail fast or emit a configuration warning |

## Enrichment Policy

### Green leads

If a filing already has a reliable defendant/property address:

1. Run **tenant address-based enrichment**.
2. If landlord track is enabled, run **landlord enrichment** in parallel.
3. Do **not** run people search first.
4. Process downstream only for enabled tracks.

Default paid-call count:

- tenant-only mode: **1**
- dual-track mode: **2**

### Yellow leads

If a filing lacks a reliable street address:

1. Attempt any **free address recovery** supported by the source:
   - court party page
   - case detail page
   - downloadable filing
   - assessor-based recovery only when confidence is high enough to meet source policy
2. If a reliable address is recovered, route into the green path.
3. If no address is recovered, run **one people-search call** for the tenant.
4. Interpret the result as follows:

| Yellow result | Default action |
|---|---|
| phone only | store; manual review / no automated outreach |
| phone + address | store; do not automatically run a second paid call |
| address only | optional second-call rescue path if explicitly enabled |
| no result / ambiguous | stop |

### Second-call escalation

A second paid call may be used only when:

1. the first paid call recovered a specific address,
2. the tenant identity is not ambiguous,
3. the source/county has enough business value to justify the rescue attempt, and
4. `YELLOW_SECOND_CALL_ENABLED=true`.

The intended rescue flow is:

> people search recovers address but no usable phone → tenant address-based enrichment

The system should not automatically run a second paid call merely because an address exists if a usable phone was already found.

## Vendor Placement

### Green lane

Use an address-based tenant enrichment provider. Current implementation may continue using BatchData for this path because it already fits the address-known use case.

### Yellow lane

Use a people-search provider. Current production default may remain SearchBug until a bake-off proves a better value vendor for eviction-defendant populations.

Future vendors should be pluggable behind the same interface so SearchBug and other people-search vendors can be compared without rewriting the pipeline.

## Compliance Rules

1. `dnc_status = "clear"` is the only auto-callable state.
2. `blocked` is blocked.
3. `unknown` is manual review only.
4. Phone-only yellow results without explicit DNC confidence must not enter automated outreach.
5. Bland.ai auto-calling remains separately controlled and off unless explicitly enabled.
6. SMS workflows must continue to include STOP opt-out language.

## Proposed Architecture Changes

### `pipeline/runner.py`

Refactor track orchestration so the runner:

1. decides source path first (`green` vs `yellow`),
2. checks track flags independently,
3. executes only the enabled tracks,
4. avoids landlord calls when `LANDLORD_TRACK_ENABLED=false`,
5. avoids second paid calls on yellow leads unless escalation conditions are satisfied.

### `services/batchdata_service.py`

Retain:

- `enrich()` for landlord enrichment
- `enrich_tenant()` for address-based tenant enrichment
- `enrich_tenant_by_name()` for yellow-source people-search orchestration

Adjust yellow-source behavior so a recovered address does not automatically force a second paid call when a usable phone already exists.

### Enrichment vendor abstraction

Introduce or preserve a separable people-search layer so the yellow lane can benchmark vendors cleanly. At minimum, the service boundary should support:

- tenant name
- city/state/ZIP narrowing
- optional phone return
- optional address return
- vendor identity
- ambiguity / match-quality metadata

### Metrics and persistence

Persist enough metadata to compare ROI by source and vendor.

Recommended fields or attempt logs:

- lead color at enrichment time
- enrichment path
- vendor
- paid call count
- success type: `phone`, `address`, `phone_and_address`, `no_match`, `ambiguous`
- address recovered
- outreach eligible
- manual review reason
- estimated cost

A dedicated `enrichment_attempts` table is preferred long term because it separates operational history from the current lead snapshot.

## Reporting

Track daily and by source/county:

- filings scraped
- green filings
- yellow filings
- paid calls
- tenant phones found
- DNC-clear tenant phones
- manual-review phones
- cost per phone
- cost per DNC-clear phone
- second-call rescue attempts and success rate

## Testing Requirements

### Unit coverage

- green filing with tenant-only mode invokes only tenant enrichment
- green filing with landlord enabled invokes both tracks
- yellow filing defaults to one people-search call
- yellow phone + address result does not auto-trigger a second call
- yellow address-only result can trigger rescue only when enabled
- invalid configuration with both tracks disabled is handled explicitly
- non-clear DNC never becomes outreach eligible

### Integration coverage

- green tenant-only end-to-end path
- green dual-track end-to-end path
- yellow phone-only path
- yellow address-only rescue path
- yellow no-match path

## Rollout Plan

### Phase 1 — behavior change

- Add independent tenant/landlord track switches.
- Make tenant-only the default production posture.
- Prevent automatic second paid calls on yellow phone hits.

### Phase 2 — observability

- Add enrichment-path instrumentation.
- Report cost/yield by county, source, and vendor.

### Phase 3 — optimization

- Compare SearchBug against alternative people-search vendors using real yellow-like samples.
- Enable selective rescue paths only where incremental DNC-clear phones justify the marginal cost.
- Continue prioritizing discovery and upgrade of green sources over scaling weak yellow inventory.

## Tradeoffs

### Benefits

- materially lower default enrichment spend
- preserves optional landlord-side revenue experiments
- clearer ROI measurement
- better alignment with current business focus
- safer treatment of low-confidence yellow phone results

### Costs

- slightly more configuration surface area
- more conditional logic in orchestration
- dual-track mode remains more expensive when enabled
- ROI reporting requires additional persistence work

## Non-Goals

- Removing all landlord-related code immediately
- Switching yellow vendors before measured evidence exists
- Treating phone-only yellow results as auto-callable
- Reclassifying yellow sources as green without a reliable address basis

