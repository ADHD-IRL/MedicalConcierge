"""Supplement ingestion agent: reads a document (PDF/photo/handwritten note/
supplement bottle) and produces normalized supplement records.

Many supplements (vitamins, common minerals, some standardized herbal
extracts) are present in RxNorm and normalize the same way medications do.
Others (most herbal/nutraceutical products) aren't, so this agent tries
RxNorm first and falls back to a small curated local synonym table --
see docs/ARCHITECTURE.md section 2.5 for the production upgrade path
(TRC Healthcare NatMed Pro).
"""

from __future__ import annotations

from app.agents.common import make_rxnorm_client, process_document
from app.normalization import supplement_terms
from app.schemas import ExtractedItem, NormalizedRecord, RecordKind, RxNormMatch, SourceType

_RXNORM_ACCEPT_THRESHOLD = 50.0


async def process_supplement_document(
    filename: str, data: bytes, source_type: SourceType = SourceType.other
) -> list[NormalizedRecord]:
    client = make_rxnorm_client()

    async def normalize(item: ExtractedItem) -> RxNormMatch:
        rxnorm_match = await client.find_best_match(item.name_as_written)
        if rxnorm_match.match_score >= _RXNORM_ACCEPT_THRESHOLD:
            return rxnorm_match

        local_match = supplement_terms.lookup(item.name_as_written)
        if local_match is not None:
            return local_match

        return rxnorm_match

    return await process_document(
        filename, data, kind=RecordKind.supplement, normalize=normalize, source_type=source_type
    )
