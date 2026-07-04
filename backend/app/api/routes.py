from __future__ import annotations

import csv
import io

from fastapi import APIRouter, HTTPException, UploadFile
from fastapi.responses import StreamingResponse

from app.agents.medicine_agent import process_medicine_document
from app.agents.supplement_agent import process_supplement_document
from app.config import get_settings
from app.ingestion.file_loader import UnsupportedFileType
from app.schemas import IngestResponse, RecordKind, SourceType
from app.storage.store import RecordStore

router = APIRouter()


def get_store() -> RecordStore:
    return RecordStore(get_settings().db_path)


@router.get("/health")
def health():
    settings = get_settings()
    return {
        "ok": True,
        "anthropic_key_configured": bool(settings.anthropic_api_key.strip()),
    }


@router.post("/ingest/medicine", response_model=IngestResponse)
async def ingest_medicine(file: UploadFile, source_type: SourceType = SourceType.other):
    data = await file.read()
    try:
        records = await process_medicine_document(file.filename, data, source_type=source_type)
    except UnsupportedFileType as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    get_store().save_all(records)
    return IngestResponse(filename=file.filename, kind=RecordKind.medicine, records=records)


@router.post("/ingest/supplement", response_model=IngestResponse)
async def ingest_supplement(file: UploadFile, source_type: SourceType = SourceType.other):
    data = await file.read()
    try:
        records = await process_supplement_document(file.filename, data, source_type=source_type)
    except UnsupportedFileType as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    get_store().save_all(records)
    return IngestResponse(filename=file.filename, kind=RecordKind.supplement, records=records)


@router.get("/records")
def list_records(kind: RecordKind | None = None):
    store = get_store()
    records = store.list_all(kind=kind.value if kind else None)
    return {"records": [r.model_dump(mode="json") for r in records]}


@router.get("/export")
def export_records(format: str = "json"):
    store = get_store()
    records = store.list_all()

    if format == "json":
        return {"records": [r.model_dump(mode="json") for r in records]}

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

    raise HTTPException(status_code=400, detail="format must be 'json' or 'csv'")
