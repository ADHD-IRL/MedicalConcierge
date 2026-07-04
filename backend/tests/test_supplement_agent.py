import pytest

import app.agents.common as common
from app.agents.supplement_agent import process_supplement_document
from app.normalization.rxnorm_client import RxNormClient
from app.schemas import ExtractedItem, RxNormMatch, SourceType


def _fake_extract(images, record_kind, source_type=SourceType.other):
    return [
        ExtractedItem(
            raw_text="Ashwagandha 600mg",
            name_as_written="ashwagandha",
            dosage="600 mg",
            source_type=source_type,
            extraction_confidence=0.85,
            ambiguities=[],
        )
    ]


@pytest.mark.asyncio
async def test_falls_back_to_local_supplement_table_when_rxnorm_weak(monkeypatch):
    monkeypatch.setattr(common, "extract_records", _fake_extract)

    async def weak_rxnorm_match(self, name):
        return RxNormMatch(rxcui=None, canonical_name=None, match_score=10.0, normalization_confidence=0.1)

    monkeypatch.setattr(RxNormClient, "find_best_match", weak_rxnorm_match)

    records = await process_supplement_document("bottle.jpg", b"fake-image-bytes")

    assert len(records) == 1
    record = records[0]
    assert record.normalization.source == "local_supplement_table"
    assert record.normalization.canonical_name == "Ashwagandha"
    assert record.overall_confidence == pytest.approx(0.85 * 0.75)


@pytest.mark.asyncio
async def test_uses_rxnorm_when_match_is_strong(monkeypatch):
    monkeypatch.setattr(common, "extract_records", _fake_extract)

    async def strong_rxnorm_match(self, name):
        return RxNormMatch(rxcui="99999", canonical_name="Ashwagandha Extract", match_score=95.0, normalization_confidence=0.95)

    monkeypatch.setattr(RxNormClient, "find_best_match", strong_rxnorm_match)

    records = await process_supplement_document("bottle.jpg", b"fake-image-bytes")

    assert records[0].normalization.source == "rxnorm"
    assert records[0].normalization.rxcui == "99999"
