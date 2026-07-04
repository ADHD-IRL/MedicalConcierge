"""Generates a clinician-readable PDF summary of all stored records.

Built with PyMuPDF (already a dependency for PDF ingestion), so no extra
packages. Layout is deliberately plain — black text, clear sections, flagged
items marked in red — because the audience is a doctor skimming it during a
short appointment, not a dashboard.
"""

from __future__ import annotations

from datetime import date

import fitz

from app.schemas import NormalizedRecord, RecordKind

_PAGE_W, _PAGE_H = fitz.paper_size("letter")
_MARGIN = 54.0
_BOTTOM = _PAGE_H - 64.0
_TEXT_W = _PAGE_W - 2 * _MARGIN

_BLACK = (0.0, 0.0, 0.0)
_GRAY = (0.38, 0.38, 0.38)
_RED = (0.72, 0.11, 0.11)
_RULE = (0.80, 0.82, 0.85)


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


def build_pdf(records: list[NormalizedRecord]) -> bytes:
    medicines = sorted(
        (r for r in records if r.kind == RecordKind.medicine),
        key=lambda r: (r.normalization.canonical_name or r.extracted.name_as_written).lower(),
    )
    supplements = sorted(
        (r for r in records if r.kind == RecordKind.supplement),
        key=lambda r: (r.normalization.canonical_name or r.extracted.name_as_written).lower(),
    )
    flagged = sum(1 for r in records if r.needs_review)

    w = _Writer()

    w.text("Medication & Supplement Summary", size=16, bold=True, gap=2)
    summary = (
        f"Prepared {date.today().isoformat()}  -  {len(medicines)} medication(s), "
        f"{len(supplements)} supplement(s)"
    )
    if flagged:
        summary += f"  -  {flagged} item(s) marked PLEASE CONFIRM"
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

    w.finish_footers()
    pdf_bytes = w.doc.tobytes()
    w.doc.close()
    return pdf_bytes
