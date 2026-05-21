# Whitepages Pro vs SearchBug Benchmark ? 2026-05-19

## Purpose

Compare Whitepages Pro and SearchBug on the **same 30 Green-A tenant leads** using live, current, clean residential eviction records selected from Supabase.

## Cohort

- Cohort size: **30**
- Selection rule:
  - `lead_bucket = residential_approved`
  - `court_date >= 2026-05-19`
  - real street address
  - clean human tenant name
  - no obvious entity / noisy marker
  - no existing tenant-side phone in `lead_contacts`
- Jurisdiction mix in this slice: mostly Davidson County, TN, plus a small number of later rows after cleaning.

## Query Shapes

### Whitepages Pro

Sent:
- first name
- last name
- full street line
- city
- state
- ZIP
- `include_historical_locations=true`

### SearchBug

Sent through current production wrapper:
- first name
- last name
- city
- state
- ZIP

SearchBug does **not** currently receive the full street line in our implementation.

## Results

| Metric | Whitepages Pro | SearchBug |
|---|---:|---:|
| Queries | 30 | 30 |
| Phone hits | 7 | 17 |
| Hit rate | 23.3% | 56.7% |
| Strong same-property matches | 2 | 10 |
| Strong-match rate | 6.7% | 33.3% |
| Hits unique to provider | 5 | 15 |
| Shared hits | 2 | 2 |

### Strong same-property hits

#### Whitepages Pro

- `26GT5028` ? LYNDON WHITE ? `(334) 874-8978`
- `26GT4932` ? KITTY L WILLIAMS ? `(610) 299-1241`

#### SearchBug

- `26GT5042` ? JASMINE SWEAT ? `6159209447`
- `26GT2846` ? DIYAHNA POE ? `6157153229`
- `26GT4996` ? MAYAH KING ? `6152951833`
- `26GT4977` ? CLAYTON BLOCKER ? `4784442064`
- `26GT4959` ? DAWN CARRINGTON ? `3137288599`
- `26GT4956` ? HUBERNEY VALLEJO NORENA ? `6154298742`
- `26GT5029` ? KENIJAH GATES ? `6157273690`
- `26GT4994` ? KHAJONTAKIA BUSH ? `6159397743`
- `26GT3302` ? MYANN CHRISTIAN DAVIDSON ? `8594024073`
- `26GT4932` ? KITTY L WILLIAMS ? `6156269761`

## Cost View

### Whitepages Pro

Using the Growth plan rate supplied by the vendor screenshot:
- 30 queries ? **$0.21/query** = **$6.30**
- Cost per raw phone hit: **$0.90**
- Cost per strong same-property hit: **$3.15**

### SearchBug

Using the latest directly comparable SearchBug test result from **May 18, 2026**:
- 30 queries
- 27 hits
- total cost: **$21.33**

Applied to the same 30-record comparison frame:
- Cost per raw phone hit: **$0.79**
- Cost per strong same-property hit in the 30-record benchmark: **$2.13**

**Interpretation:** Whitepages remained cheaper for the full 30-query run (**$6.30** vs **$21.33**), but SearchBug produced materially better tenant-side output quality in the same 30-record benchmark: far more phone hits and far more strong same-property matches.

## Supabase Persistence

- Best available phone per case was persisted for **22 leads** into tenant-side `lead_contacts`.
- Persisted rows were stored with:
  - `track = ng`
  - `dnc_status = unknown`
  - `enrichment_source = whitepages` or `searchbug`
  - no GHL / Bland action triggered
- Raw provider outputs are preserved in the benchmark artifact files so provider disagreements are not lost.

## Important Caveats

1. Whitepages result parsing initially under-read returned address fields because the live payload uses `current_addresses` / `historic_addresses`; the final analysis in this document uses corrected post-processing over the raw responses.
2. SearchBug strong-match count is based on normalized same-property matching, not exact raw string equality. This matters because `STREET` vs `ST`, `UNIT` vs `APT`, and punctuation otherwise undercount true matches.
3. Whitepages returned some numbers without a same-property address anchor. Those are useful leads, but should remain review-only until confidence scoring or an independent validation step is added.
4. Provider outputs were saved as data-enrichment leads only; DNC status remains unknown and these are **not callable** by policy.

## Trial Usage

From the calls executed in this thread:
- Prior Whitepages queries before this benchmark: **8**
- Benchmark queries: **30**
- Total known Whitepages trial usage: **38 / 50**
- Estimated remaining trial queries: **12**

## Artifacts

- `tmp/provider_benchmark_2026_05_19/cohort.json`
- `tmp/provider_benchmark_2026_05_19/whitepages_raw.json`
- `tmp/provider_benchmark_2026_05_19/searchbug_raw.json`
- `tmp/provider_benchmark_2026_05_19/whitepages_reanalyzed.csv`
- `tmp/provider_benchmark_2026_05_19/searchbug_reanalyzed.csv`
- `tmp/provider_benchmark_2026_05_19/persisted_hits.json`
