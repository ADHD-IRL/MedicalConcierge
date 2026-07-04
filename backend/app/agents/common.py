from __future__ import annotations

from collections.abc import Awaitable, Callable

from app.config import get_settings
from app.ingestion.file_loader import load_as_images
from app.ingestion.multimodal_extractor import extract_records
from app.normalization.rxnorm_client import RxNormClient
from app.schemas import ExtractedItem, NormalizedRecord, RecordKind, RxNormMatch, SourceType

NormalizeFn = Callable[[ExtractedItem], Awaitable[RxNormMatch]]


async def process_document(
    filename: str,
    data: bytes,
    kind: RecordKind,
    normalize: NormalizeFn,
    source_type: SourceType = SourceType.other,
) -> list[NormalizedRecord]:
    """Shared pipeline: rasterize/load -> multimodal extract -> normalize each
    item -> build NormalizedRecord. `normalize` is injected so the medicine
    and supplement agents can each supply their own normalization strategy."""

    settings = get_settings()
    images = load_as_images(filename, data, dpi=settings.pdf_render_dpi)
    extracted_items = extract_records(images, record_kind=kind, source_type=source_type)

    records: list[NormalizedRecord] = []
    for item in extracted_items:
        match = await normalize(item)
        records.append(
            NormalizedRecord.build(
                kind=kind,
                extracted=item,
                normalization=match,
                review_threshold=settings.review_confidence_threshold,
                source_filename=filename,
            )
        )
    return records


def make_rxnorm_client() -> RxNormClient:
    settings = get_settings()
    return RxNormClient(base_url=settings.rxnorm_base_url)
