# Vantage Go-Live Batch 1 Review — 2026-05-18

## Batch Summary

First controlled SearchBug Green-A batch attempted:

- Planned sample: 25 tenant leads
- SearchBug lookups completed before balance issue recurred: 8
- Raw phone hits: 4 / 8
- Strong same-property matches: 2 / 8
- Different-address hits requiring review: 2 / 8

## Best Leads To Work First

These are the strongest outputs from the completed portion of the batch because SearchBug returned a phone and a same-property address match.

| Case # | Tenant | Filing Address | SearchBug Returned Address | Phone | Confidence |
|---|---|---|---|---|---|
| 26GT1910 | SHAUNTRAIL PICKETT | 405 S 4TH STREET, UNIT 301, NASHVILLE, TN 37206 | 405 S 4TH ST APT 301, NASHVILLE, TN 37206 | 414-759-0382 | High |
| 26GT3351 | RASHOD ROSE | 2704 GLEN OAKS DRIVE, NASHVILLE, TN 37214 | 2704 GLENOAKS DR, NASHVILLE, TN 37214 | 910-224-0930 | High |

## Manual Review Before Use

These returned a phone, but SearchBug's address did **not** match the filing address. They may still be the same person, but they should not be treated as auto-ready without review.

| Case # | Tenant | Filing Address | SearchBug Returned Address | Phone | Review Reason |
|---|---|---|---|---|---|
| 26GT1309 | ABDELRAHMAN MOHAMOUD | 4420 TAYLOR ROAD, UNIT 7204, NASHVILLE, TN 37211 | 3011 RG BUCHANAN DR, LA VERGNE, TN 37086 | 615-207-5455 | Different current address |
| 26GT2234 | KENNY HARDY | 1400 BRICK CHURCH PIKE, APT 116, NASHVILLE, TN 37207 | 4040 CENTRAL PIKE #1-105, HERMITAGE, TN 37076 | 662-336-7385 | Different current address |

## Not Actionable From This Batch Yet

The remaining planned rows should **not** be interpreted as true misses because the SearchBug prepaid balance issue recurred mid-batch and blocked later API requests.

## Recommended Immediate Use

1. Work the **2 high-confidence leads** first.
2. Manually inspect the **2 review leads** before deciding whether to use them.
3. Do **not** continue a larger SearchBug batch until:
   - the prepaid API issue is stable, and
   - we decide whether Green-A needs an additional scoring layer to improve spend efficiency.

## Operational Lesson

The first 5-case pilot was overly optimistic. The first live batch suggests:

- people-search is still a better tenant-discovery fit than BatchData property skip trace,
- but SearchBug is too expensive to spray broadly without tighter prioritization.

## Source File

- Raw batch output: `tmp/searchbug_green_a_batch1_2026-05-18.csv`

