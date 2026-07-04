import pytest

import app.agents.common as common
from app.agents.medicine_agent import process_medicine_document
from app.normalization.rxnorm_client import RxNormClient
from app.schemas import ExtractedItem, RxNormMatch, SourceType


def _fake_extract(images, record_kind, source_type=SourceType.other):
    return [
        ExtractedItem(
            raw_text="Metformin 500mg BID",
            name_as_written="Metformin",
            dosage="500 mg",
            frequency="twice daily",
            source_type=source_type,
            extraction_confidence=0.95,
            ambiguities=[],
        )
    ]


@pytest.mark.asyncio
async def test_process_medicine_document_end_to_end(monkeypatch):
    monkeypatch.setattr(common, "extract_records", _fake_extract)

    async def fake_find_best_match(self, name):
        assert name == "Metformin"
        return RxNormMatch(
            rxcui="6809",
            canonical_name="metformin",
            match_score=100.0,
            normalization_confidence=1.0,
        )

    monkeypatch.setattr(RxNormClient, "find_best_match", fake_find_best_match)

    records = await process_medicine_document("note.png", b"fake-image-bytes")

    assert len(records) == 1
    record = records[0]
    assert record.normalization.rxcui == "6809"
    assert record.overall_confidence == 0.95
    assert record.needs_review is False
    assert record.source_filename == "note.png"
