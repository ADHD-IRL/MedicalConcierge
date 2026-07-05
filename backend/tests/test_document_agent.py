import pytest

import app.agents.common as common
from app.normalization.rxnorm_client import RxNormClient
from app.schemas import ExtractedItem, RecordKind, RxNormMatch, SourceType


def _fake_extract(images):
    return [
        ExtractedItem(
            raw_text="Metformin 500mg BID", name_as_written="Metformin",
            kind=RecordKind.medicine, dosage="500 mg", frequency="twice daily",
            source_type=SourceType.printed_document,
            extraction_confidence=0.95, ambiguities=[],
        ),
        ExtractedItem(
            raw_text="Ashwagandha 600mg", name_as_written="ashwagandha",
            kind=RecordKind.supplement, dosage="600 mg",
            source_type=SourceType.bottle_label,
            extraction_confidence=0.85, ambiguities=[],
        ),
    ]


@pytest.mark.asyncio
async def test_single_pass_normalizes_each_item_per_its_kind(monkeypatch):
    monkeypatch.setattr(common, "extract_records", _fake_extract)

    async def fake_match(self, name, context=None):
        if name == "Metformin":
            return RxNormMatch(rxcui="6809", canonical_name="metformin",
                               ingredient_rxcui="6809", ingredient_name="metformin",
                               match_score=100.0, normalization_confidence=1.0)
        # RxNorm doesn't know ashwagandha -> weak match triggers local fallback
        return RxNormMatch(match_score=10.0, normalization_confidence=0.1)

    monkeypatch.setattr(RxNormClient, "find_best_match", fake_match)

    records = await common.process_document("chart.jpg", b"fake-bytes")

    assert [r.kind for r in records] == [RecordKind.medicine, RecordKind.supplement]
    assert records[0].normalization.rxcui == "6809"
    assert records[0].overall_confidence == 0.95
    assert records[1].normalization.source == "local_supplement_table"
    assert records[1].normalization.canonical_name == "Ashwagandha"
    assert all(r.source_filename == "chart.jpg" for r in records)
