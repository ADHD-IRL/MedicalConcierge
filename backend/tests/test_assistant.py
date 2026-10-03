"""The conversational layer: grounded in the record, never directive."""

import asyncio

import pytest
from fastapi.testclient import TestClient

from app.assistant import engine, guard, retrieval
from app.assistant.schemas import ActionKind, CitationKind, Role
from app.config import get_settings
from app.main import app
from app.panel import llm
from app.panel.schemas import ObservationStatus, TrackedObservation, Urgency
from app.schemas import ItemStatus, LabFlag, LabResult, MedListItem, RecordKind


# ------------------------------------------------------------------- guard

@pytest.mark.parametrize("text", [
    "Your cardiologist told you to stop the fish oil on 12 March.",
    "You stopped taking metformin in March, according to your history.",
    "The panel said an INR would settle whether to stop it.",
    "Your prescriber should be the one to decide whether to reduce it.",
    "That is worth asking before you change anything.",
])
def test_guard_allows_reporting_and_recording(text):
    """The naive version of this check fires on all of these, which is why it
    would get switched off. Reporting what a clinician said and recording what
    the person did are both core to what this assistant is for."""
    assert guard.check(text) == ""


@pytest.mark.parametrize("text", [
    "I recommend you stop taking the fish oil.",
    "You should reduce your warfarin dose.",
    "I'd suggest increasing the dose to 7.5 mg.",
    "It would be best to discontinue the lisinopril.",
    "You need to come off the ibuprofen.",
    "My advice is to taper slowly.",
])
def test_guard_catches_the_assistant_recommending(text):
    assert guard.check(text)


# --------------------------------------------------------------- retrieval

def _items():
    return [
        MedListItem(kind=RecordKind.medicine, name="Warfarin", dosage="5 mg", frequency="daily"),
        MedListItem(kind=RecordKind.medicine, name="Old Pill", status=ItemStatus.stopped),
    ]


def test_digest_carries_ids_so_answers_can_cite():
    items = _items()
    labs = [LabResult(name="INR", value="2.4", flag=LabFlag.normal, collected_date="2026-09-20")]
    observations = [
        TrackedObservation(text="An INR compared against the previous one."),
        TrackedObservation(text="Whether the bruising started then.",
                           status=ObservationStatus.answered, answer="No"),
    ]

    digest = retrieval.build_digest(items, labs, [], observations, [])

    assert f"[{items[0].id}]" in digest and "Warfarin - 5 mg daily" in digest
    assert "** STOPPED **" in digest
    assert f"[{labs[0].id}] INR: 2.4" in digest
    assert "OPEN: An INR" in digest and "ANSWERED: Whether the bruising" in digest


def test_digest_says_plainly_when_there_are_no_labs():
    assert "(none on file)" in retrieval.build_digest(_items(), [], [], [], [])


def test_digest_bounds_history_but_reports_the_total():
    from app.schemas import ListHistoryEvent

    history = [
        ListHistoryEvent(item_id="x", action="updated", detail=f"change {i}",
                         item_snapshot=_items()[0])
        for i in range(80)
    ]
    digest = retrieval.build_digest(_items(), [], history, [], [])
    assert "60 most recent of 80" in digest


# ------------------------------------------------------------------ engine

def _reply(**over):
    base = {
        "answer": "Your records don't say why lisinopril was started.",
        "citations": [], "questions_for_clinician": [], "actions": [],
    }
    base.update(over)
    return base


@pytest.fixture
def fake_model(monkeypatch):
    box = {"reply": _reply(), "seen": {}}

    async def fake_structured(system, user, schema, *, gate, max_tokens=4000, model=None):
        box["seen"] = {"system": system, "user": user, "model": model}
        return box["reply"]

    monkeypatch.setattr(llm, "structured", fake_structured)
    return box


def _answer(message, fake_model, **over):
    record = {"items": _items(), "labs": [], "history": [], "observations": [], "findings": []}
    record.update(over)
    return asyncio.run(engine.answer(message, **record))


def test_urgent_message_short_circuits_before_any_model_call(monkeypatch):
    def explode(*a, **k):
        raise AssertionError("no model call may happen on an urgent message")

    monkeypatch.setattr(llm, "structured", explode)
    turn = asyncio.run(engine.answer(
        "I have chest pain and can't breathe",
        items=_items(), labs=[], history=[], observations=[], findings=[],
    ))

    assert turn.urgency is Urgency.urgent
    assert "911" in turn.text


def test_the_record_and_the_question_both_reach_the_model(fake_model):
    _answer("why am I on warfarin?", fake_model)
    assert "Warfarin - 5 mg daily" in fake_model["seen"]["user"]
    assert "why am I on warfarin?" in fake_model["seen"]["user"]
    assert "NEVER tell them to start, stop" in fake_model["seen"]["system"]


def test_invented_citations_are_dropped(fake_model):
    fake_model["reply"] = _reply(citations=[
        {"kind": "med_item", "ref_id": "totally-made-up", "label": "Ghost Drug"},
        {"kind": "not_a_kind", "ref_id": "", "label": "Bad kind"},
    ])
    turn = _answer("what do I take?", fake_model)
    assert turn.citations == []


def test_real_citations_survive(fake_model):
    items = _items()
    fake_model["reply"] = _reply(citations=[
        {"kind": "med_item", "ref_id": items[0].id, "label": "Warfarin 5 mg"},
    ])
    record = {"items": items, "labs": [], "history": [], "observations": [], "findings": []}
    turn = asyncio.run(engine.answer("what do I take?", **record))

    assert len(turn.citations) == 1
    assert turn.citations[0].kind is CitationKind.med_item
    assert turn.citations[0].label == "Warfarin 5 mg"


def test_actions_against_unknown_targets_are_dropped(fake_model):
    fake_model["reply"] = _reply(actions=[
        {"kind": "update_item", "description": "Change a ghost", "target_id": "nope",
         "payload": {"dosage": "7.5 mg"}},
        {"kind": "add_item", "description": "Add something nameless", "target_id": "",
         "payload": {}},
    ])
    assert _answer("they changed something", fake_model).actions == []


def test_post_visit_report_proposes_changes_without_applying_them(fake_model):
    items = _items()
    fake_model["reply"] = _reply(
        answer="Noted. I've put those as changes for you to confirm.",
        actions=[
            {"kind": "update_item", "description": "Change Warfarin from 5 mg to 7.5 mg daily",
             "target_id": items[0].id, "payload": {"dosage": "7.5 mg"}},
            {"kind": "add_item", "description": "Add Vitamin K 100 mcg",
             "target_id": "", "payload": {"kind": "supplement", "name": "Vitamin K",
                                          "dosage": "100 mcg"}},
        ],
    )
    record = {"items": items, "labs": [], "history": [], "observations": [], "findings": []}
    turn = asyncio.run(engine.answer("they upped the warfarin to 7.5", **record))

    assert [a.kind for a in turn.actions] == [ActionKind.update_item, ActionKind.add_item]
    assert all(not a.applied for a in turn.actions)  # proposed only


def test_guard_notice_is_attached_not_substituted(fake_model):
    fake_model["reply"] = _reply(answer="I recommend you stop taking the fish oil.")
    turn = _answer("should I stop the fish oil?", fake_model)

    assert turn.guard_notice  # flagged
    assert turn.text == "I recommend you stop taking the fish oil."  # not silently rewritten


# --------------------------------------------------------------------- API

@pytest.fixture
def client(tmp_path, monkeypatch, fake_model):
    monkeypatch.setenv("DB_PATH", str(tmp_path / "chat.sqlite3"))
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key")
    get_settings.cache_clear()
    yield TestClient(app)
    get_settings.cache_clear()


def test_conversation_persists_in_order(client):
    client.post("/api/chat", json={"message": "why am I on warfarin?"})
    client.post("/api/chat", json={"message": "and what about the fish oil?"})

    turns = client.get("/api/chat").json()["turns"]
    assert [t["role"] for t in turns] == ["user", "assistant", "user", "assistant"]
    assert turns[0]["text"] == "why am I on warfarin?"


def test_applying_an_action_changes_the_list_and_is_idempotent(client, fake_model):
    created = client.post("/api/list/items",
                          json={"kind": "medicine", "name": "Warfarin", "dosage": "5 mg"}).json()
    item_id = created["id"]

    fake_model["reply"] = _reply(
        answer="Noted.",
        actions=[{"kind": "update_item", "description": "Change Warfarin to 7.5 mg",
                  "target_id": item_id, "payload": {"dosage": "7.5 mg"}}],
    )
    turn = client.post("/api/chat", json={"message": "they upped it to 7.5"}).json()["turn"]
    action_id = turn["actions"][0]["id"]

    # Nothing changed until it is confirmed.
    assert client.get("/api/list").json()["items"][0]["dosage"] == "5 mg"

    applied = client.post(f"/api/chat/actions/{action_id}/apply")
    assert applied.status_code == 200
    assert client.get("/api/list").json()["items"][0]["dosage"] == "7.5 mg"

    # Applying twice is refused rather than silently repeating.
    assert client.post(f"/api/chat/actions/{action_id}/apply").status_code == 409
    assert client.post("/api/chat/actions/unknown/apply").status_code == 404


def test_action_can_answer_an_open_panel_question(client, fake_model):
    from app.storage.med_list import MedListStore

    store = MedListStore(get_settings().db_path)
    observation = TrackedObservation(text="An INR compared against the previous one.")
    store.save_observations([observation])

    fake_model["reply"] = _reply(
        answer="Noted.",
        actions=[{"kind": "answer_observation", "description": "Record INR 2.4",
                  "target_id": observation.id, "payload": {"answer": "2.4, unchanged"}}],
    )
    turn = client.post("/api/chat", json={"message": "INR came back 2.4"}).json()["turn"]
    client.post(f"/api/chat/actions/{turn['actions'][0]['id']}/apply")

    answered = client.get("/api/observations").json()["observations"][0]
    assert answered["status"] == "answered" and answered["answer"] == "2.4, unchanged"


def test_chat_clear_leaves_the_record_alone(client):
    client.post("/api/list/items", json={"kind": "medicine", "name": "Warfarin"})
    client.post("/api/chat", json={"message": "hello"})

    client.delete("/api/chat")
    assert client.get("/api/chat").json()["turns"] == []
    assert len(client.get("/api/list").json()["items"]) == 1


def test_reset_clears_the_conversation_too(client):
    client.post("/api/list/items", json={"kind": "medicine", "name": "Warfarin"})
    client.post("/api/chat", json={"message": "hello"})

    client.post("/api/reset")
    assert client.get("/api/chat").json()["turns"] == []


def test_chat_requires_a_key(client, monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "")
    get_settings.cache_clear()
    res = client.post("/api/chat", json={"message": "hi"})
    assert res.status_code == 503 and "API key" in res.json()["detail"]


def test_the_versioned_path_serves_the_same_api(client):
    """A native client targets /api/v1 so a future break can ship as /api/v2
    without stranding installed apps."""
    client.post("/api/list/items", json={"kind": "medicine", "name": "Warfarin"})

    assert client.get("/api/v1/health").json()["ok"] is True
    assert len(client.get("/api/v1/list").json()["items"]) == 1
    assert client.post("/api/v1/chat", json={"message": "hi"}).status_code == 200
