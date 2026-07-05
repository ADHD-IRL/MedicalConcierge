import fitz

from app.export.pdf_report import build_pdf
from app.schemas import ExtractedItem, NormalizedRecord, RecordKind, RxNormMatch, SourceType


def _record(kind, name, canonical, confidence, needs_review=False, ambiguities=None):
    return NormalizedRecord(
        kind=kind,
        extracted=ExtractedItem(
            raw_text=f"{name} 500mg",
            name_as_written=name,
            dosage="500 mg",
            frequency="twice daily",
            source_type=SourceType.printed_document,
            extraction_confidence=confidence,
            ambiguities=ambiguities or [],
        ),
        normalization=RxNormMatch(
            rxcui="6809" if canonical else None,
            canonical_name=canonical,
            match_score=100.0,
            normalization_confidence=1.0,
        ),
        overall_confidence=confidence,
        needs_review=needs_review,
        source_filename="visit_note.pdf",
    )


def _all_text(pdf_bytes: bytes) -> str:
    with fitz.open(stream=pdf_bytes, filetype="pdf") as doc:
        return "\n".join(page.get_text() for page in doc)


def test_pdf_contains_sections_and_records():
    records = [
        _record(RecordKind.medicine, "Metformin", "metformin", 0.95),
        _record(
            RecordKind.medicine, "Lisinopril", "lisinopril", 0.45,
            needs_review=True, ambiguities=["Dose digit unclear - could be 10 or 40"],
        ),
        _record(RecordKind.supplement, "Magnesium Glycinate", "Magnesium Glycinate", 0.69),
    ]

    text = _all_text(build_pdf(records))

    assert "MEDICATIONS" in text
    assert "SUPPLEMENTS" in text
    assert "metformin" in text
    assert "Magnesium Glycinate" in text
    assert "PLEASE CONFIRM" in text
    assert "Dose digit unclear" in text
    assert "not medical advice" in text
    assert "Page 1 of" in text


def test_pdf_handles_empty_record_set():
    text = _all_text(build_pdf([]))

    assert "No medications recorded." in text
    assert "No supplements recorded." in text


def test_pdf_paginates_many_records():
    records = [
        _record(RecordKind.medicine, f"Drug{i:03d}", f"drug{i:03d}", 0.9) for i in range(60)
    ]
    pdf_bytes = build_pdf(records)

    with fitz.open(stream=pdf_bytes, filetype="pdf") as doc:
        assert len(doc) > 1
        text = "\n".join(page.get_text() for page in doc)

    assert "drug000" in text
    assert "drug059" in text
    assert f"Page {len(fitz.open(stream=pdf_bytes, filetype='pdf'))} of" in text
