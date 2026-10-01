"""Lab ingestion, and the loop that turns a review's open questions into
answers the next review has to reckon with."""

import fitz
import pytest
from fastapi.testclient import TestClient

from app.config import get_settings
from app.main import app
from app.panel import observations as obs
from app.panel.schemas import (
    Dissent,
    ObservationSource,
    ObservationStatus,
    PanelReview,
    TrackedObservation,
)
from app.schemas import LabFlag, LabResult
from app.storage.store import RecordStore
from tests.test_panel import _fake_structured_for, mocked_llm  # noqa: F401 (fixture)


def _pdf_text(pdf_bytes: bytes) -> str:
    with fitz.open(stream=pdf_bytes, filetype="pdf") as doc:
        return "\n".join(page.get_text() for page in doc)


# ---------------------------------------------------------------------- labs

def test_labs_round_trip_newest_first(tmp_path):
    store = RecordStore(str(tmp_path / "labs.sqlite3"))
    store.save_labs([
        LabResult(name="INR", value="2.4", collected_date="2026-08-01"),
        LabResult(name="eGFR", value="48", unit="mL/min", collected_date="2026-09-20",
                  flag=LabFlag.low),
    ])

    labs = store.list_labs()
    assert [lab.name for lab in labs] == ["eGFR", "INR"]  # newest collection first
    assert labs[0].display == "48 mL/min"


def test_lab_values_are_never_coerced_to_numbers(tmp_path):
    """Inequalities and qualitative results are exactly the things a float
    would destroy, and a silently mangled lab value is worse than none."""
    store = RecordStore(str(tmp_path / "labs.sqlite3"))
    store.save_labs([
        LabResult(name="TSH", value="<0.01", unit="mIU/L"),
        LabResult(name="Hep C Ab", value="negative"),
    ])
    assert {lab.value for lab in store.list_labs()} == {"<0.01", "negative"}


def test_brief_tells_the_panel_when_it_has_no_labs():
    from app.panel.prompts import regimen_brief
    from app.schemas import MedListItem, RecordKind

    items = [MedListItem(kind=RecordKind.medicine, name="Warfarin", dosage="5 mg")]

    blind = regimen_brief(items, [], [], "")
    assert "NONE" in blind and "Say what you are assuming" in blind

    seeing = regimen_brief(items, [], [], "", labs=[
        LabResult(name="INR", value="2.4", reference_range="2.0-3.0", flag=LabFlag.normal),
        LabResult(name="eGFR", value="48", flag=LabFlag.low, needs_review=True),
    ])
    assert "INR: 2.4 (ref 2.0-3.0) [NORMAL]" in seeing
    assert "READING UNCERTAIN" in seeing  # the record auditor keys off this


# -------------------------------------------------------------- the loop

def _review_with_open_questions():
    return PanelReview(
        dissent=[Dissent(topic="Whether the fish oil matters", side_a="a", side_b="b",
                         what_would_settle_it="An INR compared against the previous one.")],
        discriminating_observations=[
            "Whether the bruising began when the fish oil started.",
            "An INR compared against the previous one!",  # near-duplicate of the dissent one
            "tiny",  # too short to be a real observation
        ],
    )


def test_derive_prefers_dissent_and_dedupes():
    derived = obs.derive(_review_with_open_questions(), [])

    assert [o.source for o in derived] == [
        ObservationSource.dissent, ObservationSource.discriminating
    ]
    assert derived[0].topic == "Whether the fish oil matters"
    assert all(len(o.text) > 8 for o in derived)


def test_derive_does_not_re_ask_what_was_already_answered():
    already = [TrackedObservation(
        text="An INR compared against the previous one.",
        status=ObservationStatus.answered, answer="2.4, unchanged",
    )]
    derived = obs.derive(_review_with_open_questions(), already)

    assert [o.text for o in derived] == ["Whether the bruising began when the fish oil started."]


def test_answered_filter_ignores_blank_answers():
    tracked = [
        TrackedObservation(text="a question here", status=ObservationStatus.answered, answer="yes"),
        TrackedObservation(text="another question", status=ObservationStatus.answered, answer="  "),
        TrackedObservation(text="a third question", status=ObservationStatus.open),
        TrackedObservation(text="a fourth question", status=ObservationStatus.dismissed),
    ]
    assert [o.answer for o in obs.answered(tracked)] == ["yes"]
    assert len(obs.open_only(tracked)) == 1


def test_answers_reach_the_next_panel_run():
    from app.panel.prompts import regimen_brief
    from app.schemas import MedListItem, RecordKind

    brief = regimen_brief(
        [MedListItem(kind=RecordKind.medicine, name="Warfarin")], [], [], "",
        answered=[TrackedObservation(
            text="An INR compared against the previous one.",
            status=ObservationStatus.answered, answer="2.4, unchanged from June",
        )],
    )
    assert "Answers since the last review (1)" in brief
    assert "2.4, unchanged from June" in brief
    assert "CONCEDE" in brief  # agents are explicitly held to their concede-when


# ------------------------------------------------------------------- API

@pytest.fixture
def client(tmp_path, monkeypatch, mocked_llm):  # noqa: F811
    monkeypatch.setenv("DB_PATH", str(tmp_path / "loop.sqlite3"))
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key")
    get_settings.cache_clear()
    yield TestClient(app)
    get_settings.cache_clear()


def test_panel_run_produces_observations_to_chase(client):
    client.post("/api/list/items", json={"kind": "medicine", "name": "Warfarin", "dosage": "5 mg"})
    assert client.get("/api/observations").json()["observations"] == []

    client.post("/api/panel", json={"note": ""})
    tracked = client.get("/api/observations").json()["observations"]

    assert tracked, "a completed review should leave the person something to find out"
    assert all(o["status"] == "open" for o in tracked)
    assert any(o["source"] == "dissent" for o in tracked)


def test_answer_and_dismiss_lifecycle(client):
    client.post("/api/list/items", json={"kind": "medicine", "name": "Warfarin"})
    client.post("/api/panel", json={"note": ""})
    tracked = client.get("/api/observations").json()["observations"]
    first, second = tracked[0]["id"], tracked[-1]["id"]

    answered = client.post(f"/api/observations/{first}/answer",
                           json={"answer": "INR was 2.4, unchanged"}).json()["observation"]
    assert answered["status"] == "answered" and answered["answered_at"]

    dismissed = client.post(f"/api/observations/{second}/dismiss").json()["observation"]
    assert dismissed["status"] == "dismissed"

    assert client.post("/api/observations/nope/answer", json={"answer": "x"}).status_code == 404
    assert client.post("/api/observations/nope/dismiss").status_code == 404


def test_a_second_run_does_not_re_ask_an_answered_question(client):
    client.post("/api/list/items", json={"kind": "medicine", "name": "Warfarin"})
    client.post("/api/panel", json={"note": ""})

    tracked = client.get("/api/observations").json()["observations"]
    for o in tracked:
        client.post(f"/api/observations/{o['id']}/answer", json={"answer": "found out"})

    client.post("/api/panel", json={"note": ""})
    after = client.get("/api/observations").json()["observations"]

    # The same questions are not asked twice, and the answers survive.
    assert len(after) == len(tracked)
    assert all(o["status"] == "answered" for o in after)


def test_labs_endpoint_and_reset(client):
    from app.storage.store import RecordStore

    RecordStore(get_settings().db_path).save_labs([
        LabResult(name="INR", value="2.4", flag=LabFlag.normal, collected_date="2026-09-01")
    ])
    body = client.get("/api/labs").json()
    assert [lab["name"] for lab in body["labs"]] == ["INR"]

    client.post("/api/list/items", json={"kind": "medicine", "name": "Warfarin"})
    client.post("/api/panel", json={"note": ""})
    assert client.get("/api/observations").json()["observations"]

    client.post("/api/reset")
    assert client.get("/api/labs").json()["labs"] == []
    assert client.get("/api/observations").json()["observations"] == []


def test_labs_appear_in_the_clinician_and_panel_pdfs(client):
    RecordStore(get_settings().db_path).save_labs([
        LabResult(name="eGFR", value="48", unit="mL/min", reference_range="&gt;60",
                  flag=LabFlag.low, collected_date="2026-09-20"),
    ])
    client.post("/api/list/items", json={"kind": "medicine", "name": "Lisinopril"})

    clinician = _pdf_text(client.get("/api/export?format=pdf").content)
    assert "LAB RESULTS AS READ FROM THE PATIENT'S DOCUMENTS" in clinician
    assert "eGFR: 48 mL/min" in clinician
    assert "[LOW]" in clinician

    client.post("/api/panel", json={"note": ""})
    panel = _pdf_text(client.get("/api/panel/pdf").content)
    assert "LAB VALUES THE PANEL READ" in panel
    assert "eGFR" in panel


def test_archive_pdf_carries_labs(client):
    RecordStore(get_settings().db_path).save_labs([LabResult(name="ALT", value="62", flag=LabFlag.high)])
    client.post("/api/list/items", json={"kind": "medicine", "name": "Atorvastatin"})

    text = _pdf_text(client.post("/api/reset").content)
    assert "LAB RESULTS" in text and "ALT: 62" in text


def test_resolved_items_flow_through_synthesis(monkeypatch, mocked_llm):  # noqa: F811
    """Convergence has to be visible, or the loop is invisible work."""
    import asyncio

    from app.panel import engine, llm
    from app.schemas import MedListItem, RecordKind

    original = llm.structured

    async def with_resolution(system, user, schema, *, gate, max_tokens=4000, model=None):
        result = await original(system, user, schema, gate=gate, max_tokens=max_tokens, model=model)
        if "dissent" in set(schema.get("required", [])):
            result["resolved"] = [{
                "topic": "Whether the fish oil matters",
                "settled_by": "INR 2.4, unchanged from June",
                "outcome": "The pharmacist seat conceded; no measurable effect at this dose.",
            }]
        return result

    monkeypatch.setattr(llm, "structured", with_resolution)
    review = asyncio.run(engine.run_panel(
        [MedListItem(kind=RecordKind.medicine, name="Warfarin")], [], [], note="",
        answered=[TrackedObservation(text="An INR compared against the previous one.",
                                     status=ObservationStatus.answered, answer="2.4")],
    ))

    assert len(review.resolved) == 1
    assert "conceded" in review.resolved[0].outcome


def test_saving_a_review_never_clears_answered_observations(tmp_path):
    """The loop depends on answers outliving the run that asked for them."""
    from app.storage.med_list import MedListStore

    store = MedListStore(str(tmp_path / "x.sqlite3"))
    store.save_observations([TrackedObservation(
        text="An INR compared against the previous one.",
        status=ObservationStatus.answered, answer="2.4",
    )])
    store.save_panel_review(PanelReview(headline="a later review"))

    survived = store.list_observations()
    assert [o.answer for o in survived] == ["2.4"]
