from app.schemas import ExtractedItem, NormalizedRecord, RecordKind, RxNormMatch, SourceType


def _item(confidence: float) -> ExtractedItem:
    return ExtractedItem(
        raw_text="Tylenol 500mg twice daily",
        name_as_written="Tylenol",
        dosage="500 mg",
        frequency="twice daily",
        source_type=SourceType.bottle_label,
        extraction_confidence=confidence,
        ambiguities=[],
    )


def test_overall_confidence_is_multiplicative():
    item = _item(0.9)
    match = RxNormMatch(rxcui="161", canonical_name="acetaminophen", match_score=90, normalization_confidence=0.9)

    record = NormalizedRecord.build(kind=RecordKind.medicine, extracted=item, normalization=match)

    assert record.overall_confidence == 0.81
    assert record.needs_review is False


def test_low_confidence_flags_needs_review():
    item = _item(0.4)
    match = RxNormMatch(rxcui=None, canonical_name=None, match_score=0, normalization_confidence=0.0)

    record = NormalizedRecord.build(kind=RecordKind.medicine, extracted=item, normalization=match)

    assert record.overall_confidence == 0.0
    assert record.needs_review is True


def test_review_threshold_is_configurable():
    item = _item(0.8)
    match = RxNormMatch(rxcui="161", canonical_name="acetaminophen", match_score=70, normalization_confidence=0.7)

    record = NormalizedRecord.build(
        kind=RecordKind.medicine, extracted=item, normalization=match, review_threshold=0.9
    )

    assert record.overall_confidence == 0.56
    assert record.needs_review is True
