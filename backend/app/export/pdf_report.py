"""Generates a clinician-readable PDF summary of all stored records.

Built with PyMuPDF (already a dependency for PDF ingestion), so no extra
packages. Layout is deliberately plain — black text, clear sections, flagged
items marked in red — because the audience is a doctor skimming it during a
short appointment, not a dashboard.
"""

from __future__ import annotations

from datetime import date

import fitz

from app.schemas import (
    Baseline,
    Finding,
    FindingSeverity,
    ItemStatus,
    LabResult,
    ListHistoryEvent,
    MedListItem,
    NormalizedRecord,
    RecordKind,
)

_PAGE_W, _PAGE_H = fitz.paper_size("letter")
_MARGIN = 54.0
_BOTTOM = _PAGE_H - 64.0
_TEXT_W = _PAGE_W - 2 * _MARGIN

_BLACK = (0.0, 0.0, 0.0)
_GRAY = (0.38, 0.38, 0.38)
_RED = (0.72, 0.11, 0.11)
_AMBER = (0.62, 0.42, 0.03)
_RULE = (0.80, 0.82, 0.85)

_SEVERITY_LABEL = {
    FindingSeverity.major: ("MAJOR", _RED),
    FindingSeverity.moderate: ("MODERATE", _AMBER),
    FindingSeverity.info: ("FYI", _GRAY),
}


def _wrap(text: str, fontname: str, size: float, max_width: float) -> list[str]:
    words = text.split()
    lines: list[str] = []
    current = ""
    for word in words:
        trial = f"{current} {word}".strip()
        if not current or fitz.get_text_length(trial, fontname=fontname, fontsize=size) <= max_width:
            current = trial
        else:
            lines.append(current)
            current = word
    if current:
        lines.append(current)
    return lines or [""]


class _Writer:
    def __init__(self) -> None:
        self.doc = fitz.open()
        self.page: fitz.Page
        self.y = 0.0
        self._new_page()

    def _new_page(self) -> None:
        self.page = self.doc.new_page(width=_PAGE_W, height=_PAGE_H)
        self.y = _MARGIN

    def _ensure(self, height: float) -> None:
        if self.y + height > _BOTTOM:
            self._new_page()

    def text(
        self,
        content: str,
        size: float = 10.0,
        bold: bool = False,
        color: tuple = _BLACK,
        indent: float = 0.0,
        gap: float = 3.0,
    ) -> None:
        fontname = "hebo" if bold else "helv"
        line_h = size * 1.25
        for line in _wrap(content, fontname, size, _TEXT_W - indent):
            self._ensure(line_h)
            self.page.insert_text(
                (_MARGIN + indent, self.y + size),
                line,
                fontname=fontname,
                fontsize=size,
                color=color,
            )
            self.y += line_h
        self.y += gap

    def space(self, height: float) -> None:
        self._ensure(height)
        self.y += height

    def rule(self) -> None:
        self._ensure(12)
        self.page.draw_line(
            (_MARGIN, self.y + 4), (_PAGE_W - _MARGIN, self.y + 4), color=_RULE, width=0.7
        )
        self.y += 12

    def finish_footers(self) -> None:
        total = len(self.doc)
        for i, page in enumerate(self.doc, start=1):
            page.insert_text(
                (_MARGIN, _PAGE_H - 36),
                "Personal record summary - not medical advice. Please verify all entries with the patient.",
                fontname="helv",
                fontsize=7.5,
                color=_GRAY,
            )
            label = f"Page {i} of {total}"
            width = fitz.get_text_length(label, fontname="helv", fontsize=7.5)
            page.insert_text(
                (_PAGE_W - _MARGIN - width, _PAGE_H - 36),
                label,
                fontname="helv",
                fontsize=7.5,
                color=_GRAY,
            )


def _record_block(w: _Writer, record: NormalizedRecord) -> None:
    name = record.normalization.canonical_name or record.extracted.name_as_written

    title = name
    if record.needs_review:
        title += "   ** PLEASE CONFIRM **"
    w.text(title, size=10.5, bold=True, color=_RED if record.needs_review else _BLACK, gap=1)

    details = " / ".join(
        part
        for part in (
            record.extracted.dosage,
            record.extracted.frequency,
            record.extracted.form,
            record.extracted.route,
        )
        if part
    )
    if details:
        w.text(details, size=9.5, gap=1)

    provenance_bits = []
    if (
        record.normalization.canonical_name
        and record.extracted.name_as_written.lower() != record.normalization.canonical_name.lower()
    ):
        provenance_bits.append(f'written as "{record.extracted.name_as_written}"')
    if record.normalization.rxcui:
        provenance_bits.append(f"RxNorm RxCUI {record.normalization.rxcui}")
    provenance_bits.append(f"reading confidence {round(record.overall_confidence * 100)}%")
    if record.source_filename:
        provenance_bits.append(f"source: {record.source_filename}")
    if record.extracted.prescriber_or_source:
        provenance_bits.append(f"prescriber/source: {record.extracted.prescriber_or_source}")
    if record.extracted.date_documented:
        provenance_bits.append(f"documented {record.extracted.date_documented}")
    w.text(" - ".join(provenance_bits), size=8.0, color=_GRAY, indent=2, gap=1)

    for note in record.extracted.ambiguities:
        w.text(f"Note: {note}", size=8.5, color=_RED, indent=2, gap=1)

    w.space(7)


def _finding_block(w: _Writer, finding: Finding) -> None:
    label, color = _SEVERITY_LABEL[finding.severity]
    w.text(f"[{label}]  {finding.title}", size=10.0, bold=True, color=color, gap=1)
    w.text("Involves: " + ", ".join(finding.involved), size=9.0, indent=2, gap=1)
    w.text(finding.explanation, size=9.0, color=_GRAY, indent=2, gap=1)
    w.text(f"Suggested next step: {finding.recommendation}", size=9.0, indent=2, gap=1)
    if finding.reading_confidence >= 1.0:
        caveat = f"{finding.evidence_note} Based on the patient's confirmed medication list."
    else:
        caveat = f"{finding.evidence_note} Based on entries read with >= {round(finding.reading_confidence * 100)}% confidence."
    if finding.needs_record_review:
        caveat += " One or more underlying entries is itself marked PLEASE CONFIRM - verify those first."
    w.text(caveat, size=7.5, color=_GRAY, indent=2, gap=1)
    w.space(7)


def build_pdf(
    records: list[NormalizedRecord],
    findings: list[Finding] | None = None,
    labs: list[LabResult] | None = None,
) -> bytes:
    medicines = sorted(
        (r for r in records if r.kind == RecordKind.medicine),
        key=lambda r: (r.normalization.canonical_name or r.extracted.name_as_written).lower(),
    )
    supplements = sorted(
        (r for r in records if r.kind == RecordKind.supplement),
        key=lambda r: (r.normalization.canonical_name or r.extracted.name_as_written).lower(),
    )
    flagged = sum(1 for r in records if r.needs_review)
    findings = findings or []
    labs = labs or []
    major_count = sum(1 for f in findings if f.severity == FindingSeverity.major)

    w = _Writer()

    w.text("Medication & Supplement Summary", size=16, bold=True, gap=2)
    summary = (
        f"Prepared {date.today().isoformat()}  -  {len(medicines)} medication(s), "
        f"{len(supplements)} supplement(s)"
    )
    if flagged:
        summary += f"  -  {flagged} item(s) marked PLEASE CONFIRM"
    if findings:
        summary += f"  -  {len(findings)} screening finding(s)"
        if major_count:
            summary += f" ({major_count} major)"
    w.text(summary, size=9.5, color=_GRAY, gap=2)
    w.text(
        "Entries were read from the patient's documents and photos by software and "
        "standardized against RxNorm. Each entry shows a reading confidence; items "
        "the software was unsure about are marked PLEASE CONFIRM in red and should "
        "be verified with the patient.",
        size=8.5,
        color=_GRAY,
        gap=4,
    )
    w.rule()

    w.text("POTENTIAL INTERACTIONS & RECOMMENDATIONS", size=11.5, bold=True, gap=2)
    w.text(
        "Screened against a built-in list of well-documented interactions "
        "(drug-drug, drug-supplement, vitamin/mineral, duplicate therapy, nutrient "
        "depletion). This is a starter screen, not a complete interaction check - "
        "a pharmacist can run a full one.",
        size=8.0,
        color=_GRAY,
        gap=5,
    )
    if findings:
        for finding in findings:
            _finding_block(w, finding)
    else:
        w.text(
            "No potential interactions found in the built-in screening list.",
            size=9.5, color=_GRAY, gap=6,
        )

    w.rule()
    w.text("MEDICATIONS", size=11.5, bold=True, gap=6)
    if medicines:
        for record in medicines:
            _record_block(w, record)
    else:
        w.text("No medications recorded.", size=9.5, color=_GRAY, gap=6)

    w.rule()
    w.text("SUPPLEMENTS", size=11.5, bold=True, gap=6)
    if supplements:
        for record in supplements:
            _record_block(w, record)
    else:
        w.text("No supplements recorded.", size=9.5, color=_GRAY, gap=6)

    if labs:
        w.rule()
        w.text("LAB RESULTS AS READ FROM THE PATIENT'S DOCUMENTS", size=11.5, bold=True, gap=2)
        w.text(
            "Transcribed from uploaded reports, not retrieved from a laboratory system. "
            "Verify against the source report before acting on any value.",
            size=8.0, color=_GRAY, gap=5,
        )
        _lab_block(w, labs)

    w.finish_footers()
    pdf_bytes = w.doc.tobytes()
    w.doc.close()
    return pdf_bytes


def _item_block(w: _Writer, item: MedListItem) -> None:
    title = item.canonical_name or item.name
    if item.status == ItemStatus.stopped:
        title += "   [STOPPED]"
    w.text(title, size=10.5, bold=True,
           color=_GRAY if item.status == ItemStatus.stopped else _BLACK, gap=1)
    details = " / ".join(p for p in (item.dosage, item.frequency) if p)
    if details:
        w.text(details, size=9.5, gap=1)
    bits = [item.kind.value]
    if item.canonical_name and item.name.lower() != item.canonical_name.lower():
        bits.append(f'entered as "{item.name}"')
    if item.ingredient_name:
        bits.append(f"ingredient: {item.ingredient_name}")
    if item.rxcui:
        bits.append(f"RxCUI {item.rxcui}")
    w.text(" - ".join(bits), size=8.0, color=_GRAY, indent=2, gap=1)
    if item.notes:
        w.text(f"Notes: {item.notes}", size=8.5, color=_GRAY, indent=2, gap=1)
    w.space(6)


def _lab_block(w: _Writer, labs: list[LabResult]) -> None:
    for lab in labs:
        line = f"{lab.name}: {lab.display}"
        if lab.reference_range:
            line += f"  (ref {lab.reference_range})"
        if lab.flag.value not in ("unknown", "normal"):
            line += f"  [{lab.flag.value.upper()}]"
        if lab.collected_date:
            line += f"  - {lab.collected_date}"
        colour = _RED if lab.flag.value == "critical" else (
            _AMBER if lab.flag.value in ("high", "low") else _BLACK
        )
        w.text(line, size=9.5, color=colour, gap=1)
        if lab.needs_review:
            w.text("Reading uncertain - confirm against the original report.",
                   size=8, color=_AMBER, indent=2, gap=1)
    w.space(4)


_GRADE_LABEL = {
    "outcome": "outcome data",
    "surrogate": "lab endpoint",
    "pharmacokinetic": "exposure study",
    "case_report": "case reports only",
    "mechanism": "mechanism only",
}
_TIER_LABEL = {
    "free_now": "Free, today",
    "pharmacist": "Ask a pharmacist (free)",
    "appointment": "At your next appointment",
    "specialist": "Needs a referral",
}


def build_panel_pdf(
    review, items: list[MedListItem], labs: list[LabResult] | None = None
) -> bytes:
    """The panel's output as something to hand over at an appointment.

    Questions come first, because that is what the visit is for. The
    disagreements are kept and given their own section rather than smoothed
    into the consensus - what the panel could not settle is the part a
    clinician is actually equipped to resolve."""

    w = _Writer()

    w.text("Expert Panel Review", size=16, bold=True, gap=2)
    w.text(
        f"Generated {review.created_at.date().isoformat()}  -  "
        f"{len(review.seated)} experts seated, {len(review.concerns)} agreed concern(s), "
        f"{len(review.dissent)} unresolved disagreement(s)",
        size=9.5, color=_GRAY, gap=2,
    )
    w.text(
        "Produced by an AI panel as preparation for this conversation. Every expert in it is "
        "generated by the same underlying model, so agreement between them is much weaker "
        "evidence than agreement between independent clinicians. Nothing here is a "
        "recommendation to change treatment.",
        size=8.5, color=_GRAY, gap=4,
    )
    w.rule()

    if review.urgency_message:
        w.text(review.urgency_message, size=10, bold=True, color=_RED, gap=6)

    if review.headline:
        w.text(review.headline, size=11.5, bold=True, gap=4)
    if review.summary:
        w.text(review.summary, size=9.5, gap=6)

    if getattr(review, "resolved", None):
        w.rule()
        w.text("SETTLED SINCE THE LAST REVIEW", size=11.5, bold=True, gap=2)
        w.text(
            "The patient was asked to find these out, did, and the panel changed its "
            "reading accordingly.",
            size=8.5, color=_GRAY, gap=6,
        )
        for item in review.resolved:
            w.text(item.topic, size=10.5, bold=True, gap=1)
            w.text(item.outcome, size=9.5, indent=2, gap=1)
            w.text(f"Settled by: {item.settled_by}", size=8.5, color=_GRAY, indent=2, gap=1)
            w.space(5)

    if review.questions:
        w.rule()
        w.text("QUESTIONS THE PATIENT WANTS TO ASK", size=11.5, bold=True, gap=6)
        for q in review.questions:
            w.text(f'"{q.question}"', size=10, bold=True, gap=1)
            w.text(q.why, size=9, color=_GRAY, indent=2, gap=1)
            bits = [_TIER_LABEL.get(q.tier.value, q.tier.value)]
            if q.about:
                bits.append(", ".join(q.about))
            w.text(" - ".join(bits), size=8, color=_GRAY, indent=2, gap=1)
            w.space(5)

    if review.concerns:
        w.rule()
        w.text("WHAT THE PANEL AGREED IS WORTH RAISING", size=11.5, bold=True, gap=6)
        for c in review.concerns:
            w.text(c.title, size=10.5, bold=True, gap=1)
            w.text(c.detail, size=9.5, indent=2, gap=1)
            meta = [
                ", ".join(c.involved) or "-",
                f"evidence: {_GRADE_LABEL.get(c.evidence_grade.value, c.evidence_grade.value)}",
                f"panel confidence {c.confidence:.0%}",
            ]
            w.text(" - ".join(meta), size=8, color=_GRAY, indent=2, gap=1)
            if c.data_caveat:
                w.text(f"Caveat: {c.data_caveat}", size=8.5, color=_AMBER, indent=2, gap=1)
            w.space(5)

    if review.dissent:
        w.rule()
        w.text("WHERE THE PANEL DID NOT AGREE", size=11.5, bold=True, gap=2)
        w.text(
            "Kept deliberately. These are the questions a clinician is equipped to settle "
            "and this panel is not.",
            size=8.5, color=_GRAY, gap=6,
        )
        for d in review.dissent:
            w.text(d.topic, size=10.5, bold=True, gap=2)
            w.text(f"One side ({len(d.side_a_agents)}): {d.side_a}", size=9.5, indent=2, gap=1)
            w.text(f"Other side ({len(d.side_b_agents)}): {d.side_b}", size=9.5, indent=2, gap=1)
            w.text(f"What would settle it: {d.what_would_settle_it}", size=9.5,
                   bold=True, indent=2, gap=1)
            w.space(5)

    if review.discriminating_observations:
        w.rule()
        w.text("OBSERVATIONS THAT WOULD RESOLVE THE OPEN QUESTIONS", size=11.5, bold=True, gap=6)
        for obs in review.discriminating_observations:
            w.text(f"- {obs}", size=9.5, gap=2)
        w.space(4)

    if review.unknowns:
        w.rule()
        w.text("WHAT THE PANEL COULD NOT SEE", size=11.5, bold=True, gap=6)
        for unknown in review.unknowns:
            w.text(f"- {unknown}", size=9.5, color=_GRAY, gap=2)
        w.space(4)

    if review.dropped:
        w.rule()
        w.text("CONSIDERED AND RULED OUT", size=11.5, bold=True, gap=2)
        w.text(
            "Recorded so it is clear these were examined rather than missed.",
            size=8.5, color=_GRAY, gap=6,
        )
        for d in review.dropped:
            w.text(f"{d.title} - {d.reason}", size=9, color=_GRAY, gap=2)
        w.space(4)

    if labs:
        w.rule()
        w.text("LAB VALUES THE PANEL READ", size=11.5, bold=True, gap=6)
        _lab_block(w, labs)

    w.rule()
    w.text("THE REGIMEN THIS REVIEWED", size=11.5, bold=True, gap=6)
    active = [i for i in items if i.status == ItemStatus.active]
    for item in active:
        detail = " ".join(p for p in (item.dosage, item.frequency) if p)
        w.text(f"- {item.canonical_name or item.name}" + (f"  {detail}" if detail else ""),
               size=9.5, gap=2)
    if not active:
        w.text("(the list was empty)", size=9.5, color=_GRAY, gap=2)

    w.finish_footers()
    pdf_bytes = w.doc.tobytes()
    w.doc.close()
    return pdf_bytes


def build_archive_pdf(
    records: list[NormalizedRecord],
    items: list[MedListItem],
    baselines: list[Baseline],
    history: list[ListHistoryEvent],
    findings: list[Finding],
    labs: list[LabResult] | None = None,
) -> bytes:
    """The everything-before-reset archive: the complete medication list
    (including stopped items), current screening findings, every baseline,
    the full change history, and every raw ingested record. Generated and
    returned BEFORE any data is deleted."""

    w = _Writer()

    active = sum(1 for i in items if i.status == ItemStatus.active)
    w.text("Medical Concierge - Full Archive", size=16, bold=True, gap=2)
    w.text(
        f"Generated {date.today().isoformat()} before a data reset  -  "
        f"{active} active and {len(items) - active} stopped list item(s), "
        f"{len(baselines)} baseline(s), {len(history)} history event(s), "
        f"{len(records)} source record(s)",
        size=9.5, color=_GRAY, gap=2,
    )
    w.text(
        "This document is a complete snapshot of everything stored in the app "
        "at the moment the user chose Start Over. Keep it - the data it "
        "describes was erased immediately after this file was created.",
        size=8.5, color=_GRAY, gap=4,
    )
    w.rule()

    w.text("POTENTIAL INTERACTIONS AT TIME OF RESET", size=11.5, bold=True, gap=6)
    if findings:
        for finding in findings:
            _finding_block(w, finding)
    else:
        w.text("No findings from the built-in screening list.", size=9.5, color=_GRAY, gap=6)

    w.rule()
    w.text("MEDICATION & SUPPLEMENT LIST (including stopped)", size=11.5, bold=True, gap=6)
    if items:
        for item in items:
            _item_block(w, item)
    else:
        w.text("The list was empty.", size=9.5, color=_GRAY, gap=6)

    w.rule()
    w.text("BASELINES", size=11.5, bold=True, gap=6)
    if baselines:
        for b in baselines:
            w.text(
                f"{b.name}  -  {b.created_at.date().isoformat()}  -  {len(b.items)} item(s)",
                size=9.5, gap=2,
            )
    else:
        w.text("No baselines were set.", size=9.5, color=_GRAY, gap=6)
    w.space(4)

    w.rule()
    w.text("LAB RESULTS", size=11.5, bold=True, gap=6)
    if labs:
        _lab_block(w, labs)
    else:
        w.text("No lab results were on file.", size=9.5, color=_GRAY, gap=6)

    w.rule()
    w.text("COMPLETE CHANGE HISTORY", size=11.5, bold=True, gap=6)
    if history:
        for event in history:
            name = event.item_snapshot.canonical_name or event.item_snapshot.name
            w.text(
                f"{event.timestamp.strftime('%Y-%m-%d %H:%M')}  {name}  -  "
                f"{event.action}: {event.detail}",
                size=8.5, color=_GRAY, gap=1,
            )
    else:
        w.text("No history events.", size=9.5, color=_GRAY, gap=6)
    w.space(4)

    w.rule()
    w.text("RAW SOURCE RECORDS (as read from documents)", size=11.5, bold=True, gap=6)
    if records:
        for record in records:
            _record_block(w, record)
    else:
        w.text("No source records.", size=9.5, color=_GRAY, gap=6)

    w.finish_footers()
    pdf_bytes = w.doc.tobytes()
    w.doc.close()
    return pdf_bytes
