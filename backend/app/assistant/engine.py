"""The conversational layer: answers grounded in the person's own record.

Two things separate this from a general medical chatbot, and both are
structural rather than stylistic.

It answers from the record and says so. Every claim is expected to cite the
item, lab, history event or panel conclusion it came from, and "your documents
don't say" is a first-class answer - often the most useful one, because a blank
where an indication should be is itself a finding worth taking to a prescriber.

It never decides anything. Questions of the form "should I take this" have no
answer here by construction: the response schema puts clinical questions in
their own field, pointed at a clinician, and a directive-language backstop
flags anything that slips through. The assistant's job is to turn a question
it cannot answer into a better question for someone who can.
"""

from __future__ import annotations

from app.assistant import guard, retrieval
from app.assistant.schemas import (
    ActionKind,
    AssistantTurn,
    Citation,
    CitationKind,
    ProposedAction,
    Role,
)
from app.config import get_settings
from app.panel import llm
from app.panel.safety import screen
from app.panel.schemas import Urgency

_HISTORY_TURNS = 8  # prior turns sent back for continuity

_RESPONSE_SCHEMA = {
    "type": "object",
    "properties": {
        "answer": {
            "type": "string",
            "description": "Plain language, under 160 words, addressed to the person as 'you'. "
            "Lead with the answer. If their record does not contain what was asked, say that "
            "plainly in the first sentence rather than filling the gap with general knowledge.",
        },
        "citations": {
            "type": "array",
            "description": "What in their record you drew on. Use the bracketed ids from the "
            "record exactly. Empty only when the answer genuinely used nothing from it.",
            "items": {
                "type": "object",
                "properties": {
                    "kind": {"type": "string", "enum": [k.value for k in CitationKind]},
                    "ref_id": {"type": "string"},
                    "label": {"type": "string", "description": "How to show it, e.g. 'Warfarin 5 mg'."},
                },
                "required": ["kind", "ref_id", "label"],
                "additionalProperties": False,
            },
        },
        "questions_for_clinician": {
            "type": "array",
            "description": "Where the question was clinical rather than about their record, put "
            "it here as something they could say out loud to a pharmacist or prescriber. This is "
            "where anything you are not allowed to decide goes.",
            "items": {"type": "string"},
        },
        "actions": {
            "type": "array",
            "description": "Changes to their record implied by what they just said - typically "
            "after an appointment. Propose; never assume. Leave empty unless they clearly "
            "reported something that changed.",
            "items": {
                "type": "object",
                "properties": {
                    "kind": {"type": "string", "enum": [k.value for k in ActionKind]},
                    "description": {
                        "type": "string",
                        "description": "One sentence the person will see on a confirm button, "
                        "e.g. 'Change Warfarin from 5 mg to 7.5 mg daily'.",
                    },
                    "target_id": {
                        "type": "string",
                        "description": "The bracketed id from the record. Required for "
                        "update_item, stop_item and answer_observation; empty for add_item.",
                    },
                    "payload": {
                        "type": "object",
                        "description": "add_item: kind/name/dosage/frequency. update_item: any of "
                        "dosage/frequency/notes. stop_item: empty. answer_observation: answer.",
                        "properties": {
                            "kind": {"type": "string"},
                            "name": {"type": "string"},
                            "dosage": {"type": "string"},
                            "frequency": {"type": "string"},
                            "notes": {"type": "string"},
                            "answer": {"type": "string"},
                        },
                        "additionalProperties": False,
                    },
                },
                "required": ["kind", "description", "target_id", "payload"],
                "additionalProperties": False,
            },
        },
    },
    "required": ["answer", "citations", "questions_for_clinician", "actions"],
    "additionalProperties": False,
}


def _system_prompt() -> str:
    return """You are the assistant inside a personal medication record app. The person you are talking to is coordinating their own care between doctors who do not talk to each other, and you are looking at everything their app knows about them.

What you are for: answering questions about THEIR OWN RECORD, and turning everything else into a better question for someone licensed.

Binding rules:
- Answer from their record. Cite what you used, by the bracketed ids. When their record does not say, your first sentence is that it does not say - never fill the gap with general knowledge dressed up as their history.
- A blank is a finding. "Nothing in your documents says why you were put on this" is a genuinely useful answer, and it belongs in questions_for_clinician too.
- NEVER tell them to start, stop, change, skip, or adjust anything. Not directly, not as a hint, not hedged. A question like "should I stop this?" has no answer here: say plainly that it is their prescriber's call, give them what their own record says that bears on it, and put the question itself into questions_for_clinician phrased the way they could say it out loud.
- General pharmacology is allowed when it explains something in their record ("warfarin and fish oil both affect bleeding, by different mechanisms"), and must be labelled as general rather than specific to them. Being specific about a drug is fine; being directive about THEIR drug is not.
- If they report something that changed - an appointment, a new prescription, something they stopped - propose record updates as actions. Propose only what they actually said. A dose you inferred is a dose you invented.
- Plain, warm, brief. No preamble, no restating the question, no "as an AI". Short sentences. If you use a clinical term, gloss it in the same sentence.
- If anything they write suggests immediate danger, your answer is to get help now, and nothing else."""


def _parse(raw: dict, digest: str) -> AssistantTurn:
    """Drop citations and action targets whose ids are not actually in the
    record, so the UI can never show a reference to something invented."""

    turn = AssistantTurn(role=Role.assistant, text=(raw.get("answer") or "").strip())

    for c in raw.get("citations") or []:
        ref = (c.get("ref_id") or "").strip()
        try:
            kind = CitationKind(c.get("kind"))
        except ValueError:
            continue
        if c.get("label") and (not ref or f"[{ref}]" in digest):
            turn.citations.append(Citation(kind=kind, ref_id=ref, label=c["label"]))

    for a in raw.get("actions") or []:
        try:
            kind = ActionKind(a.get("kind"))
        except ValueError:
            continue
        target = (a.get("target_id") or "").strip()
        # Everything except add_item edits something that must already exist.
        if kind is not ActionKind.add_item and (not target or f"[{target}]" not in digest):
            continue
        if not a.get("description"):
            continue
        payload = {k: v for k, v in (a.get("payload") or {}).items() if v not in (None, "")}
        if kind is ActionKind.add_item and not payload.get("name"):
            continue
        turn.actions.append(ProposedAction(
            kind=kind, description=a["description"], target_id=target, payload=payload
        ))

    turn.questions_for_clinician = [
        q for q in raw.get("questions_for_clinician") or []
        if isinstance(q, str) and q.strip()
    ]
    return turn


async def answer(
    message: str,
    *,
    items,
    labs,
    history,
    observations,
    findings,
    review=None,
    prior: list[AssistantTurn] | None = None,
) -> AssistantTurn:
    """One conversational turn."""

    # The urgency screen runs on every turn, before any API call - the same
    # deterministic gate the panel uses, for the same reason.
    urgency = screen(message)
    if urgency.level is Urgency.urgent:
        return AssistantTurn(
            role=Role.assistant,
            text=urgency.message,
            urgency=urgency.level,
            urgency_message=urgency.message,
        )

    digest = retrieval.build_digest(items, labs, history, observations, findings, review)

    transcript = ""
    for turn in (prior or [])[-_HISTORY_TURNS:]:
        who = "They" if turn.role is Role.user else "You"
        transcript += f"\n{who}: {turn.text}"

    raw = await llm.structured(
        system=_system_prompt(),
        user=f"""# Their record

{digest}
{f"# Earlier in this conversation{transcript}" if transcript else ""}

# What they just said

\"\"\"
{message.strip()}
\"\"\"""",
        schema=_RESPONSE_SCHEMA,
        gate=llm.semaphore(),
        max_tokens=2000,
        model=get_settings().assistant_model,
    )

    turn = _parse(raw, digest)
    turn.urgency = urgency.level
    turn.urgency_message = urgency.message
    turn.guard_notice = guard.check(turn.text)
    return turn
