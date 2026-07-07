import fitz
import pytest
from fastapi.testclient import TestClient

from app.config import get_settings
from app.export.pdf_report import build_archive_pdf
from app.main import app
from app.normalization.rxnorm_client import RxNormClient
from app.schemas import ItemStatus, MedListItem, RecordKind, RxNormMatch
from app.storage.med_list import MedListStore
from app.storage.store import RecordStore


def _pdf_text(pdf_bytes: bytes) -> str:
    with fitz.open(stream=pdf_bytes, filetype="pdf") as doc:
        return "\n".join(page.get_text() for page in doc)


def test_clear_all_empties_both_stores(tmp_path):
    db = str(tmp_path / "t.sqlite3")
    ml = MedListStore(db)
    ml.add_item(MedListItem(kind=RecordKind.medicine, name="Metformin"))
    ml.create_baseline("extra")
    assert ml.list_items() and ml.list_baselines() and ml.history()

    ml.clear_all()
    RecordStore(db).clear_all()  # empty table, must not raise

    assert ml.list_items() == []
    assert ml.list_baselines() == []
    assert ml.history() == []


def test_archive_pdf_contains_everything_and_handles_empty(tmp_path):
    ml = MedListStore(str(tmp_path / "a.sqlite3"))
    item = ml.add_item(MedListItem(kind=RecordKind.medicine, name="Lisinopril", dosage="10 mg"))
    ml.update_item(item.id, {"dosage": "20 mg"})
    ml.update_item(item.id, {"status": "stopped"})
    ml.create_baseline("Before change")

    text = _pdf_text(build_archive_pdf(
        records=[], items=ml.list_items(), baselines=ml.list_baselines(),
        history=ml.history(), findings=[],
    ))

    assert "Full Archive" in text
    assert "[STOPPED]" in text and "Lisinopril" in text
    assert "Original baseline" in text and "Before change" in text
    assert "stopped:" in text and "'10 mg' -> '20 mg'" in text  # history detail
    assert "not medical advice" in text

    empty = _pdf_text(build_archive_pdf([], [], [], [], []))
    assert "The list was empty." in empty


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("DB_PATH", str(tmp_path / "api.sqlite3"))
    get_settings.cache_clear()

    async def fake_match(self, name, context=None):
        return RxNormMatch(rxcui="6809", canonical_name=name.lower(),
                           match_score=100.0, normalization_confidence=1.0)

    monkeypatch.setattr(RxNormClient, "find_best_match", fake_match)
    yield TestClient(app)
    get_settings.cache_clear()


def test_reset_returns_archive_then_wipes(client):
    client.post("/api/list/items", json={"kind": "medicine", "name": "Metformin", "dosage": "500 mg"})
    client.post("/api/list/items", json={"kind": "supplement", "name": "Fish Oil"})

    res = client.post("/api/reset")

    assert res.status_code == 200
    assert res.headers["content-type"] == "application/pdf"
    assert "medconcierge_archive_" in res.headers["content-disposition"]
    text = _pdf_text(res.content)
    assert "metformin" in text and "fish oil" in text
    assert "Full Archive" in text

    # everything is gone
    assert client.get("/api/list").json() == {"items": [], "baselines": []}
    assert client.get("/api/records").json()["records"] == []
    assert client.get("/api/findings").json()["findings"] == []
    assert client.get("/api/list/history").json()["events"] == []

    # and a fresh start re-creates the original baseline on the next add
    client.post("/api/list/items", json={"kind": "medicine", "name": "Aspirin"})
    baselines = client.get("/api/list").json()["baselines"]
    assert [b["name"] for b in baselines] == ["Original baseline"]
