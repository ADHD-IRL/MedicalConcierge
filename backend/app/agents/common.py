from __future__ import annotations

from dataclasses import dataclass, field

from app.config import get_settings
from app.ingestion.file_loader import load_as_images
from app.ingestion.multimodal_extractor import extract_records
from app.normalization import supplement_terms
from app.normalization.rxnorm_client import RxNormClient
from app.schemas import ExtractedItem, LabResult, NormalizedRecord, RecordKind, RxNormMatch

_RXNORM_ACCEPT_THRESHOLD = 50.0


def make_rxnorm_client() -> RxNormClient:
    settings = get_settings()
    return RxNormClient(base_url=settings.rxnorm_base_url)


async def normalize_item(client: RxNormClient, item: ExtractedItem) -> RxNormMatch:
    """RxNorm first; for supplements RxNorm doesn't know (most herbal
    products), fall back to the curated local synonym table."""
    match = await client.find_best_match(item.name_as_written, context=item)
    if item.kind == RecordKind.supplement and match.match_score < _RXNORM_ACCEPT_THRESHOLD:
        local = supplement_terms.lookup(item.name_as_written)
        if local is not None:
            return local
    return match


@dataclass
class DocumentResult:
    records: list[NormalizedRecord] = field(default_factory=list)
    labs: list[LabResult] = field(default_factory=list)


async def process_document(filename: str, data: bytes) -> DocumentResult:
    """The full ingestion pipeline for one uploaded document: rasterize/load
    -> single-pass multimodal extraction (medicines, supplements, AND lab
    results, each classified by the model) -> per-item normalization ->
    NormalizedRecord. Labs need no normalization pass - they are transcribed
    values, not entities to be resolved against a vocabulary."""

    settings = get_settings()
    images = load_as_images(filename, data, dpi=settings.pdf_render_dpi)
    extracted = extract_records(images)

    for lab in extracted.labs:
        lab.source_filename = filename
        lab.needs_review = (
            lab.extraction_confidence < settings.review_confidence_threshold
            or bool(lab.ambiguities)
        )

    client = make_rxnorm_client()
    records: list[NormalizedRecord] = []
    for item in extracted.items:
        match = await normalize_item(client, item)
        records.append(
            NormalizedRecord.build(
                kind=item.kind,
                extracted=item,
                normalization=match,
                review_threshold=settings.review_confidence_threshold,
                source_filename=filename,
            )
        )
    return DocumentResult(records=records, labs=extracted.labs)
