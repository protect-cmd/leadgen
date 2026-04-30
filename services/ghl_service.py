from __future__ import annotations
from models.contact import EnrichedContact


async def create_contact(
    contact: EnrichedContact,
    tags: list[str],
    pipeline_stage_id: str,
) -> str:
    raise NotImplementedError(
        "GHL stage IDs and custom field IDs not yet received. "
        "See open items in eviction-lead-pipeline-CLAUDE.md."
    )
