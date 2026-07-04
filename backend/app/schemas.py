"""Pydantic models shared across ingestion, normalization, agents, and storage."""

from __future__ import annotations

import uuid
from datetime import datetime
from enum import Enum

from pydantic import BaseModel, Field


class RecordKind(str, Enum):
    medicine = "medicine"
    supplement = "supplement"


class SourceType(str, Enum):
    bottle_label = "bottle_label"
    handwritten_note = "handwritten_note"
    printed_document = "printed_document"
    pdf_page = "pdf_page"
    other = "other"


class ExtractedItem(BaseModel):
    """One medication or supplement mention as read directly off a document,
    before any normalization. Produced by the multimodal extractor."""

    raw_text: str = Field(..., description="Verbatim text as it appears on the source.")
    name_as_written: str = Field(..., description="The drug/supplement name as written.")
    dosage: str | None = Field(None, description="e.g. '500 mg', '10 mcg'.")
    form: str | None = Field(None, description="e.g. tablet, capsule, liquid, patch.")
    route: str | None = Field(None, description="e.g. oral, topical, injection.")
    frequency: str | None = Field(None, description="e.g. 'twice daily', 'as needed'.")
    prescriber_or_source: str | None = Field(
        None, description="Doctor name, pharmacy, or document source if legible."
    )
    date_documented: str | None = Field(
        None, description="Date on the document if legible, ISO format if determinable."
    )
    source_type: SourceType = SourceType.other
    extraction_confidence: float = Field(
        ..., ge=0.0, le=1.0, description="Model's confidence it read this correctly."
    )
    ambiguities: list[str] = Field(
        default_factory=list,
        description="Notes on anything unclear, crossed out, or guessed.",
    )


class RxNormMatch(BaseModel):
    rxcui: str | None = None
    canonical_name: str | None = None
    match_score: float = Field(0.0, ge=0.0, le=100.0)
    normalization_confidence: float = Field(0.0, ge=0.0, le=1.0)
    source: str = Field("rxnorm", description="Which normalization source matched.")


class NormalizedRecord(BaseModel):
    """A fully processed medication or supplement record: extraction + normalization
    combined, ready for storage and downstream cross-checking agents."""

    id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    kind: RecordKind
    extracted: ExtractedItem
    normalization: RxNormMatch
    overall_confidence: float = Field(..., ge=0.0, le=1.0)
    needs_review: bool = False
    created_at: datetime = Field(default_factory=datetime.utcnow)
    source_filename: str | None = None

    @classmethod
    def build(
        cls,
        kind: RecordKind,
        extracted: ExtractedItem,
        normalization: RxNormMatch,
        review_threshold: float = 0.6,
        source_filename: str | None = None,
    ) -> "NormalizedRecord":
        overall = extracted.extraction_confidence * normalization.normalization_confidence
        return cls(
            kind=kind,
            extracted=extracted,
            normalization=normalization,
            overall_confidence=round(overall, 4),
            needs_review=overall < review_threshold,
            source_filename=source_filename,
        )


class IngestResponse(BaseModel):
    filename: str
    kind: RecordKind
    records: list[NormalizedRecord]
