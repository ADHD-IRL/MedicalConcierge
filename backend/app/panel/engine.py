"""The eight-round SME panel.

Protocol (``data/panel_registry.json``):

  1. Intake framing        - state the regimen, the unknowns; urgency gate
  2. Independent review    - every seated agent, in parallel, blind to each other
  3. Cross-examination     - challenges along the registry's adversarial edges
  4. Significance pruning  - evidence grading; drop what is inert in practice
  5. Lived-experience pass - adherence reality, and whether this helps or frightens
  6. Feasibility + ethics  - tier by cost and access; check for drift into directing
  7. Synthesis and dissent - the output, with disagreement kept intact
  8. Safety veto           - rewrite anything that reads as an instruction

Round 2 is parallel by design: the point of an independent round is that no
agent can see another's answer, which is the only structural defence this
has against every seat agreeing because they share one underlying model.
"""

from __future__ import annotations

import asyncio
from typing import Any, Callable

from app.config import get_settings
from app.panel import llm, prompts
from app.panel.registry import Agent, load_registry
from app.panel.safety import screen
from app.panel.schemas import (
    AccessTier,
    Challenge,
    Concern,
    Dissent,
    DroppedConcern,
    EvidenceGrade,
    PanelReview,
    Question,
    Seat,
    Take,
    Urgency,
)
from app.schemas import Finding, MedListItem, NormalizedRecord

Emit = Callable[[dict[str, Any]], None]

HALT_TOKEN = "STOP-URGENT"

_STRINGS = {"type": "array", "items": {"type": "string"}}


def _noop(_: dict[str, Any]) -> None:
    pass


# ----------------------------------------------------------------- round 1

_FRAMING_SCHEMA = {
    "type": "object",
    "properties": {
        "framing": {
            "type": "string",
            "description": "The regimen and the question it raises, in two neutral sentences. "
            "No interpretation, so the person can tell you if you framed it wrong.",
        },
        "unknowns": {
            **_STRINGS,
            "description": "What is missing or uncertain and would change the review: absent "
            "doses, low-confidence readings, conditions implied but not stated, labs nobody can see.",
        },
        "seated": {
            "type": "array",
            "minItems": 4,
            "items": {
                "type": "object",
                "properties": {
                    "agent_id": {"type": "string"},
                    "reason": {"type": "string", "description": "One sentence, to the reader, on why this expert is worth hearing for this regimen."},
                },
                "required": ["agent_id", "reason"],
                "additionalProperties": False,
            },
        },
        "not_seated": {
            "type": "array",
            "minItems": 2,
            "maxItems": 5,
            "items": {
                "type": "object",
                "properties": {
                    "agent_id": {"type": "string"},
                    "reason": {"type": "string"},
                },
                "required": ["agent_id", "reason"],
                "additionalProperties": False,
            },
        },
    },
    "required": ["framing", "unknowns", "seated", "not_seated"],
    "additionalProperties": False,
}


def _roster_text(agents: list[Agent]) -> str:
    return "\n".join(
        f"- {a.id} — {a.name} ({a.panel}): {a.stance} Watch: {a.bias_watch}" for a in agents
    )


async def _round1(brief: str, gate: asyncio.Semaphore, emit: Emit) -> dict[str, Any]:
    registry = load_registry()
    seatable = registry.seatable
    moderator = registry.get("gov_moderator")
    max_seated = get_settings().panel_max_seated

    emit({"type": "round", "n": 1, "name": "Intake framing"})

    raw = await llm.structured(
        system=f"""{prompts.agent_system(moderator)}

You are opening the panel. Two jobs.

First, frame the regimen neutrally and list what is genuinely unknown - missing doses, readings the extractor was unsure of, conditions implied by a drug but never stated, lab values nobody here can see.

Second, seat the panel. Start from this regimen, not from the roster: work out what a bad review of THIS list would look like - what would be missed, over-flagged, or dismissed - and seat the experts who guard against those specific failures.

Rules for seating:
- Seat between 4 and {max_seated} experts. Fewer, well-chosen beats a crowd.
- Always seat at least one adversarial seat that would push back on the obvious reading.
- Always seat at least one non-clinical seat - lived experience, access, or data provenance - because a review that is only clinicians misses how this is actually lived.
- Then record two to five experts you deliberately left out and why. This is not a formality: writing down who was excluded is what stops the panel quietly becoming the same six seats for every regimen.
- Use the exact agent_id values given. Never invent one.""",
        user=f"{brief}\n\n## Experts available\n{_roster_text(seatable)}",
        schema=_FRAMING_SCHEMA,
        gate=gate,
        max_tokens=3000,
    )

    return _normalise_seating(raw, seatable, max_seated)


def _normalise_seating(raw: dict, seatable: list[Agent], max_seated: int) -> dict[str, Any]:
    """Drop invented ids, de-duplicate, and guarantee a panel wide enough to
    disagree - a formatting slip should not collapse the debate."""

    allowed = {a.id: a for a in seatable}
    seen: set[str] = set()

    seated: list[Seat] = []
    for entry in raw.get("seated") or []:
        agent_id = entry.get("agent_id")
        if agent_id in allowed and agent_id not in seen:
            seen.add(agent_id)
            seated.append(Seat(
                agent_id=agent_id,
                name=allowed[agent_id].name,
                reason=(entry.get("reason") or "").strip() or "Relevant to this regimen.",
            ))
    seated = seated[:max_seated]

    for agent in seatable:
        if len(seated) >= 4:
            break
        if agent.id in seen:
            continue
        seen.add(agent.id)
        seated.append(Seat(agent_id=agent.id, name=agent.name,
                           reason="Added to keep the panel wide enough to disagree."))

    not_seated = [
        Seat(agent_id=e["agent_id"], name=allowed[e["agent_id"]].name,
             reason=(e.get("reason") or "").strip() or "Not central to this regimen.")
        for e in (raw.get("not_seated") or [])
        if e.get("agent_id") in allowed and e["agent_id"] not in seen
    ][:5]

    return {
        "framing": (raw.get("framing") or "").strip(),
        "unknowns": [u for u in (raw.get("unknowns") or []) if isinstance(u, str) and u.strip()],
        "seated": seated,
        "not_seated": not_seated,
    }


# ----------------------------------------------------------------- round 2

async def _take(agent: Agent, brief: str, gate: asyncio.Semaphore, emit: Emit) -> Take:
    emit({"type": "take-start", "agent_id": agent.id, "name": agent.name})
    text = await llm.prose(
        system=prompts.agent_system(agent),
        user=f"""{brief}

Give your independent read. Your required output for this round: {agent.required_round_output}

You are writing at the same time as the other experts and cannot see what any of them are saying, so do not reference them. If nothing in your specialty is relevant to this regimen, say so plainly in one sentence rather than manufacturing a concern.""",
        gate=gate,
        max_tokens=1200,
    )
    emit({"type": "take-end", "agent_id": agent.id})
    return Take(agent_id=agent.id, text=text)


# ----------------------------------------------------------------- round 3

_PAIRING_SCHEMA = {
    "type": "object",
    "properties": {
        "exchanges": {
            "type": "array",
            "maxItems": 6,
            "items": {
                "type": "object",
                "properties": {
                    "from_id": {"type": "string"},
                    "to_id": {"type": "string"},
                    "about": {
                        "type": "string",
                        "description": "The specific disagreement in one phrase. Must be a real "
                        "tension between what the two actually wrote, never a manufactured one.",
                    },
                },
                "required": ["from_id", "to_id", "about"],
                "additionalProperties": False,
            },
        }
    },
    "required": ["exchanges"],
    "additionalProperties": False,
}


async def _round3_pairs(takes: list[Take], gate: asyncio.Semaphore) -> list[dict]:
    if len(takes) < 2:
        return []

    registry = load_registry()
    edges = [
        f"{t.agent_id} typically challenges: {', '.join(registry.get(t.agent_id).typically_challenges) or '(no standing edges)'}"
        for t in takes
    ]

    raw = await llm.structured(
        system=f"""{prompts.agent_system(registry.get("gov_moderator"))}

You are reading the panel's opening statements and finding where they genuinely disagree about this regimen.

Pick real tensions - two experts who would have the person ask different things, or who explain the same risk incompatibly. Do not manufacture conflict where two simply emphasised different drugs. Never pair an expert with itself, and do not repeat a pair.

The registry's standing adversarial edges are below; prefer them when the disagreement is real, but a genuine tension off-edge beats a manufactured one on it.""",
        user="## Standing edges\n" + "\n".join(edges) + "\n\n## Opening statements\n\n" + "\n\n".join(
            f"### {t.agent_id} ({registry.get(t.agent_id).name})\n{t.text}" for t in takes
        ),
        schema=_PAIRING_SCHEMA,
        gate=gate,
        max_tokens=2000,
    )

    present = {t.agent_id for t in takes}
    used: set[tuple[str, str]] = set()
    pairs = []
    for e in raw.get("exchanges") or []:
        a, b = e.get("from_id"), e.get("to_id")
        if a in present and b in present and a != b and (a, b) not in used:
            used.add((a, b))
            pairs.append({"from_id": a, "to_id": b, "about": (e.get("about") or "").strip()})
    return pairs[:6]


async def _challenge(
    pair: dict, takes: list[Take], brief: str, gate: asyncio.Semaphore, emit: Emit
) -> Challenge:
    registry = load_registry()
    speaker, target = registry.get(pair["from_id"]), registry.get(pair["to_id"])
    target_text = next((t.text for t in takes if t.agent_id == pair["to_id"]), "")

    emit({"type": "challenge-start", "agent_id": speaker.id, "target_id": target.id,
          "name": speaker.name, "target_name": target.name})

    text = await llm.prose(
        system=f"""{prompts.agent_system(speaker)}

You are now pushing back on one other expert. Different rules this turn:
- Under 110 words, one paragraph.
- Address them by name in your first clause.
- Name the specific claim you think is wrong or incomplete, not their general outlook.
- Name the observation, lab, or timeline that would settle which of you is right. A challenge with no way to check it is just an opinion.
- Concede first if they are mostly right, and say what you would change your mind about.""",
        user=f"""{brief}

{target.name} said:

\"\"\"
{target_text}
\"\"\"

Push back, specifically on: {pair["about"]}""",
        gate=gate,
        max_tokens=700,
    )

    emit({"type": "challenge-end", "agent_id": speaker.id, "target_id": target.id})
    return Challenge(agent_id=speaker.id, target_id=target.id, about=pair["about"], text=text)


# ----------------------------------------------------------------- round 4

_PRUNE_SCHEMA = {
    "type": "object",
    "properties": {
        "kept": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "title": {"type": "string", "description": "Under 12 words."},
                    "detail": {"type": "string", "description": "Two sentences at most, plain language."},
                    "involved": _STRINGS,
                    "raised_by": {**_STRINGS, "description": "agent_ids that raised it."},
                    "evidence_grade": {
                        "type": "string",
                        "enum": [g.value for g in EvidenceGrade],
                        "description": "outcome = outcome data in a comparable population; "
                        "surrogate = lab endpoint; pharmacokinetic = healthy-volunteer exposure "
                        "study; case_report; mechanism = plausible mechanism only.",
                    },
                    "confidence": {"type": "number", "description": "0 to 1."},
                    "data_caveat": {
                        "type": "string",
                        "description": "Empty unless this rests on a dose or reading the record "
                        "auditor flagged as uncertain; then say which, in one clause.",
                    },
                },
                "required": ["title", "detail", "involved", "raised_by", "evidence_grade", "confidence", "data_caveat"],
                "additionalProperties": False,
            },
        },
        "dropped": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "title": {"type": "string"},
                    "reason": {"type": "string", "description": "Why it is inert in practice - incidence, dose, or no observation could discriminate it."},
                    "dropped_by": _STRINGS,
                },
                "required": ["title", "reason", "dropped_by"],
                "additionalProperties": False,
            },
        },
    },
    "required": ["kept", "dropped"],
    "additionalProperties": False,
}


async def _round4(transcript: str, brief: str, gate: asyncio.Semaphore) -> dict:
    registry = load_registry()
    evidence, skeptic = registry.get("res_evidence"), registry.get("red_alert_fatigue")

    return await llm.structured(
        system=f"""You are running the significance-pruning round jointly as two seats.

{evidence.name}: {evidence.system_prompt}

{skeptic.name}: {skeptic.system_prompt}

Together: take every concern the panel raised, grade its evidence honestly, and drop the ones that are real in principle and inert in practice at these doses. Keep what would change what this person asks their prescriber.

Dropping matters as much as keeping. A reader warned about everything learns to ignore warnings and then misses the one that mattered - so be willing to drop, and say plainly why.

Never drop something because it is inconvenient, and never drop a severe-consequence risk merely because it is rare. Where a concern depends on a reading the record auditor flagged, carry that caveat forward rather than hiding it.

{prompts.house_rules()}""",
        user=f"{brief}\n\n## Panel transcript\n\n{transcript}",
        schema=_PRUNE_SCHEMA,
        gate=gate,
        max_tokens=6000,
    )


# ----------------------------------------------------------------- rounds 5+6

async def _round5(transcript: str, brief: str, seated: list[str], gate: asyncio.Semaphore) -> str:
    registry = load_registry()
    voices = [registry.get(i) for i in ("ld_patient", "ld_caregiver") if i in seated] or [
        registry.get("ld_patient")
    ]
    joined = "\n\n".join(f"{v.name}: {v.system_prompt}" for v in voices)

    return await llm.prose(
        system=f"""You are the lived-experience round, speaking as the seats below.

{joined}

Review what the panel has concluded for three failures: language that would frighten without informing, concerns that ignore how medication is actually taken day to day, and anything that would land as being told you are on too much by someone who has never met you.

Under 220 words. Be specific about which conclusions need re-framing and how.

{prompts.house_rules()}""",
        user=f"{brief}\n\n## Panel transcript\n\n{transcript}",
        gate=gate,
        max_tokens=1200,
    )


_GATE_SCHEMA = {
    "type": "object",
    "properties": {
        "tiering_notes": {
            **_STRINGS,
            "description": "For each surviving concern, what acting on it actually costs this "
            "person - and name the free option first where one exists.",
        },
        "ethical_flags": {
            **_STRINGS,
            "description": "Anywhere the panel drifted from informing into directing, undermined "
            "a prescriber the person depends on, or raised alarm with no reachable next step. "
            "Empty if none.",
        },
    },
    "required": ["tiering_notes", "ethical_flags"],
    "additionalProperties": False,
}


async def _round6(transcript: str, brief: str, gate: asyncio.Semaphore) -> dict:
    registry = load_registry()
    access, ethics = registry.get("acc_access_reality"), registry.get("eth_ethics")

    return await llm.structured(
        system=f"""You are the feasibility and ethics gate, running jointly as two seats.

{access.name}: {access.system_prompt}

{ethics.name}: {ethics.system_prompt}

{prompts.house_rules()}""",
        user=f"{brief}\n\n## Panel transcript\n\n{transcript}",
        schema=_GATE_SCHEMA,
        gate=gate,
        max_tokens=2500,
    )


# ----------------------------------------------------------------- round 7

_SYNTHESIS_SCHEMA = {
    "type": "object",
    "properties": {
        "headline": {"type": "string", "description": "One sentence under 16 words a tired person could read and feel oriented by."},
        "summary": {"type": "string", "description": "Two short paragraphs: what the panel converged on, and what it could not settle. Address the reader as 'you'."},
        "discriminating_observations": {
            **_STRINGS,
            "description": "Things the person or their clinician could actually observe or measure "
            "that would decide between the panel's readings. Each must be real and reachable.",
        },
        "questions": {
            "type": "array",
            "minItems": 2,
            "items": {
                "type": "object",
                "properties": {
                    "question": {"type": "string", "description": "Phrased the way they could say it out loud to a pharmacist."},
                    "why": {"type": "string", "description": "One sentence on what it would resolve."},
                    "tier": {"type": "string", "enum": [t.value for t in AccessTier]},
                    "about": _STRINGS,
                },
                "required": ["question", "why", "tier", "about"],
                "additionalProperties": False,
            },
        },
        "dissent": {
            "type": "array",
            "minItems": 1,
            "description": "Disagreements the panel did not resolve. NEVER empty this to make the "
            "output tidier - an unresolved disagreement is information the reader needs.",
            "items": {
                "type": "object",
                "properties": {
                    "topic": {"type": "string"},
                    "side_a": {"type": "string"},
                    "side_a_agents": _STRINGS,
                    "side_b": {"type": "string"},
                    "side_b_agents": _STRINGS,
                    "what_would_settle_it": {"type": "string"},
                },
                "required": ["topic", "side_a", "side_a_agents", "side_b", "side_b_agents", "what_would_settle_it"],
                "additionalProperties": False,
            },
        },
        "what_this_cannot_tell": {
            **_STRINGS,
            "description": "What a panel like this genuinely cannot know about this person. Concrete, not performed modesty.",
        },
        "correlated_model_note": {
            "type": "string",
            "description": "One or two plain sentences saying that every expert here is generated "
            "by the same underlying model, so several agreeing is much weaker evidence than "
            "several independent clinicians agreeing. Ordinary words, no jargon.",
        },
    },
    "required": ["headline", "summary", "discriminating_observations", "questions", "dissent", "what_this_cannot_tell", "correlated_model_note"],
    "additionalProperties": False,
}


async def _round7(
    brief: str, transcript: str, pruned: dict, lived: str, gate_notes: dict,
    gate: asyncio.Semaphore,
) -> dict:
    registry = load_registry()
    return await llm.structured(
        system=f"""{prompts.agent_system(registry.get("gov_synthesizer"))}

You are writing the closing output for the person whose regimen this is. They are not a clinician. They may be tired, worried, and reading this on a phone.

What matters:
- Separate what the panel agreed on from what it could not settle, with equal prominence. Never return an empty dissent list.
- Attribute honestly. Only list an expert as holding a position if they actually said it.
- Every question must be sayable out loud to a pharmacist, and tiered by what it costs to ask.
- No instruction to start, stop, or change anything. Not once, not implied.
- Plain, warm, unpatronising. Short sentences. No jargon, no filler.""",
        user=f"""{brief}

## Full transcript
{transcript}

## Concerns that survived pruning
{llm.dumps(pruned.get("kept", []))}

## Concerns the panel dropped
{llm.dumps(pruned.get("dropped", []))}

## Lived-experience review
{lived}

## Feasibility and ethics gate
{llm.dumps(gate_notes)}""",
        schema=_SYNTHESIS_SCHEMA,
        gate=gate,
        max_tokens=8000,
        model=get_settings().panel_synthesis_model,
    )


# ----------------------------------------------------------------- round 8

_VETO_SCHEMA = {
    "type": "object",
    "properties": {
        "released": {"type": "boolean", "description": "False only if the output cannot be made safe by rewriting."},
        "block_reason": {"type": "string", "description": "Empty unless released is false."},
        "headline": {"type": "string", "description": "The headline, rewritten if it needed it, otherwise unchanged."},
        "summary": {"type": "string", "description": "The summary, rewritten if it needed it, otherwise unchanged."},
        "rewrites": {
            **_STRINGS,
            "description": "What you changed and why. Empty if nothing needed changing.",
        },
        "urgent_note": {
            "type": "string",
            "description": "Empty unless something here warrants same-day contact with a "
            "pharmacist or prescriber; then one plain sentence saying so and why.",
        },
    },
    "required": ["released", "block_reason", "headline", "summary", "rewrites", "urgent_note"],
    "additionalProperties": False,
}


async def _round8(synthesis: dict, gate: asyncio.Semaphore) -> dict:
    registry = load_registry()
    return await llm.structured(
        system=f"""{prompts.agent_system(registry.get("gov_safety"))}

You hold the final gate on output that is about to be shown to the person whose regimen this is.

Check three things:
1. Any sentence that tells them to start, stop, change, or skip a medication - rewrite it as a question for their prescriber. This is the one thing that must never ship.
2. Anything acutely dangerous buried mid-output that should be surfaced with a same-day route to a pharmacist - set urgent_note.
3. Alarm with no reachable next step - rewrite so there is one.

Return the headline and summary either unchanged or rewritten. Err toward releasing accurate information framed well: withholding what is true about someone's own regimen is its own harm, and blocking should be a last resort.""",
        user=llm.dumps(synthesis),
        schema=_VETO_SCHEMA,
        gate=gate,
        max_tokens=3000,
        model=get_settings().panel_synthesis_model,
    )


# ----------------------------------------------------------------- assembly

def _parse_concerns(pruned: dict) -> tuple[list[Concern], list[DroppedConcern]]:
    kept = []
    for c in pruned.get("kept") or []:
        try:
            kept.append(Concern(
                title=c.get("title", ""),
                detail=c.get("detail", ""),
                involved=[x for x in c.get("involved") or [] if isinstance(x, str)],
                raised_by=[x for x in c.get("raised_by") or [] if isinstance(x, str)],
                evidence_grade=EvidenceGrade(c.get("evidence_grade", "mechanism")),
                confidence=min(max(float(c.get("confidence", 0.5)), 0.0), 1.0),
                data_caveat=(c.get("data_caveat") or "").strip(),
            ))
        except (ValueError, TypeError):
            continue

    dropped = [
        DroppedConcern(
            title=d.get("title", ""),
            reason=d.get("reason", ""),
            dropped_by=[x for x in d.get("dropped_by") or [] if isinstance(x, str)],
        )
        for d in pruned.get("dropped") or []
        if d.get("title")
    ]
    return kept, dropped


def _parse_synthesis(raw: dict, spoke: set[str]) -> dict:
    """Strip attributions to experts who never spoke, so the UI cannot
    over-claim who backed a position."""

    keep = lambda ids: [i for i in (ids or []) if i in spoke]  # noqa: E731

    questions = []
    for q in raw.get("questions") or []:
        try:
            tier = AccessTier(q.get("tier", "pharmacist"))
        except ValueError:
            tier = AccessTier.pharmacist
        if q.get("question"):
            questions.append(Question(
                question=q["question"], why=q.get("why", ""), tier=tier,
                about=[a for a in q.get("about") or [] if isinstance(a, str)],
            ))

    dissent = [
        Dissent(
            topic=d.get("topic", ""),
            side_a=d.get("side_a", ""), side_a_agents=keep(d.get("side_a_agents")),
            side_b=d.get("side_b", ""), side_b_agents=keep(d.get("side_b_agents")),
            what_would_settle_it=d.get("what_would_settle_it", ""),
        )
        for d in raw.get("dissent") or []
        if d.get("topic")
    ]

    strings = lambda xs: [x for x in (xs or []) if isinstance(x, str) and x.strip()]  # noqa: E731

    return {
        "questions": questions,
        "dissent": dissent,
        "discriminating_observations": strings(raw.get("discriminating_observations")),
        "what_this_cannot_tell": strings(raw.get("what_this_cannot_tell")),
        "correlated_model_note": (raw.get("correlated_model_note") or "").strip(),
    }


async def run_panel(
    items: list[MedListItem],
    records: list[NormalizedRecord],
    findings: list[Finding],
    note: str = "",
    emit: Emit | None = None,
) -> PanelReview:
    """Run all eight rounds and return the review. ``emit`` receives progress
    events as each round and agent turn starts and finishes."""

    emit = emit or _noop
    registry = load_registry()
    review = PanelReview(context_note=note, registry_version=registry.version)

    # Round 1a: the urgency gate, before any API call is made.
    urgency = screen(note)
    review.urgency, review.urgency_message = urgency.level, urgency.message
    if urgency.level is Urgency.urgent:
        review.halted, review.halt_reason = True, "urgent"
        emit({"type": "halt", "reason": "urgent", "message": urgency.message})
        return review

    if not [i for i in items if i.status.value == "active"]:
        review.halted, review.halt_reason = True, "empty-list"
        review.urgency_message = "There is nothing on the list for a panel to review yet."
        emit({"type": "halt", "reason": "empty-list"})
        return review

    gate = llm.semaphore()
    brief = prompts.regimen_brief(items, records, findings, note)

    # Round 1b: framing and seating.
    framing = await _round1(brief, gate, emit)
    review.framing = framing["framing"]
    review.unknowns = framing["unknowns"]
    review.seated = framing["seated"]
    review.not_seated = framing["not_seated"]
    emit({"type": "panel", "seated": [s.model_dump() for s in review.seated],
          "not_seated": [s.model_dump() for s in review.not_seated],
          "framing": review.framing, "unknowns": review.unknowns})

    # Round 2: independent review, in parallel and blind.
    emit({"type": "round", "n": 2, "name": "Independent review"})
    seated_agents = [registry.get(s.agent_id) for s in review.seated]
    settled = await asyncio.gather(
        *(_take(a, brief, gate, emit) for a in seated_agents), return_exceptions=True
    )
    for agent, result in zip(seated_agents, settled):
        if isinstance(result, BaseException):
            review.notices.append(f"{agent.name} could not answer, so the panel carried on without them.")
            emit({"type": "take-end", "agent_id": agent.id})
        elif result.text.strip().startswith(HALT_TOKEN):
            review.halted, review.halt_reason = True, "agent-flagged-urgent"
            review.urgency = Urgency.concern
            review.urgency_message = (
                f"{agent.name} flagged something here as needing attention today rather than "
                "at your next appointment. Please call a pharmacist or your prescriber."
            )
            emit({"type": "halt", "reason": "agent-flagged-urgent"})
            return review
        else:
            review.takes.append(result)

    if len(review.takes) < 2:
        raise RuntimeError("Not enough of the panel could answer to hold a discussion.")

    # Round 3: cross-examination.
    emit({"type": "round", "n": 3, "name": "Cross-examination"})
    pairs = await _round3_pairs(review.takes, gate)
    challenged = await asyncio.gather(
        *(_challenge(p, review.takes, brief, gate, emit) for p in pairs), return_exceptions=True
    )
    review.challenges = [
        c for c in challenged
        if isinstance(c, Challenge) and not c.text.strip().startswith(HALT_TOKEN)
    ]

    transcript = "\n\n".join(
        ["### Opening statements"]
        + [f"**{registry.get(t.agent_id).name}** ({t.agent_id})\n{t.text}" for t in review.takes]
        + ["### Cross-examination"]
        + [
            f"**{registry.get(c.agent_id).name}** → **{registry.get(c.target_id).name}** "
            f"(on {c.about})\n{c.text}"
            for c in review.challenges
        ]
    )

    # Rounds 4-6.
    emit({"type": "round", "n": 4, "name": "Significance pruning"})
    pruned = await _round4(transcript, brief, gate)
    review.concerns, review.dropped = _parse_concerns(pruned)

    emit({"type": "round", "n": 5, "name": "Lived-experience review"})
    lived = await _round5(transcript, brief, {s.agent_id for s in review.seated}, gate)

    emit({"type": "round", "n": 6, "name": "Feasibility and ethics"})
    gate_notes = await _round6(transcript, brief, gate)

    # Round 7: synthesis.
    emit({"type": "round", "n": 7, "name": "Synthesis and dissent"})
    synthesis = await _round7(brief, transcript, pruned, lived, gate_notes, gate)
    parsed = _parse_synthesis(synthesis, {t.agent_id for t in review.takes})
    review.headline = (synthesis.get("headline") or "").strip()
    review.summary = (synthesis.get("summary") or "").strip()
    for field, value in parsed.items():
        setattr(review, field, value)

    # Round 8: the veto.
    emit({"type": "round", "n": 8, "name": "Safety review"})
    veto = await _round8({**synthesis, "concerns": pruned.get("kept", [])}, gate)
    if not veto.get("released", True):
        review.halted, review.halt_reason = True, "safety-veto"
        review.urgency_message = (
            veto.get("block_reason")
            or "The safety review held this back. Please take your list to a pharmacist."
        )
        emit({"type": "halt", "reason": "safety-veto"})
        return review

    review.headline = (veto.get("headline") or review.headline).strip()
    review.summary = (veto.get("summary") or review.summary).strip()
    if urgent := (veto.get("urgent_note") or "").strip():
        review.urgency = max(review.urgency, Urgency.concern, key=_URGENCY_ORDER.get)
        review.urgency_message = urgent
    review.notices += [r for r in veto.get("rewrites") or [] if isinstance(r, str)]

    emit({"type": "done"})
    return review


_URGENCY_ORDER = {Urgency.clear: 0, Urgency.sensitive: 1, Urgency.concern: 2, Urgency.urgent: 3}
