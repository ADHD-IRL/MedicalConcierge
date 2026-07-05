from __future__ import annotations

import csv
import io
from datetime import date

import anthropic
from fastapi import APIRouter, HTTPException, UploadFile
from fastapi.responses import Response, StreamingResponse

from app.agents.medicine_agent import process_medicine_document
from app.agents.supplement_agent import process_supplement_document
from app.config import get_settings
from app.export.pdf_report import build_pdf
from app.interactions.engine import evaluate
from app.ingestion.file_loader import UnsupportedFileType
from app.schemas import IngestResponse, RecordKind, SourceType
from app.storage.store import RecordStore

router = APIRouter()

MAX_UPLOAD_BYTES = 30_000_000  # sanity cap; images are downscaled after this gate


def get_store() -> RecordStore:
    return RecordStore(get_settings().db_path)


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
    except anthropic.APIError as exc:
        raise HTTPException(
            status_code=502,
            detail="The document-reading service returned an error: "
            f"{getattr(exc, 'message', str(exc))[:300]}",
        ) from exc

    get_store().save_all(records)
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
    records = get_store().list_all()
    findings = evaluate(records)
    return {"findings": [f.model_dump(mode="json") for f in findings]}


@router.get("/export")
def export_records(format: str = "json"):
    store = get_store()
    records = store.list_all()

    if format == "json":
        return {"records": [r.model_dump(mode="json") for r in records]}

    if format == "pdf":
        pdf_bytes = build_pdf(records, findings=evaluate(records))
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
