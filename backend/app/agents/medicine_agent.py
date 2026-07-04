"""Medicine ingestion agent: reads a document (PDF/photo/handwritten note/pill
bottle) and produces normalized medication records with confidence scores.
"""

from __future__ import annotations

from app.agents.common import make_rxnorm_client, process_document
from app.schemas import ExtractedItem, NormalizedRecord, RecordKind, RxNormMatch, SourceType


async def process_medicine_document(
    filename: str, data: bytes, source_type: SourceType = SourceType.other
) -> list[NormalizedRecord]:
    client = make_rxnorm_client()

    async def normalize(item: ExtractedItem) -> RxNormMatch:
        return await client.find_best_match(item.name_as_written)

    return await process_document(
        filename, data, kind=RecordKind.medicine, normalize=normalize, source_type=source_type
    )
