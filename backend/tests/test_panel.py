import fitz

import asyncio

import pytest

from app.panel import engine, llm
from app.panel.registry import load_registry
from app.panel.safety import screen
from app.panel.schemas import AccessTier, EvidenceGrade, Urgency
from app.schemas import ItemStatus, MedListItem, RecordKind


def _pdf_text(pdf_bytes: bytes) -> str:
    with fitz.open(stream=pdf_bytes, filetype="pdf") as doc:
        return "\n".join(page.get_text() for page in doc)


# ----------------------------------------------------------------- registry

def test_registry_is_internally_consistent():
    r = load_registry()
    assert len(r.agents) == 29
    assert len(r.seatable) == 26  # 29 less the three governance seats

    ids = set(r.agents)
    for agent in r.agents.values():
        assert agent.system_prompt and agent.stance and agent.bias_watch
        assert agent.concedes_when and agent.required_round_output
        for target in agent.typically_challenges:
            assert target in ids, f"{agent.id} challenges unknown agent {target}"

    # The rounds the engine implements must match the published protocol.
    assert [round_["n"] for round_ in r.protocol["rounds"]] == list(range(1, 9))

    # The one rule the whole design rests on.
    assert any("never tell the reader to start, stop" in rule.lower() for rule in r.house_rules)


def test_governance_seats_never_take_a_position():
    r = load_registry()
    assert {a.id for a in r.agents.values() if a.is_governance} == {
        "gov_moderator", "gov_synthesizer", "gov_safety"
    }
    assert all(not a.is_governance for a in r.seatable)


def test_adversarial_seats_exist_on_both_sides():
    """The panel is structurally biased toward finding problems with what is
    present; the undertreatment and alert-fatigue seats are the counterweight,
    so their absence would be a silent failure."""
    ids = set(load_registry().agents)
    assert {"red_overtreatment", "red_undertreatment", "red_alert_fatigue"} <= ids


# ------------------------------------------------------------------- safety

@pytest.mark.parametrize(
    "note,expected",
    [
        ("", Urgency.clear),
        ("I've been a bit tired since the spring", Urgency.clear),
        ("my chest pain started this morning", Urgency.urgent),
        ("I think I overdosed", Urgency.urgent),
        ("new bruising all over my arms", Urgency.concern),
        ("I keep feeling faint when I stand", Urgency.concern),
        ("I stopped taking it, I can't afford it", Urgency.sensitive),
        ("I'm pregnant", Urgency.sensitive),
    ],
)
def test_urgency_screen_grades(note, expected):
    assert screen(note).level is expected


def test_urgent_screen_names_what_matched():
    result = screen("chest pain and I can't breathe")
    assert result.level is Urgency.urgent
    assert result.matched
    assert "911" in result.message


def test_screen_does_not_grade_the_regimen_itself():
    """A dangerous combination is a finding for the panel to reason about,
    not a reason to refuse to run."""
    assert screen("I take warfarin and fish oil").level is Urgency.clear


# ------------------------------------------------------------- mocked rounds

def _items():
    return [
        MedListItem(kind=RecordKind.medicine, name="Warfarin", dosage="5 mg", frequency="daily"),
        MedListItem(kind=RecordKind.supplement, name="Fish Oil", dosage="1000 mg"),
        MedListItem(kind=RecordKind.medicine, name="Old Pill", status=ItemStatus.stopped),
    ]


def _fake_structured_for(schema):
    """Dispatch on the schema's shape, so each round gets a plausible payload."""
    required = set(schema.get("required", []))

    if "seated" in required:
        return {
            "framing": "Two agents with additive bleeding risk.",
            "unknowns": ["No INR value is visible to this panel."],
            "seated": [
                {"agent_id": "pharm_clinical", "reason": "Reads the regimen as one system."},
                {"agent_id": "med_cardiology", "reason": "Anticoagulation is the live risk."},
                {"agent_id": "red_alert_fatigue", "reason": "Will argue this is over-flagged."},
                {"agent_id": "ld_patient", "reason": "Says how this is actually taken."},
                {"agent_id": "nope_invented", "reason": "Should be dropped as invented."},
            ],
            "not_seated": [
                {"agent_id": "med_repro", "reason": "Not relevant here."},
                {"agent_id": "psy_pharm", "reason": "No psychotropics on the list."},
            ],
        }
    if "exchanges" in required:
        return {"exchanges": [
            {"from_id": "red_alert_fatigue", "to_id": "pharm_clinical", "about": "whether this is clinically meaningful"},
            {"from_id": "pharm_clinical", "to_id": "pharm_clinical", "about": "self-pair, must be dropped"},
            {"from_id": "ghost", "to_id": "pharm_clinical", "about": "invented, must be dropped"},
        ]}
    if "kept" in required:
        return {
            "kept": [{
                "title": "Additive bleeding risk",
                "detail": "Both thin the blood by different mechanisms.",
                "involved": ["Warfarin", "Fish Oil"],
                "raised_by": ["pharm_clinical", "med_cardiology"],
                "evidence_grade": "pharmacokinetic",
                "confidence": 0.7,
                "data_caveat": "",
            }],
            "dropped": [{
                "title": "Vitamin K variation",
                "reason": "Intake is consistent; no evidence it matters here.",
                "dropped_by": ["red_alert_fatigue"],
            }],
        }
    if "tiering_notes" in required:
        return {"tiering_notes": ["A pharmacist can answer this for free."], "ethical_flags": []}
    if "headline" in required and "dissent" in required:
        return {
            "headline": "One combination is worth asking your pharmacist about.",
            "summary": "The panel agreed on one thing and split on another.",
            "discriminating_observations": ["Whether bruising appears after starting fish oil."],
            "questions": [
                {"question": "Does my fish oil change how my warfarin works?", "why": "Settles the main concern.",
                 "tier": "pharmacist", "about": ["Warfarin", "Fish Oil"]},
                {"question": "Should my INR be checked sooner?", "why": "Would measure it directly.",
                 "tier": "appointment", "about": ["Warfarin"]},
            ],
            "dissent": [{
                "topic": "Whether this needs action at all",
                "side_a": "Clinically meaningful.", "side_a_agents": ["pharm_clinical"],
                "side_b": "Inert at this dose.", "side_b_agents": ["red_alert_fatigue", "ghost_agent"],
                "what_would_settle_it": "An INR drawn after four weeks.",
            }],
            "what_this_cannot_tell": ["Whether your INR is currently in range."],
            "correlated_model_note": "These experts all come from one model, so agreement is weak evidence.",
        }
    if "released" in required:
        return {
            "released": True, "block_reason": "",
            "headline": "One combination is worth asking your pharmacist about.",
            "summary": "The panel agreed on one thing and split on another.",
            "rewrites": ["Rephrased one sentence that read as an instruction."],
            "urgent_note": "",
        }
    raise AssertionError(f"unexpected schema: {required}")


@pytest.fixture
def mocked_llm(monkeypatch):
    calls = {"structured": 0, "prose": 0}

    async def fake_structured(system, user, schema, *, gate, max_tokens=4000, model=None):
        calls["structured"] += 1
        return _fake_structured_for(schema)

    async def fake_prose(system, user, *, gate, max_tokens=1200, model=None):
        calls["prose"] += 1
        return "A short, specific take about this regimen."

    monkeypatch.setattr(llm, "structured", fake_structured)
    monkeypatch.setattr(llm, "prose", fake_prose)
    return calls


def test_full_panel_run(mocked_llm):
    review = asyncio.run(engine.run_panel(_items(), [], [], note="a bit tired"))

    assert not review.halted
    assert review.headline and review.summary
    assert review.registry_version

    # Invented agent ids are dropped at every boundary they can enter.
    assert [s.agent_id for s in review.seated] == [
        "pharm_clinical", "med_cardiology", "red_alert_fatigue", "ld_patient"
    ]
    assert all(s.name for s in review.seated)
    assert "ghost_agent" not in review.dissent[0].side_b_agents
    assert review.dissent[0].side_b_agents == ["red_alert_fatigue"]

    # Self-pairs and invented speakers never produce a challenge.
    assert len(review.challenges) == 1
    assert review.challenges[0].agent_id == "red_alert_fatigue"

    assert len(review.takes) == 4
    assert review.concerns[0].evidence_grade is EvidenceGrade.pharmacokinetic
    assert review.dropped[0].title == "Vitamin K variation"
    assert review.questions[1].tier is AccessTier.appointment
    assert review.correlated_model_note
    assert review.notices  # the veto's rewrite was carried through


def test_panel_emits_progress_events(mocked_llm):
    events = []
    asyncio.run(engine.run_panel(_items(), [], [], note="", emit=events.append))

    rounds = [e["n"] for e in events if e["type"] == "round"]
    assert rounds == [1, 2, 3, 4, 5, 6, 7, 8]
    assert events[-1]["type"] == "done"
    assert {e["type"] for e in events} >= {"panel", "take-start", "take-end", "challenge-start"}


def test_urgent_note_halts_before_any_api_call(monkeypatch):
    def explode(*a, **k):
        raise AssertionError("no API call may happen on an urgent note")

    monkeypatch.setattr(llm, "structured", explode)
    monkeypatch.setattr(llm, "prose", explode)

    review = asyncio.run(engine.run_panel(_items(), [], [], note="I have chest pain"))
    assert review.halted and review.halt_reason == "urgent"
    assert review.urgency is Urgency.urgent
    assert "911" in review.urgency_message


def test_empty_list_halts_without_calling_the_model(monkeypatch):
    monkeypatch.setattr(llm, "structured", lambda *a, **k: pytest.fail("should not run"))
    stopped = [MedListItem(kind=RecordKind.medicine, name="Old", status=ItemStatus.stopped)]
    review = asyncio.run(engine.run_panel(stopped, [], [], note=""))
    assert review.halted and review.halt_reason == "empty-list"


def test_agent_failure_does_not_sink_the_run(monkeypatch, mocked_llm):
    calls = {"n": 0}
    original = llm.prose

    async def flaky(system, user, *, gate, max_tokens=1200, model=None):
        calls["n"] += 1
        if calls["n"] == 1:
            raise RuntimeError("upstream hiccup")
        return await original(system, user, gate=gate, max_tokens=max_tokens, model=model)

    monkeypatch.setattr(llm, "prose", flaky)
    review = asyncio.run(engine.run_panel(_items(), [], [], note=""))

    assert not review.halted
    assert len(review.takes) == 3  # one seat dropped out
    assert any("could not answer" in n for n in review.notices)


def test_agent_halt_token_stops_the_run(monkeypatch, mocked_llm):
    async def alarmed(system, user, *, gate, max_tokens=1200, model=None):
        return engine.HALT_TOKEN

    monkeypatch.setattr(llm, "prose", alarmed)
    review = asyncio.run(engine.run_panel(_items(), [], [], note=""))

    assert review.halted and review.halt_reason == "agent-flagged-urgent"
    assert review.urgency is Urgency.concern


def test_safety_veto_can_block_release(monkeypatch, mocked_llm):
    original = llm.structured

    async def vetoing(system, user, schema, *, gate, max_tokens=4000, model=None):
        if "released" in set(schema.get("required", [])):
            return {"released": False, "block_reason": "Read as an instruction to stop a drug.",
                    "headline": "", "summary": "", "rewrites": [], "urgent_note": ""}
        return await original(system, user, schema, gate=gate, max_tokens=max_tokens, model=model)

    monkeypatch.setattr(llm, "structured", vetoing)
    review = asyncio.run(engine.run_panel(_items(), [], [], note=""))

    assert review.halted and review.halt_reason == "safety-veto"
    assert "instruction" in review.urgency_message


def test_veto_urgent_note_raises_urgency(monkeypatch, mocked_llm):
    original = llm.structured

    async def urgent(system, user, schema, *, gate, max_tokens=4000, model=None):
        result = await original(system, user, schema, gate=gate, max_tokens=max_tokens, model=model)
        if "released" in set(schema.get("required", [])):
            result["urgent_note"] = "Call a pharmacist today about the bleeding risk."
        return result

    monkeypatch.setattr(llm, "structured", urgent)
    review = asyncio.run(engine.run_panel(_items(), [], [], note=""))

    assert review.urgency is Urgency.concern
    assert "pharmacist today" in review.urgency_message


def test_regimen_brief_carries_provenance():
    from app.panel.prompts import regimen_brief
    from app.schemas import ExtractedItem, NormalizedRecord, RxNormMatch

    record = NormalizedRecord.build(
        kind=RecordKind.medicine,
        extracted=ExtractedItem(kind=RecordKind.medicine, raw_text="Warfrin 5mg",
                                name_as_written="Warfrin", extraction_confidence=0.4),
        normalization=RxNormMatch(rxcui="11289", canonical_name="warfarin", match_score=90.0,
                                  normalization_confidence=0.9),
        review_threshold=0.6,
        source_filename="chart.jpg",
    )
    item = MedListItem(kind=RecordKind.medicine, name="Warfrin", canonical_name="warfarin",
                       source_record_id=record.id)

    brief = regimen_brief([item], [record], [], "")
    assert "FLAGGED FOR REVIEW" in brief
    assert "NO DOSE RECORDED" in brief


# ----------------------------------------------------------------- API + PDF

@pytest.fixture
def api_client(tmp_path, monkeypatch, mocked_llm):
    from fastapi.testclient import TestClient

    from app.config import get_settings
    from app.main import app

    monkeypatch.setenv("DB_PATH", str(tmp_path / "panel.sqlite3"))
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key")
    get_settings.cache_clear()
    yield TestClient(app)
    get_settings.cache_clear()


def _sse_events(response):
    return [
        __import__("json").loads(line[6:])
        for line in response.text.split("\n\n")
        if line.startswith("data: ")
    ]


def test_roster_endpoint_exposes_the_panel(api_client):
    body = api_client.get("/api/panel/roster").json()

    assert len(body["agents"]) == 29
    assert len(body["rounds"]) == 8
    assert body["house_rules"]
    skeptic = next(a for a in body["agents"] if a["id"] == "red_alert_fatigue")
    assert skeptic["bias_watch"] and skeptic["concedes_when"]
    assert not skeptic["governance"]


def test_panel_streams_rounds_then_the_review(api_client):
    api_client.post("/api/list/items", json={"kind": "medicine", "name": "Warfarin", "dosage": "5 mg"})
    api_client.post("/api/list/items", json={"kind": "supplement", "name": "Fish Oil"})

    res = api_client.post("/api/panel", json={"note": "bruising a bit"})
    assert res.status_code == 200
    assert res.headers["content-type"].startswith("text/event-stream")

    events = _sse_events(res)
    assert [e["n"] for e in events if e["type"] == "round"] == [1, 2, 3, 4, 5, 6, 7, 8]
    review = next(e["review"] for e in events if e["type"] == "review")
    assert review["headline"] and review["dissent"]

    # Completed reviews survive a reload.
    assert api_client.get("/api/panel/latest").json()["review"]["id"] == review["id"]


def test_panel_pdf_leads_with_questions_and_keeps_dissent(api_client):
    api_client.post("/api/list/items", json={"kind": "medicine", "name": "Warfarin", "dosage": "5 mg"})
    assert api_client.get("/api/panel/pdf").status_code == 404  # nothing to export yet

    api_client.post("/api/panel", json={"note": ""})
    res = api_client.get("/api/panel/pdf")

    assert res.status_code == 200
    assert "panel_review_" in res.headers["content-disposition"]
    text = _pdf_text(res.content)
    assert "Expert Panel Review" in text
    assert "QUESTIONS THE PATIENT WANTS TO ASK" in text
    assert "WHERE THE PANEL DID NOT AGREE" in text
    assert "weaker evidence" in text  # the correlated-model caveat survives into print
    assert "Warfarin" in text


def test_panel_requires_a_key(api_client, monkeypatch):
    from app.config import get_settings

    monkeypatch.setenv("ANTHROPIC_API_KEY", "")
    get_settings.cache_clear()
    res = api_client.post("/api/panel", json={"note": ""})
    assert res.status_code == 503
    assert "API key" in res.json()["detail"]


def test_reset_clears_panel_reviews(api_client):
    api_client.post("/api/list/items", json={"kind": "medicine", "name": "Warfarin"})
    api_client.post("/api/panel", json={"note": ""})
    assert api_client.get("/api/panel/latest").json()["review"]

    api_client.post("/api/reset")
    assert api_client.get("/api/panel/latest").json()["review"] is None


def test_halted_review_is_not_persisted(api_client):
    api_client.post("/api/list/items", json={"kind": "medicine", "name": "Warfarin"})
    res = api_client.post("/api/panel", json={"note": "I have chest pain"})

    events = _sse_events(res)
    review = next(e["review"] for e in events if e["type"] == "review")
    assert review["halted"] and review["urgency"] == "urgent"
    assert api_client.get("/api/panel/latest").json()["review"] is None
