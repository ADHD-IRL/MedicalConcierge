"""Prompt construction for the panel.

The house rules live in the registry JSON so the panel's binding commitments
are reviewable as data. Everything here assembles them with an agent's own
definition and the regimen under review.
"""

from __future__ import annotations

from app.interactions.knowledge_base import InteractionRule  # noqa: F401  (typing only)
from app.panel.registry import Agent, load_registry
from app.schemas import Finding, ItemStatus, LabResult, MedListItem, NormalizedRecord


def house_rules() -> str:
    rules = load_registry().house_rules
    return "\n".join(f"- {rule}" for rule in rules)


def agent_system(agent: Agent) -> str:
    """One agent's full system prompt: the binding rules every seat inherits,
    then its own definition, then the two things that keep it honest - what
    would make it concede, and where it is known to go wrong."""

    return f"""You are one subject-matter expert on a panel reviewing one person's medication and supplement regimen. The panel is decision support for a conversation with a clinician, not a clinic.

These rules override everything else:
{house_rules()}

Your seat on this panel is "{agent.name}" ({agent.discipline}).

{agent.system_prompt}

Your stance: {agent.stance}
Evidence you lean on: {", ".join(agent.privileged_evidence)}.
You concede when: {agent.concedes_when}
Your known failure mode: {agent.bias_watch} Check your own answer for it before you finish.

Write prose, not bullet points. Under 200 words. Do not open by restating the regimen or greeting anyone - start with your actual point."""


def _item_line(item: MedListItem, confidence: dict[str, float], review: set[str]) -> str:
    bits = [item.canonical_name or item.name]
    if item.canonical_name and item.name.lower() != item.canonical_name.lower():
        bits.append(f'(written as "{item.name}")')
    detail = " ".join(p for p in (item.dosage, item.frequency) if p)
    if detail:
        bits.append(f"- {detail}")
    else:
        bits.append("- NO DOSE RECORDED")
    bits.append(f"[{item.kind.value}]")
    if item.ingredient_name and item.ingredient_name.lower() != (item.canonical_name or "").lower():
        bits.append(f"ingredient: {item.ingredient_name}")
    if item.status == ItemStatus.stopped:
        bits.append("** STOPPED **")
    if item.notes:
        bits.append(f"note: {item.notes}")

    # Provenance, for the record auditor and for anyone reasoning off a dose.
    if item.source_record_id is None:
        bits.append("(entered by hand)")
    else:
        score = confidence.get(item.source_record_id)
        if score is not None:
            bits.append(f"(read from a document, confidence {score:.0%})")
        if item.source_record_id in review:
            bits.append("** FLAGGED FOR REVIEW - the reading itself is uncertain **")
    return "- " + " ".join(bits)


def _lab_line(lab: LabResult) -> str:
    bits = [f"- {lab.name}: {lab.display}"]
    if lab.reference_range:
        bits.append(f"(ref {lab.reference_range})")
    if lab.flag.value != "unknown":
        bits.append(f"[{lab.flag.value.upper()}]")
    if lab.collected_date:
        bits.append(f"collected {lab.collected_date}")
    if lab.needs_review:
        bits.append("** READING UNCERTAIN - confirm before relying on it **")
    return " ".join(bits)


def regimen_brief(
    items: list[MedListItem],
    records: list[NormalizedRecord],
    findings: list[Finding],
    note: str,
    labs: list[LabResult] | None = None,
    answered=None,
) -> str:
    """Everything the panel is given about the person. Provenance is included
    deliberately: an agent reasoning about a dose should be able to see how
    confidently that dose was read."""

    confidence = {r.id: r.overall_confidence for r in records}
    review = {r.id for r in records if r.needs_review}

    active = [i for i in items if i.status == ItemStatus.active]
    stopped = [i for i in items if i.status == ItemStatus.stopped]

    parts = [f"## Current regimen ({len(active)} active)"]
    parts += [_item_line(i, confidence, review) for i in active] or ["- (nothing on the list)"]

    if stopped:
        parts.append(f"\n## Recently stopped ({len(stopped)})")
        parts += [_item_line(i, confidence, review) for i in stopped]

    if labs:
        parts.append(f"\n## Lab results on file ({len(labs)}, most recent first)")
        parts.append(
            "Read these as written. Where a value settles something you would "
            "otherwise have to assume, say so explicitly rather than hedging."
        )
        parts += [_lab_line(lab) for lab in labs]
    else:
        parts.append(
            "\n## Lab results on file\n"
            "- NONE. No lab values have been uploaded, so kidney function, liver "
            "enzymes, INR, and thyroid levels are all unknown to this panel. Say what "
            "you are assuming rather than reasoning as though you knew."
        )

    if findings:
        parts.append("\n## What the built-in rule screen already flagged")
        parts.append(
            "These come from a fixed table of well-documented interactions, not from "
            "reasoning. Treat them as a starting point to argue with - confirm, "
            "contextualize, or dismiss them as clinically inert for this person."
        )
        for f in findings:
            parts.append(
                f"- [{f.severity.value}] {f.title} ({', '.join(f.involved)}): {f.explanation}"
            )

    if answered:
        parts.append(f"\n## Answers since the last review ({len(answered)})")
        parts.append(
            "The panel previously said these observations would settle open questions, "
            "and the person went and found out. Treat each as established fact. Where an "
            "answer meets your own stated concede-when condition, CONCEDE explicitly and "
            "say so - a panel that never changes its mind when given the evidence it "
            "asked for is not deliberating."
        )
        for obs in answered:
            parts.append(f'- Asked: {obs.text}\n  Answer: {obs.answer}')

    parts.append("\n## What the person said")
    parts.append(f'"""\n{note.strip() or "(they did not add a note)"}\n"""')

    return "\n".join(parts)
