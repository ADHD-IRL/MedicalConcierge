from __future__ import annotations

import asyncio
import csv
import io
import json
from datetime import date, datetime

import anthropic
from fastapi import APIRouter, HTTPException, UploadFile
from fastapi.responses import Response, StreamingResponse
from pydantic import BaseModel, Field

from app.agents.common import make_rxnorm_client, process_document
from app.config import get_settings
from app.export.pdf_report import build_archive_pdf, build_panel_pdf, build_pdf
from app.interactions.engine import evaluate
from app.ingestion.file_loader import UnsupportedFileType
from app.ingestion.multimodal_extractor import ExtractionTruncated
from app.normalization import supplement_terms
from app.assistant import engine as assistant_engine
from app.assistant.schemas import AssistantTurn, Role
from app.panel import engine as panel_engine
from app.panel import observations as panel_observations
from app.panel.llm import PanelUnavailable
from app.panel.registry import load_registry
from app.panel.schemas import ObservationStatus
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


async def _run_ingest(file: UploadFile):
    data = await file.read()
    if len(data) > MAX_UPLOAD_BYTES:
        raise HTTPException(
            status_code=413,
            detail=f"That file is {len(data) / 1_000_000:.0f} MB - the limit is "
            f"{MAX_UPLOAD_BYTES // 1_000_000} MB. A photo or a smaller PDF works best.",
        )
    try:
        result = await process_document(file.filename, data)
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

    store = get_store()
    store.save_all(result.records)
    store.save_labs(result.labs)
    get_list_store().sync_from_records(result.records)
    return IngestResponse(
        filename=file.filename, records=result.records, labs=result.labs
    )


@router.get("/health")
def health():
    settings = get_settings()
    return {
        "ok": True,
        "anthropic_key_configured": bool(settings.anthropic_api_key.strip()),
    }


@router.post("/ingest/document", response_model=IngestResponse)
async def ingest_document(file: UploadFile):
    """Unified ingestion: one pass extracts medicines AND supplements, and the
    model classifies each item's kind and source type from the page itself."""
    return await _run_ingest(file)


# Back-compat aliases: kind/source_type hints are no longer needed - the
# unified pass classifies every item itself.
@router.post("/ingest/medicine", response_model=IngestResponse)
async def ingest_medicine(file: UploadFile, source_type: SourceType = SourceType.other):
    return await _run_ingest(file)


@router.post("/ingest/supplement", response_model=IngestResponse)
async def ingest_supplement(file: UploadFile, source_type: SourceType = SourceType.other):
    return await _run_ingest(file)


@router.get("/records")
def list_records(kind: RecordKind | None = None):
    store = get_store()
    records = store.list_all(kind=kind.value if kind else None)
    return {"records": [r.model_dump(mode="json") for r in records]}


@router.get("/labs")
def list_labs():
    """Lab values read off uploaded documents. The panel reasons about kidney
    and liver function, anticoagulation, and thyroid levels, and without these
    several of its experts can only state what they are assuming."""
    labs = get_store().list_labs()
    return {"labs": [lab.model_dump(mode="json") for lab in labs]}


@router.get("/findings")
def list_findings():
    findings = evaluate(_screening_records())
    return {"findings": [f.model_dump(mode="json") for f in findings]}


# --- SME panel ----------------------------------------------------------------


@router.get("/panel/roster")
def panel_roster():
    """Who could be in the room, and what each would push back on. Shown so
    the panel's composition is inspectable rather than a black box."""
    registry = load_registry()
    return {
        "version": registry.version,
        "disclaimer": registry.disclaimer,
        "house_rules": list(registry.house_rules),
        "rounds": registry.protocol["rounds"],
        "agents": [
            {
                "id": a.id, "name": a.name, "panel": a.panel, "discipline": a.discipline,
                "stance": a.stance, "bias_watch": a.bias_watch, "concedes_when": a.concedes_when,
                "challenges": list(a.typically_challenges), "governance": a.is_governance,
            }
            for a in registry.agents.values()
        ],
    }


@router.get("/panel/latest")
def latest_panel_review():
    """The most recent completed review, so it survives a page reload."""
    review = get_list_store().latest_panel_review()
    return {"review": review.model_dump(mode="json") if review else None}


@router.get("/panel/pdf")
def panel_pdf():
    """The panel's questions and unresolved disagreements, to hand over at an
    appointment."""
    store = get_list_store()
    review = store.latest_panel_review()
    if review is None:
        raise HTTPException(status_code=404, detail="No panel review yet - convene the panel first.")

    pdf_bytes = build_panel_pdf(review, store.list_items(), get_store().list_labs())
    filename = f"panel_review_{review.created_at.date().isoformat()}.pdf"
    return Response(
        content=pdf_bytes,
        media_type="application/pdf",
        headers={"Content-Disposition": f"attachment; filename={filename}"},
    )


# --- conversational assistant ------------------------------------------------


class ChatRequest(BaseModel):
    message: str = Field(..., min_length=1, max_length=4000)


def _record_for_assistant():
    store, list_store = get_store(), get_list_store()
    return {
        "items": list_store.list_items(),
        "labs": store.list_labs(),
        "history": list_store.history(),
        "observations": list_store.list_observations(),
        "findings": evaluate(_screening_records()),
        "review": list_store.latest_panel_review(),
    }


@router.get("/chat")
def chat_history():
    return {"turns": [t.model_dump(mode="json") for t in get_list_store().chat_history()]}


@router.delete("/chat")
def clear_chat():
    get_list_store().clear_chat()
    return {"ok": True}


@router.post("/chat")
async def chat(request: ChatRequest):
    """One conversational turn, grounded in the person's own record.

    Returns structured content - answer, citations, proposed actions - rather
    than rendered markup, so a native client can present each part natively.
    See docs/APP_ROADMAP.md."""

    settings = get_settings()
    if not settings.anthropic_api_key.strip():
        raise HTTPException(
            status_code=503,
            detail="The assistant needs an Anthropic API key. Add one to backend/.env.",
        )

    store = get_list_store()
    user_turn = AssistantTurn(role=Role.user, text=request.message.strip())
    store.save_chat_turn(user_turn)

    try:
        reply = await assistant_engine.answer(
            request.message, prior=store.chat_history()[:-1], **_record_for_assistant()
        )
    except anthropic.APIError as exc:
        raise HTTPException(
            status_code=502,
            detail=f"The assistant could not reach the model: "
            f"{getattr(exc, 'message', str(exc))[:200]}",
        ) from exc

    store.save_chat_turn(reply)
    return {"turn": reply.model_dump(mode="json")}


@router.post("/chat/actions/{action_id}/apply")
def apply_chat_action(action_id: str):
    """Apply one proposed record change, after the person confirmed it.

    Nothing the assistant proposes takes effect without this call: a misheard
    sentence silently editing a medication list is precisely the failure this
    app exists to prevent."""

    store = get_list_store()
    for turn in reversed(store.chat_history()):
        for action in turn.actions:
            if action.id != action_id:
                continue
            if action.applied:
                raise HTTPException(status_code=409, detail="That change was already applied.")

            detail = _apply_action(store, action)
            action.applied = True
            store.save_chat_turn(turn)
            return {"ok": True, "detail": detail,
                    "action": action.model_dump(mode="json")}

    raise HTTPException(status_code=404, detail="No such proposed change.")


def _apply_action(store: MedListStore, action) -> str:
    from app.assistant.schemas import ActionKind
    from app.panel.schemas import ObservationStatus

    if action.kind is ActionKind.add_item:
        payload = action.payload
        item = store.add_item(MedListItem(
            kind=RecordKind(payload.get("kind", "medicine")),
            name=payload["name"],
            dosage=payload.get("dosage"),
            frequency=payload.get("frequency"),
            notes=payload.get("notes"),
        ))
        return f"Added {item.name} to your list."

    if action.kind is ActionKind.update_item:
        fields = {k: v for k, v in action.payload.items() if k in EDITABLE_FIELDS}
        if not fields:
            raise HTTPException(status_code=400, detail="Nothing to change in that update.")
        item = store.update_item(action.target_id, fields)
        if item is None:
            raise HTTPException(status_code=404, detail="That item is no longer on your list.")
        return f"Updated {item.canonical_name or item.name}."

    if action.kind is ActionKind.stop_item:
        item = store.update_item(action.target_id, {"status": "stopped"})
        if item is None:
            raise HTTPException(status_code=404, detail="That item is no longer on your list.")
        return f"Marked {item.canonical_name or item.name} as stopped."

    if action.kind is ActionKind.answer_observation:
        observation = store.get_observation(action.target_id)
        if observation is None:
            raise HTTPException(status_code=404, detail="That question is no longer open.")
        observation.answer = action.payload.get("answer", "").strip()
        observation.status = ObservationStatus.answered
        observation.answered_at = datetime.utcnow()
        store.update_observation(observation)
        return "Recorded your answer to the panel's open question."

    raise HTTPException(status_code=400, detail="Unsupported change.")


@router.get("/observations")
def list_observations():
    """What the panel said would settle its open questions, and what the
    person has since found out."""
    tracked = get_list_store().list_observations()
    return {"observations": [o.model_dump(mode="json") for o in tracked]}


class AnswerRequest(BaseModel):
    answer: str = Field(..., min_length=1, max_length=2000)


@router.post("/observations/{observation_id}/answer")
def answer_observation(observation_id: str, request: AnswerRequest):
    """Record what the person found out. The next panel run reads it as
    established fact, and any expert whose concede-when condition it meets is
    asked to concede explicitly."""
    store = get_list_store()
    observation = store.get_observation(observation_id)
    if observation is None:
        raise HTTPException(status_code=404, detail="No such observation.")

    observation.answer = request.answer.strip()
    observation.status = ObservationStatus.answered
    observation.answered_at = datetime.utcnow()
    store.update_observation(observation)
    return {"observation": observation.model_dump(mode="json")}


@router.post("/observations/{observation_id}/dismiss")
def dismiss_observation(observation_id: str):
    """Not applicable, or not worth chasing. Keeps it out of future runs
    without pretending it was answered."""
    store = get_list_store()
    observation = store.get_observation(observation_id)
    if observation is None:
        raise HTTPException(status_code=404, detail="No such observation.")

    observation.status = ObservationStatus.dismissed
    store.update_observation(observation)
    return {"observation": observation.model_dump(mode="json")}


class PanelRequest(BaseModel):
    note: str = Field("", max_length=4000)


@router.post("/panel")
async def run_panel(request: PanelRequest):
    """Runs the eight-round panel over the current list, streaming progress as
    server-sent events. The last event carries the finished review.

    Streaming rather than one long response because the run takes a while and
    watching the room fill in is most of what makes it legible."""

    settings = get_settings()
    if not settings.enable_panel:
        raise HTTPException(status_code=503, detail="The expert panel is turned off.")
    if not settings.anthropic_api_key.strip():
        raise HTTPException(
            status_code=503,
            detail="The expert panel needs an Anthropic API key. Add one to backend/.env.",
        )

    list_store = get_list_store()
    store = get_store()
    items = list_store.list_items()
    records = store.list_all()  # real extraction confidence, for the record auditor
    findings = evaluate(_screening_records())
    labs = store.list_labs()
    prior = list_store.list_observations()
    answered = panel_observations.answered(prior)

    async def stream():
        queue: asyncio.Queue = asyncio.Queue()

        async def runner():
            try:
                review = await panel_engine.run_panel(
                    items, records, findings, request.note, queue.put_nowait,
                    labs=labs, answered=answered,
                )
                if not review.halted:
                    list_store.save_panel_review(review)
                    # Turn this review's open questions into things the person
                    # can go and answer, which is what makes the next run
                    # converge rather than repeat.
                    list_store.save_observations(panel_observations.derive(review, prior))
                await queue.put({"type": "review", "review": review.model_dump(mode="json")})
            except PanelUnavailable as exc:
                await queue.put({"type": "error", "message": str(exc)})
            except anthropic.APIError as exc:
                await queue.put({"type": "error", "message": f"The model API failed: {exc}"})
            except Exception as exc:  # noqa: BLE001 - the stream must always close cleanly
                await queue.put({"type": "error", "message": str(exc)})
            finally:
                await queue.put(None)

        task = asyncio.create_task(runner())
        try:
            while (event := await queue.get()) is not None:
                yield f"data: {json.dumps(event, default=str)}\n\n"
        finally:
            task.cancel()

    return StreamingResponse(
        stream(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


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
        ingredient_rxcui=match.ingredient_rxcui,
        ingredient_name=match.ingredient_name,
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


@router.post("/reset")
def reset_all():
    """Start over: builds the full-archive PDF from EVERYTHING stored, and
    only once those bytes exist wipes both stores. The PDF is the response,
    so the user cannot end up with cleared data and no archive - a failure
    anywhere before the wipe aborts with nothing deleted."""
    store = get_store()
    list_store = get_list_store()

    pdf_bytes = build_archive_pdf(
        records=store.list_all(),
        items=list_store.list_items(),
        baselines=list_store.list_baselines(),
        history=list_store.history(),
        findings=evaluate(_screening_records()),
        labs=store.list_labs(),
    )

    store.clear_all()
    list_store.clear_all()

    filename = f"medconcierge_archive_{date.today().isoformat()}.pdf"
    return Response(
        content=pdf_bytes,
        media_type="application/pdf",
        headers={"Content-Disposition": f"attachment; filename={filename}"},
    )


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
        pdf_bytes = build_pdf(screening, findings=evaluate(screening), labs=get_store().list_labs())
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
