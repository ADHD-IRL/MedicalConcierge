import pytest

from app.schemas import (
    ExtractedItem,
    ItemStatus,
    MedListItem,
    NormalizedRecord,
    RecordKind,
    RxNormMatch,
    SourceType,
)
from app.storage.med_list import MedListStore, item_to_record


@pytest.fixture
def store(tmp_path):
    return MedListStore(str(tmp_path / "test.sqlite3"))


def _record(name, canonical, rxcui, dose="500 mg", src="doc.pdf"):
    return NormalizedRecord(
        kind=RecordKind.medicine,
        extracted=ExtractedItem(
            raw_text=f"{name} {dose}", name_as_written=name, dosage=dose,
            frequency="daily", source_type=SourceType.printed_document,
            extraction_confidence=0.9, ambiguities=[],
        ),
        normalization=RxNormMatch(
            rxcui=rxcui, canonical_name=canonical, match_score=100.0,
            normalization_confidence=1.0,
        ),
        overall_confidence=0.9, needs_review=False, source_filename=src,
    )


def test_sync_creates_items_and_original_baseline(store):
    created = store.sync_from_records([_record("Metformin", "metformin", "6809")])

    assert len(created) == 1
    assert store.list_items()[0].canonical_name == "metformin"
    baselines = store.list_baselines()
    assert len(baselines) == 1
    assert baselines[0].name == "Original baseline"
    assert len(baselines[0].items) == 1


def test_resync_same_medicine_logs_observed_not_duplicate(store):
    store.sync_from_records([_record("Metformin", "metformin", "6809")])
    created = store.sync_from_records(
        [_record("Glucophage", "metformin", "6809", dose="850 mg", src="visit2.pdf")]
    )

    assert created == []
    assert len(store.list_items()) == 1
    events = store.history(store.list_items()[0].id)
    observed = [e for e in events if e.action == "observed"]
    assert len(observed) == 1
    assert "visit2.pdf" in observed[0].detail
    assert "850 mg" in observed[0].detail  # dose difference is called out


def test_update_writes_history_and_changes_fields(store):
    item = store.add_item(MedListItem(kind=RecordKind.medicine, name="Lisinopril"))

    updated = store.update_item(item.id, {"dosage": "10 mg", "notes": "per Dr. Chen"})

    assert updated.dosage == "10 mg"
    events = store.history(item.id)
    assert events[0].action == "updated"
    assert "10 mg" in events[0].detail


def test_stop_and_reactivate_are_distinct_history_actions(store):
    item = store.add_item(MedListItem(kind=RecordKind.medicine, name="Ibuprofen"))

    store.update_item(item.id, {"status": "stopped"})
    store.update_item(item.id, {"status": "active"})

    actions = [e.action for e in store.history(item.id)]
    assert actions[:2] == ["reactivated", "stopped"]  # newest first


def test_no_op_update_writes_no_history(store):
    item = store.add_item(MedListItem(kind=RecordKind.medicine, name="X", dosage="5 mg"))
    before = len(store.history(item.id))

    store.update_item(item.id, {"dosage": "5 mg"})

    assert len(store.history(item.id)) == before


def test_baseline_diff_added_stopped_changed(store):
    a = store.add_item(MedListItem(kind=RecordKind.medicine, name="Metformin", dosage="500 mg"))
    b = store.add_item(MedListItem(kind=RecordKind.medicine, name="Lisinopril", dosage="10 mg"))
    c = store.add_item(MedListItem(kind=RecordKind.supplement, name="Fish Oil"))
    baseline = store.create_baseline("Before cardiology visit")

    store.update_item(a.id, {"dosage": "850 mg"})          # changed
    store.update_item(b.id, {"status": "stopped"})          # stopped
    store.add_item(MedListItem(kind=RecordKind.medicine, name="Clopidogrel"))  # added

    diff = store.compare_to_baseline(baseline.id)

    assert [i.name for i in diff.added] == ["Clopidogrel"]
    assert [i.name for i in diff.stopped] == ["Lisinopril"]
    assert len(diff.changed) == 1
    change = diff.changed[0].changes[0]
    assert (change.field, change.before, change.after) == ("dosage", "500 mg", "850 mg")
    assert diff.unchanged_count == 1  # fish oil untouched


def test_item_to_record_carries_identity_for_screening(store):
    item = MedListItem(
        kind=RecordKind.medicine, name="Eliquis", canonical_name="apixaban",
        rxcui="1364430", dosage="5 mg", frequency="twice daily",
    )

    record = item_to_record(item)

    assert record.normalization.canonical_name == "apixaban"
    assert record.overall_confidence == 1.0
    assert record.needs_review is False
    assert record.id == item.id
