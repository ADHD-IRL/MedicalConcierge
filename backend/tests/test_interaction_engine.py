from app.interactions.engine import evaluate
from app.schemas import (
    ExtractedItem,
    FindingCategory,
    FindingSeverity,
    NormalizedRecord,
    RecordKind,
    RxNormMatch,
    SourceType,
)


def _rec(kind, name, canonical=None, confidence=0.9, needs_review=False, src="doc.pdf"):
    return NormalizedRecord(
        kind=kind,
        extracted=ExtractedItem(
            raw_text=name,
            name_as_written=name,
            source_type=SourceType.printed_document,
            extraction_confidence=confidence,
            ambiguities=[],
        ),
        normalization=RxNormMatch(
            rxcui=None,
            canonical_name=canonical,
            match_score=100.0 if canonical else 0.0,
            normalization_confidence=1.0 if canonical else 0.0,
        ),
        overall_confidence=confidence,
        needs_review=needs_review,
        source_filename=src,
    )


def _by_rule(findings, rule_id):
    return [f for f in findings if f.rule_id == rule_id]


def test_ace_inhibitor_plus_potassium_is_major():
    findings = evaluate([
        _rec(RecordKind.medicine, "Lisinopril", "lisinopril"),
        _rec(RecordKind.supplement, "Potassium", "Potassium"),
    ])

    hits = _by_rule(findings, "ace-potassium-supp")
    assert len(hits) == 1
    f = hits[0]
    assert f.severity == FindingSeverity.major
    assert f.category == FindingCategory.drug_supplement
    assert set(f.involved) == {"lisinopril", "Potassium"}


def test_st_johns_wort_plus_ssri_is_major():
    findings = evaluate([
        _rec(RecordKind.medicine, "Sertraline", "sertraline"),
        _rec(RecordKind.supplement, "St. John's Wort", "St. John's Wort"),
    ])

    assert len(_by_rule(findings, "sjw-ssri")) == 1


def test_metformin_depletion_recommends_b12_when_not_taking_it():
    findings = evaluate([_rec(RecordKind.medicine, "Metformin", "metformin")])

    hits = _by_rule(findings, "metformin-b12")
    assert len(hits) == 1
    assert hits[0].severity == FindingSeverity.info
    assert hits[0].category == FindingCategory.depletion
    assert "checking B12" in hits[0].recommendation


def test_metformin_depletion_changes_tone_when_already_supplementing():
    findings = evaluate([
        _rec(RecordKind.medicine, "Metformin", "metformin"),
        _rec(RecordKind.supplement, "B12", "Vitamin B12 (Cobalamin)"),
    ])

    hits = _by_rule(findings, "metformin-b12")
    assert len(hits) == 1
    assert "already take" in hits[0].explanation


def test_levothyroxine_plus_calcium_gives_timing_recommendation():
    findings = evaluate([
        _rec(RecordKind.medicine, "Levothyroxine", "levothyroxine"),
        _rec(RecordKind.supplement, "Calcium", "Calcium"),
    ])

    hits = _by_rule(findings, "levothyroxine-minerals")
    assert len(hits) == 1
    assert "4 hours" in hits[0].recommendation


def test_two_nsaids_flagged_as_duplicate_class():
    findings = evaluate([
        _rec(RecordKind.medicine, "Ibuprofen", "ibuprofen"),
        _rec(RecordKind.medicine, "Naproxen", "naproxen"),
    ])

    dups = [f for f in findings if f.category == FindingCategory.duplicate]
    assert len(dups) == 1
    assert set(dups[0].involved) == {"ibuprofen", "naproxen"}


def test_same_medicine_in_two_documents_flagged():
    findings = evaluate([
        _rec(RecordKind.medicine, "Metformin", "metformin", src="visit_a.pdf"),
        _rec(RecordKind.medicine, "Metformin", "metformin", src="visit_b.pdf"),
    ])

    dups = [f for f in findings if f.rule_id.startswith("duplicate-entry")]
    assert len(dups) == 1


def test_clean_combination_produces_no_interaction_findings():
    findings = evaluate([
        _rec(RecordKind.medicine, "Levothyroxine", "levothyroxine"),
        _rec(RecordKind.supplement, "Vitamin C", "Vitamin C (Ascorbic Acid)"),
    ])

    interactions = [
        f for f in findings
        if f.category in (FindingCategory.drug_drug, FindingCategory.drug_supplement)
    ]
    assert interactions == []


def test_findings_sorted_major_first_and_confidence_propagates():
    findings = evaluate([
        _rec(RecordKind.medicine, "Warfarin", "warfarin", confidence=0.9),
        _rec(RecordKind.medicine, "Ibuprofen", "ibuprofen", confidence=0.5, needs_review=True),
        _rec(RecordKind.medicine, "Omeprazole", "omeprazole", confidence=0.95),
    ])

    assert findings[0].severity == FindingSeverity.major  # warfarin+ibuprofen
    assert findings[0].reading_confidence == 0.5
    assert findings[0].needs_record_review is True
    severities = [f.severity for f in findings]
    assert severities == sorted(
        severities, key=lambda s: {"major": 0, "moderate": 1, "info": 2}[s.value]
    )


def test_word_boundary_matching_avoids_substring_false_positives():
    # "Ironwood Herbal Blend" must not match the "iron" mineral term.
    findings = evaluate([
        _rec(RecordKind.medicine, "Levothyroxine", "levothyroxine"),
        _rec(RecordKind.supplement, "Ironwood Herbal Blend", None),
    ])

    assert _by_rule(findings, "levothyroxine-minerals") == []
