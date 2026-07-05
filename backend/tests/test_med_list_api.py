import pytest
from fastapi.testclient import TestClient

from app.config import get_settings
from app.main import app
from app.normalization.rxnorm_client import RxNormClient
from app.schemas import RxNormMatch


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("DB_PATH", str(tmp_path / "api.sqlite3"))
    get_settings.cache_clear()

    async def fake_match(self, name):
        known = {"eliquis": ("1364430", "apixaban"), "ibuprofen": ("5640", "ibuprofen")}
        rxcui, canonical = known.get(name.strip().lower(), (None, None))
        if rxcui is None:
            return RxNormMatch(match_score=0.0, normalization_confidence=0.0)
        return RxNormMatch(rxcui=rxcui, canonical_name=canonical, match_score=100.0,
                           normalization_confidence=1.0)

    monkeypatch.setattr(RxNormClient, "find_best_match", fake_match)
    yield TestClient(app)
    get_settings.cache_clear()


def test_full_on_screen_flow(client):
    # add two items manually (normalized via mocked RxNorm)
    r1 = client.post("/api/list/items", json={"kind": "medicine", "name": "Eliquis", "dosage": "5 mg"})
    assert r1.status_code == 200
    assert r1.json()["canonical_name"] == "apixaban"
    r2 = client.post("/api/list/items", json={"kind": "medicine", "name": "Ibuprofen"})
    item2 = r2.json()

    # original baseline auto-created on first add
    data = client.get("/api/list").json()
    assert len(data["items"]) == 2
    assert data["baselines"][0]["name"] == "Original baseline"

    # original baseline was auto-set on the FIRST add, so ibuprofen shows as
    # added relative to it while still active
    original_id = data["baselines"][0]["id"]
    diff_before = client.get(f"/api/list/compare/{original_id}").json()
    assert [i["name"] for i in diff_before["added"]] == ["Ibuprofen"]

    # screening runs off the active list: anticoagulant + NSAID -> major
    findings = client.get("/api/findings").json()["findings"]
    assert any(f["rule_id"] == "anticoag-nsaid" for f in findings)

    # on-screen edit: stop ibuprofen -> warning clears
    patched = client.patch(f"/api/list/items/{item2['id']}", json={"status": "stopped"})
    assert patched.json()["status"] == "stopped"
    findings = client.get("/api/findings").json()["findings"]
    assert not any(f["rule_id"] == "anticoag-nsaid" for f in findings)

    # history recorded the stop
    events = client.get(f"/api/list/history?item_id={item2['id']}").json()["events"]
    assert events[0]["action"] == "stopped"

    # new baseline + compare: nothing changed since it was just set
    made = client.post("/api/baselines", json={"name": "After cleanup"})
    diff = client.get(f"/api/list/compare/{made.json()['id']}").json()
    assert diff["added"] == [] and diff["stopped"] == [] and diff["changed"] == []

    # against the ORIGINAL baseline, ibuprofen is now a net no-op: it was
    # added and stopped since, so it appears in neither bucket
    diff0 = client.get(f"/api/list/compare/{original_id}").json()
    assert diff0["added"] == [] and diff0["stopped"] == []
    assert diff0["unchanged_count"] == 1  # eliquis

    # full JSON export includes the list, baselines, and history
    export = client.get("/api/export?format=json").json()
    assert len(export["med_list"]) == 2
    assert len(export["baselines"]) == 2
    assert len(export["history"]) >= 3


def test_unknown_item_and_baseline_404(client):
    assert client.patch("/api/list/items/nope", json={"dosage": "1 mg"}).status_code == 404
    assert client.get("/api/list/compare/nope").status_code == 404
