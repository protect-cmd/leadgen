from __future__ import annotations
from models.filing import Filing
from models.contact import EnrichedContact


async def enrich(filing: Filing) -> EnrichedContact:
    raise NotImplementedError(
        "BatchData credentials not yet received. "
        "See open items in eviction-lead-pipeline-CLAUDE.md."
    )
