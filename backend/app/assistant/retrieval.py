"""Assembling the person's record into something the assistant can answer from.

No vector store, deliberately. One person's medication record is small - a few
dozen items, some labs, a change history measured in hundreds of rows - so the
honest engineering answer is to put the relevant parts in the prompt and skip
an entire category of infrastructure and its failure modes. The only thing
that genuinely grows without bound is history, which is bounded here and made
searchable by date instead.
"""

from __future__ import annotations

from app.panel.schemas import ObservationStatus
from app.schemas import ItemStatus

# History is the one unbounded table. Recent events answer "what changed
# lately"; anything older is reachable because every event carries its date.
_HISTORY_LIMIT = 60


def _item_line(item) -> str:
    bits = [f"[{item.id}]", item.canonical_name or item.name]
    if item.canonical_name and item.name.lower() != item.canonical_name.lower():
        bits.append(f'(written as "{item.name}")')
    detail = " ".join(p for p in (item.dosage, item.frequency) if p)
    bits.append(f"- {detail}" if detail else "- no dose recorded")
    bits.append(f"[{item.kind.value}]")
    if item.ingredient_name:
        bits.append(f"ingredient: {item.ingredient_name}")
    if item.status == ItemStatus.stopped:
        bits.append("** STOPPED **")
    if item.notes:
        bits.append(f"note: {item.notes}")
    return " ".join(bits)


def build_digest(
    items,
    labs,
    history,
    observations,
    findings,
    review=None,
) -> str:
    """The record as the assistant sees it. Ids are included because every
    answer has to be able to cite exactly what it drew on, and every proposed
    change has to name exactly what it would change."""

    active = [i for i in items if i.status == ItemStatus.active]
    stopped = [i for i in items if i.status == ItemStatus.stopped]

    parts = [f"## Current medications and supplements ({len(active)} active)"]
    parts += [_item_line(i) for i in active] or ["(nothing on the list)"]

    if stopped:
        parts.append(f"\n## Stopped ({len(stopped)})")
        parts += [_item_line(i) for i in stopped]

    if labs:
        parts.append(f"\n## Lab results ({len(labs)}, most recent first)")
        for lab in labs:
            line = f"[{lab.id}] {lab.name}: {lab.display}"
            if lab.reference_range:
                line += f" (ref {lab.reference_range})"
            if lab.flag.value != "unknown":
                line += f" [{lab.flag.value.upper()}]"
            if lab.collected_date:
                line += f" collected {lab.collected_date}"
            if lab.needs_review:
                line += " ** reading uncertain **"
            parts.append(line)
    else:
        parts.append("\n## Lab results\n(none on file)")

    if history:
        recent = history[:_HISTORY_LIMIT]
        parts.append(f"\n## Change history ({len(recent)} most recent of {len(history)})")
        parts.append(
            "This is the complete audit trail of every change ever made to the list, "
            "so questions about what changed and when are answerable exactly."
        )
        for event in recent:
            name = event.item_snapshot.canonical_name or event.item_snapshot.name
            parts.append(
                f"[{event.id}] {event.timestamp.strftime('%Y-%m-%d')} {name} - "
                f"{event.action}: {event.detail}"
            )

    open_obs = [o for o in observations if o.status is ObservationStatus.open]
    answered = [o for o in observations if o.status is ObservationStatus.answered]
    if open_obs or answered:
        parts.append("\n## Questions the expert panel is waiting on")
        for o in open_obs:
            parts.append(f"[{o.id}] OPEN: {o.text}" + (f" (would settle: {o.topic})" if o.topic else ""))
        for o in answered:
            parts.append(f"[{o.id}] ANSWERED: {o.text} -> {o.answer}")

    if findings:
        parts.append("\n## Current rule-screen warnings")
        for f in findings:
            parts.append(f"[{f.rule_id}] {f.severity.value}: {f.title} ({', '.join(f.involved)})")

    if review is not None and review.headline:
        parts.append("\n## Most recent expert panel review")
        parts.append(f"[{review.id}] {review.headline}")
        for concern in review.concerns:
            parts.append(f"- agreed: {concern.title} - {concern.detail}")
        for d in review.dissent:
            parts.append(f"- unresolved: {d.topic} (would settle: {d.what_would_settle_it})")

    return "\n".join(parts)
