# Context Vault — Map of Content

> **AI: start here.** This vault is the durable context for the lead-gen pipeline so
> you don't have to re-derive architecture, business rules, or history from the code
> each session. Read the note relevant to your task; follow `[[wikilinks]]`. Keep these
> updated when you change the system — they are the source of truth for *intent*.
> Code is the source of truth for *behaviour*; when they disagree, trust code and fix the note.

## Read-first by task

| Your task | Read |
|---|---|
| Anything (orientation) | this index + [[architecture]] |
| Understand a business / its leads | [[businesses]] |
| Touch the DB / queries / columns | [[data-model]] |
| Run, schedule, or deploy something | [[runbook]] |
| Add/fix/schedule a scraper | [[scrapers]] |
| Understand *why* something is the way it is | [[decisions]] |
| Decode a term (NG, good_leads_now, floor…) | [[glossary]] |

## The system in three sentences
Scrapes public court portals → dedups/stores in Supabase → (manual) SearchBug enrichment →
DNC scrub → pushes to GoHighLevel + dials via Bland — for **four businesses** that share
one pipeline. Spending is governed by a per-business calendar budget + weekend pause
([[decisions]]). The operator runs it **hands-off for scrape+score, manual for spend** ([[runbook]]).

## The four businesses (2×2 — see [[businesses]])
|  | **Filed** | **Judgment / post-judgment** |
|---|---|---|
| **Eviction** | Vantage / VDG (`filings`) | ISTS (`ists_judgments`) |
| **Debt** | Cosner Drake (`cosner_filings`) | Garnish Proof (`garnishment_orders`) |

## Conventions for keeping this vault useful
- One note per concept, **descriptive filename** (agents infer content from the name).
- Link liberally with `[[note-name]]`. A link to a not-yet-written note is a TODO.
- **No secrets** — env-var *names* only.
- Convert relative dates to absolute. Date entries that record state ("as of …").
- `AGENTS.md` (repo root) is the concise auto-loaded operating manual; it points here for depth.
