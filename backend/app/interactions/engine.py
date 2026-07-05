"""Deterministic screening engine: evaluates the current record set against
the curated knowledge base and produces Findings.

Pure function of the records - no LLM, no network - so results are
reproducible, instantly recomputable after every ingest, and testable.
"""

from __future__ import annotations

import re

from app.interactions.knowledge_base import (
    DEPLETION_RULES,
    DUPLICATE_CLASSES,
    INTERACTION_RULES,
)
from app.schemas import Finding, FindingCategory, FindingSeverity, NormalizedRecord

_SEVERITY_ORDER = {
    FindingSeverity.major: 0,
    FindingSeverity.moderate: 1,
    FindingSeverity.info: 2,
}


def _display_name(record: NormalizedRecord) -> str:
    return record.normalization.canonical_name or record.extracted.name_as_written


def _searchable_text(record: NormalizedRecord) -> str:
    parts = [record.extracted.name_as_written]
    if record.normalization.canonical_name:
        parts.append(record.normalization.canonical_name)
    return " | ".join(parts).lower()


def _matches(record: NormalizedRecord, terms: tuple[str, ...]) -> bool:
    text = _searchable_text(record)
    for term in terms:
        if re.search(rf"(?<![a-z0-9]){re.escape(term)}(?![a-z0-9])", text):
            return True
    return False


def _make_finding(
    rule_id: str,
    severity: FindingSeverity,
    category: FindingCategory,
    title: str,
    explanation: str,
    recommendation: str,
    records: list[NormalizedRecord],
    evidence_note: str = "Well-documented interaction (built-in reference list).",
) -> Finding:
    return Finding(
        rule_id=rule_id,
        severity=severity,
        category=category,
        title=title,
        involved=[_display_name(r) for r in records],
        involved_record_ids=[r.id for r in records],
        explanation=explanation,
        recommendation=recommendation,
        evidence_note=evidence_note,
        reading_confidence=min((r.overall_confidence for r in records), default=1.0),
        needs_record_review=any(r.needs_review for r in records),
    )


def _interaction_findings(records: list[NormalizedRecord]) -> list[Finding]:
    findings: list[Finding] = []
    for rule in INTERACTION_RULES:
        a_side = [r for r in records if _matches(r, rule.a_terms)]
        b_side = [r for r in records if _matches(r, rule.b_terms)]
        seen_pairs: set[frozenset[str]] = set()
        for a in a_side:
            for b in b_side:
                if a.id == b.id:
                    continue
                pair = frozenset((a.id, b.id))
                if pair in seen_pairs:
                    continue
                seen_pairs.add(pair)
                findings.append(
                    _make_finding(
                        rule.rule_id, rule.severity, rule.category, rule.title,
                        rule.explanation, rule.recommendation, [a, b],
                    )
                )
    return findings


def _depletion_findings(records: list[NormalizedRecord]) -> list[Finding]:
    findings: list[Finding] = []
    for rule in DEPLETION_RULES:
        drugs = [r for r in records if _matches(r, rule.drug_terms)]
        if not drugs:
            continue
        already_supplementing = any(_matches(r, rule.nutrient_terms) for r in records)
        drug = drugs[0]
        if already_supplementing:
            explanation = (
                f"{rule.explanation} You already take something covering "
                f"{rule.nutrient} - good; periodic level checks are still worth asking about."
            )
            recommendation = (
                f"Mention at your next visit that you supplement {rule.nutrient} "
                f"alongside {_display_name(drug)}, so levels can be checked occasionally."
            )
        else:
            explanation = rule.explanation
            recommendation = rule.recommendation_if_missing
        findings.append(
            _make_finding(
                rule.rule_id,
                FindingSeverity.info,
                FindingCategory.depletion,
                f"{rule.drug_label} can lower {rule.nutrient}",
                explanation,
                recommendation,
                drugs,
                evidence_note=rule.evidence_note,
            )
        )
    return findings


def _duplicate_findings(records: list[NormalizedRecord]) -> list[Finding]:
    findings: list[Finding] = []

    # Same therapeutic class, different active ingredients. Records sharing
    # an ingredient RxCUI are the SAME drug (brand vs generic - e.g. Cozaar
    # and losartan) and must not be flagged as two class members.
    for class_label, terms in DUPLICATE_CLASSES.items():
        members = [r for r in records if _matches(r, terms)]
        distinct_names = {
            r.normalization.ingredient_rxcui or _display_name(r).lower() for r in members
        }
        if len(distinct_names) > 1:
            findings.append(
                _make_finding(
                    f"duplicate-class-{class_label}",
                    FindingSeverity.moderate,
                    FindingCategory.duplicate,
                    f"Two different {class_label} on the list",
                    "Two medicines from the same class usually means either an "
                    "intentional switch that was never cleaned up, or two doctors "
                    "prescribing without seeing each other's lists.",
                    "Confirm with your doctor or pharmacist which one you should "
                    "actually be taking - this is a classic uncoordinated-care issue.",
                    members,
                )
            )

    # The exact same ingredient appearing under multiple records (grouped by
    # ingredient RxCUI when known, so brand and generic entries group too).
    by_name: dict[str, list[NormalizedRecord]] = {}
    for r in records:
        key = r.normalization.ingredient_rxcui or _display_name(r).lower()
        by_name.setdefault(key, []).append(r)
    for name, group in by_name.items():
        sources = {r.source_filename for r in group}
        if len(group) > 1 and len(sources) > 1:
            findings.append(
                _make_finding(
                    f"duplicate-entry-{name}",
                    FindingSeverity.info,
                    FindingCategory.duplicate,
                    f"'{_display_name(group[0])}' appears in more than one document",
                    "The same medicine shows up in multiple source documents. That's "
                    "often fine (refills, repeat visits) but can also mean two "
                    "prescribers are both issuing it.",
                    "Check the doses/frequencies match across the entries; if they "
                    "differ, ask which is current.",
                    group,
                )
            )
    return findings


def evaluate(records: list[NormalizedRecord]) -> list[Finding]:
    """Screen the full record set. Returns findings sorted most-severe first,
    then by weakest reading confidence (shakier readings surface earlier
    within a severity band so the user verifies those records)."""

    findings = (
        _interaction_findings(records)
        + _duplicate_findings(records)
        + _depletion_findings(records)
    )
    findings.sort(key=lambda f: (_SEVERITY_ORDER[f.severity], f.reading_confidence))
    return findings
