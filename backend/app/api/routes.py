from __future__ import annotations

import csv
import io
from datetime import date

import anthropic
from fastapi import APIRouter, HTTPException, UploadFile
from fastapi.responses import Response, StreamingResponse
from pydantic import BaseModel, Field

from app.agents.common import make_rxnorm_client
from app.agents.medicine_agent import process_medicine_document
from app.agents.supplement_agent import process_supplement_document
from app.config import get_settings
from app.export.pdf_report import build_pdf
from app.interactions.engine import evaluate
from app.ingestion.file_loader import UnsupportedFileType
from app.ingestion.multimodal_extractor import ExtractionTruncated
from app.normalization import supplement_terms
from app.schemas import IngestResponse, MedListItem, RecordKind, SourceType
from app.storage.med_list import EDITABLE_FIELDS, MedListStore, item_to_record
from app.storage.store import RecordStore

router = APIRouter()

MAX_UPLOAD_BYTES = 30_000_000  # sanity cap; images are downscaled after this gate


def get_store() -> RecordStore:
    return RecordStore(get_settings().db_path)


def get_list_store() -> MedListStore:
    return MedListStore(get_settings().db_path)


def _screening_records():
    """Findings and the doctor PDF run off the curated list when it has
    entries (so stopping a medicine on screen clears its warnings); before
    the list exists they fall back to raw ingested records."""
    list_store = get_list_store()
    items = list_store.list_items()
    if items:
        from app.schemas import ItemStatus

        return [item_to_record(i) for i in items if i.status == ItemStatus.active]
    return get_store().list_all()


async def _run_ingest(file: UploadFile, source_type: SourceType, processor, kind: RecordKind):
    data = await file.read()
    if len(data) > MAX_UPLOAD_BYTES:
        raise HTTPException(
            status_code=413,
            detail=f"That file is {len(data) / 1_000_000:.0f} MB - the limit is "
            f"{MAX_UPLOAD_BYTES // 1_000_000} MB. A photo or a smaller PDF works best.",
        )
    try:
        records = await processor(file.filename, data, source_type=source_type)
    except UnsupportedFileType as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except ExtractionTruncated as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except anthropic.RequestTooLargeError as exc:
        raise HTTPException(
            status_code=413,
            detail="This document is too large to read in one pass even after "
            "compression. Try splitting the PDF into smaller parts.",
        ) from exc
    except anthropic.APIError as exc:
        raise HTTPException(
            status_code=502,
            detail="The document-reading service returned an error: "
            f"{getattr(exc, 'message', str(exc))[:300]}",
        ) from exc

    get_store().save_all(records)
    get_list_store().sync_from_records(records)
    return IngestResponse(filename=file.filename, kind=kind, records=records)


@router.get("/health")
def health():
    settings = get_settings()
    return {
        "ok": True,
        "anthropic_key_configured": bool(settings.anthropic_api_key.strip()),
    }


@router.post("/ingest/medicine", response_model=IngestResponse)
async def ingest_medicine(file: UploadFile, source_type: SourceType = SourceType.other):
    return await _run_ingest(file, source_type, process_medicine_document, RecordKind.medicine)


@router.post("/ingest/supplement", response_model=IngestResponse)
async def ingest_supplement(file: UploadFile, source_type: SourceType = SourceType.other):
    return await _run_ingest(file, source_type, process_supplement_document, RecordKind.supplement)


@router.get("/records")
def list_records(kind: RecordKind | None = None):
    store = get_store()
    records = store.list_all(kind=kind.value if kind else None)
    return {"records": [r.model_dump(mode="json") for r in records]}


@router.get("/findings")
def list_findings():
    findings = evaluate(_screening_records())
    return {"findings": [f.model_dump(mode="json") for f in findings]}


# --- curated medication/supplement list --------------------------------------


class NewItemRequest(BaseModel):
    kind: RecordKind
    name: str = Field(..., min_length=1, max_length=200)
    dosage: str | None = None
    frequency: str | None = None
    notes: str | None = None


class ItemUpdateRequest(BaseModel):
    name: str | None = None
    dosage: str | None = None
    frequency: str | None = None
    notes: str | None = None
    status: str | None = Field(None, pattern="^(active|stopped)$")


class NewBaselineRequest(BaseModel):
    name: str = Field(..., min_length=1, max_length=120)


@router.get("/list")
def get_med_list():
    store = get_list_store()
    return {
        "items": [i.model_dump(mode="json") for i in store.list_items()],
        "baselines": [
            {"id": b.id, "name": b.name, "created_at": b.created_at.isoformat(),
             "item_count": len(b.items)}
            for b in store.list_baselines()
        ],
    }


@router.post("/list/items")
async def add_list_item(body: NewItemRequest):
    # Manual entries get the same normalization as ingested ones so the
    # screening engine can match them against interaction rules.
    match = await make_rxnorm_client().find_best_match(body.name)
    if body.kind == RecordKind.supplement and match.match_score < 50:
        local = supplement_terms.lookup(body.name)
        if local is not None:
            match = local

    item = MedListItem(
        kind=body.kind,
        name=body.name.strip(),
        canonical_name=match.canonical_name,
        rxcui=match.rxcui,
        dosage=body.dosage,
        frequency=body.frequency,
        notes=body.notes,
    )
    get_list_store().add_item(item)
    return item.model_dump(mode="json")


@router.patch("/list/items/{item_id}")
def update_list_item(item_id: str, body: ItemUpdateRequest):
    updates = {f: getattr(body, f) for f in EDITABLE_FIELDS if getattr(body, f) is not None}
    try:
        item = get_list_store().update_item(item_id, updates)
    except KeyError:
        raise HTTPException(status_code=404, detail="No such list item.")
    return item.model_dump(mode="json")


@router.get("/list/history")
def list_history(item_id: str | None = None):
    events = get_list_store().history(item_id=item_id)
    return {"events": [e.model_dump(mode="json") for e in events]}


@router.post("/baselines")
def create_baseline(body: NewBaselineRequest):
    baseline = get_list_store().create_baseline(body.name.strip())
    return {"id": baseline.id, "name": baseline.name,
            "created_at": baseline.created_at.isoformat(), "item_count": len(baseline.items)}


@router.get("/list/compare/{baseline_id}")
def compare_baseline(baseline_id: str):
    try:
        diff = get_list_store().compare_to_baseline(baseline_id)
    except KeyError:
        raise HTTPException(status_code=404, detail="No such baseline.")
    return diff.model_dump(mode="json")


@router.get("/export")
def export_records(format: str = "json"):
    store = get_store()
    records = store.list_all()

    if format == "json":
        list_store = get_list_store()
        return {
            "records": [r.model_dump(mode="json") for r in records],
            "med_list": [i.model_dump(mode="json") for i in list_store.list_items()],
            "baselines": [b.model_dump(mode="json") for b in list_store.list_baselines()],
            "history": [e.model_dump(mode="json") for e in list_store.history()],
        }

    if format == "pdf":
        screening = _screening_records()
        pdf_bytes = build_pdf(screening, findings=evaluate(screening))
        filename = f"medication_summary_{date.today().isoformat()}.pdf"
        return Response(
            content=pdf_bytes,
            media_type="application/pdf",
            headers={"Content-Disposition": f"attachment; filename={filename}"},
        )

    if format == "csv":
        buffer = io.StringIO()
        writer = csv.writer(buffer)
        writer.writerow(
            [
                "id",
                "kind",
                "name_as_written",
                "canonical_name",
                "rxcui",
                "dosage",
                "frequency",
                "overall_confidence",
                "needs_review",
                "source_filename",
                "created_at",
            ]
        )
        for r in records:
            writer.writerow(
                [
                    r.id,
                    r.kind.value,
                    r.extracted.name_as_written,
                    r.normalization.canonical_name or "",
                    r.normalization.rxcui or "",
                    r.extracted.dosage or "",
                    r.extracted.frequency or "",
                    r.overall_confidence,
                    r.needs_review,
                    r.source_filename or "",
                    r.created_at.isoformat(),
                ]
            )
        buffer.seek(0)
        return StreamingResponse(
            iter([buffer.getvalue()]),
            media_type="text/csv",
            headers={"Content-Disposition": "attachment; filename=medconcierge_export.csv"},
        )

    raise HTTPException(status_code=400, detail="format must be 'json', 'csv', or 'pdf'")
