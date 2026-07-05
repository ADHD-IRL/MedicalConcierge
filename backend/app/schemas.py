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
    kind: RecordKind = Field(
        RecordKind.medicine,
        description="Model-assigned classification: prescription/OTC medicine vs "
        "dietary supplement/vitamin/mineral/herbal product.",
    )
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
    ingredient_rxcui: str | None = Field(
        None, description="RxCUI of the underlying ingredient (TTY=IN) - brand and "
        "generic forms of the same drug share this, which is what de-duplicates them."
    )
    ingredient_name: str | None = None
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
    kind: RecordKind | None = Field(
        None, description="Unset for unified ingestion - each record carries its own kind."
    )
    records: list[NormalizedRecord]


class ItemStatus(str, Enum):
    active = "active"
    stopped = "stopped"


class MedListItem(BaseModel):
    """One entry on the curated medication/supplement list - the living,
    user-editable 'what I actually take' record, distinct from the raw
    ingested documents it was built from."""

    id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    kind: RecordKind
    name: str
    canonical_name: str | None = None
    rxcui: str | None = None
    ingredient_rxcui: str | None = None
    ingredient_name: str | None = None
    dosage: str | None = None
    frequency: str | None = None
    status: ItemStatus = ItemStatus.active
    notes: str | None = None
    source_record_id: str | None = Field(
        None, description="Ingested record this item was created from, if any."
    )
    created_at: datetime = Field(default_factory=datetime.utcnow)
    updated_at: datetime = Field(default_factory=datetime.utcnow)


class ListHistoryEvent(BaseModel):
    """Append-only audit trail entry. Every change to the list - creation,
    edit, stop/restart, re-sighting in a new document - lands here and is
    never modified or deleted."""

    id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    item_id: str
    timestamp: datetime = Field(default_factory=datetime.utcnow)
    action: str  # created | updated | stopped | reactivated | observed
    detail: str
    item_snapshot: MedListItem


class Baseline(BaseModel):
    """A named snapshot of the entire list at a point in time. The first one
    ('Original baseline') is created automatically; the user can set a new
    one whenever their regimen is confirmed correct."""

    id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    name: str
    created_at: datetime = Field(default_factory=datetime.utcnow)
    items: list[MedListItem]


class FieldChange(BaseModel):
    field: str
    before: str | None
    after: str | None


class ChangedItem(BaseModel):
    item: MedListItem
    changes: list[FieldChange]


class BaselineDiff(BaseModel):
    baseline_id: str
    baseline_name: str
    baseline_created_at: datetime
    added: list[MedListItem]
    stopped: list[MedListItem]
    changed: list[ChangedItem]
    unchanged_count: int


class FindingSeverity(str, Enum):
    major = "major"
    moderate = "moderate"
    info = "info"


class FindingCategory(str, Enum):
    drug_drug = "drug_drug"
    drug_supplement = "drug_supplement"
    supplement_supplement = "supplement_supplement"
    duplicate = "duplicate"
    depletion = "depletion"


class Finding(BaseModel):
    """One screening result: a potential interaction, duplication, or
    nutrient-depletion recommendation derived from the current record set.

    Findings are always framed as discussion points for the user's doctor or
    pharmacist, never as instructions to change medication."""

    rule_id: str
    severity: FindingSeverity
    category: FindingCategory
    title: str
    involved: list[str] = Field(..., description="Display names of the records involved.")
    involved_record_ids: list[str]
    explanation: str = Field(..., description="Plain-language why/mechanism.")
    recommendation: str = Field(..., description="Suggested next step, discussion-framed.")
    evidence_note: str = Field(
        "Well-documented interaction (built-in reference list).",
        description="Where this rule comes from.",
    )
    reading_confidence: float = Field(
        ..., ge=0.0, le=1.0,
        description="Lowest overall_confidence among involved records - if the "
        "underlying reading is shaky, so is the finding.",
    )
    needs_record_review: bool = Field(
        False, description="True if any involved record is itself flagged needs_review."
    )
