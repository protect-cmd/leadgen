# Discovery Pipeline Sheet — Setup Instructions

The tenant-team workflow runs on a single shared Google Sheet. This file tells you how to spin it up in ~5 minutes from the CSV templates in this directory.

## Files

- `discovery_pipeline_template.csv` — main tab data, pre-populated with each builder's starting queue from `source_discovery_matrix.md`
- `discovery_pipeline_legend.csv` — column rules / allowed values reference

## Step 1 — Create the Sheet

1. Go to https://sheets.new
2. Rename the sheet: **Tenant Discovery Pipeline**
3. Rename the first tab: **Discovery Pipeline**
4. File → Import → Upload → `discovery_pipeline_template.csv` → "Replace current sheet"
5. Add a second tab: **Legend**
6. With the Legend tab active: File → Import → Upload → `discovery_pipeline_legend.csv` → "Replace current sheet"

## Step 2 — Conditional formatting (Discovery Pipeline tab)

Format → Conditional formatting. Add these rules in order:

| Rule | Range | Condition | Format |
|---|---|---|---|
| Stuck | M2:M | Custom formula: `=AND(M2<>"", TODAY()-DATEVALUE(M2)>2, NOT(REGEXMATCH(E2, "Live\|Rejected\|Skipped")))` | Red background |
| Lead's queue | A2:O | Custom formula: `=OR($E2="Classified-pending-approval", $E2="Submitted-for-review")` | Yellow background |
| Live | A2:O | Custom formula: `=$E2="Live"` | Green background |
| Archived | A2:O | Custom formula: `=AND($F2="Red", $E2="Skipped")` | Gray background |

(Column M = `Last updated`, column E = `Stage`, column F = `Classification`.)

## Step 3 — Data validation

Select column E (`Stage`) → Data → Data validation → "Dropdown" → enter values:

```
Researching
Classified-pending-approval
Approved-to-build
Building
Submitted-for-review
Live
Rejected
Skipped
Upgrade-proposed
```

Repeat for column F (`Classification`) with: `Green`, `Yellow`, `Red`, `TBD`.

Repeat for columns G, H, I with: `Y`, `N`, `TBD`.

## Step 4 — Filter views

Data → Filter views → Create new filter view. Save two:

- **My queue** — filter column E to `Classified-pending-approval` + `Submitted-for-review`
- **Stuck** — same custom formula as the "Stuck" conditional rule above

## Step 5 — Sharing

Share → set the three builders + Lorraine + Recca + Chris as Editors. Anyone with link → Commenter (so the team can reference but not break the sheet).

## Step 6 — Lock Lead notes column

Select column N (`Lead notes`) → Data → Protect range → Restrict edit to Zee only. Builders can read but not write.

## Daily rhythm

- **Builders:** Update `Last updated` daily on every row they own, even if nothing changed. Update `Stage`, `Blocker`, and the Y/N/TBD columns as research progresses.
- **Lead:** Open **My queue** filter view every morning. Process `Classified-pending-approval` (approve Green / mark Yellow / mark Red) and `Submitted-for-review` (code review on GitHub, then move to Live or back to Building with comments). Glance at **Stuck** filter view weekly to escalate.
