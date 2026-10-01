"""The urgency screen the safety governor runs in round 1.

Deliberately deterministic and local: it runs before any API call, costs
nothing, and cannot be talked out of its verdict by the text it is reading.

It grades rather than halting on any match. The mental-health system this
protocol comes from halts the run on a broad regex list, and its own install
notes concede the problem - a halt helps nobody. Here only genuine emergency
language stops the panel, and it stops it in order to say something more
useful than a medication review would have been.

Scope: this reads the free-text note a person adds about how they are
feeling. It does not grade the regimen itself - a dangerous combination is a
finding for the panel to reason about, not a reason to refuse to run.
"""

from __future__ import annotations

import re

from app.panel.schemas import Urgency, UrgencyScreen

# Emergency presentations. These stop the panel: a medication review is the
# wrong response to someone describing an emergency happening now.
_URGENT = (
    r"overdose(?:d|ing)?",
    r"took (?:too many|the whole bottle|all of (?:my|the) pills)",
    r"can('?t| ?not) breathe",
    r"trouble breathing",
    r"chest pain",
    r"crushing (?:chest )?pressure",
    r"face (?:is )?(?:drooping|droops)",
    r"slurred speech",
    r"coughing (?:up )?blood",
    r"vomiting blood",
    r"black(?:,| and)? tarry stool",
    r"bleeding (?:that )?(?:won'?t|will not) stop",
    r"uncontrolled bleeding",
    r"throat (?:is )?closing",
    r"anaphyla(?:xis|ctic)",
    r"passed out",
    r"lost consciousness",
    r"seizure",
    r"high fever (?:and|with) (?:stiff|rigid)",
)

# Worth same-day contact, but the review still helps and still runs.
_CONCERN = (
    r"fainted",
    r"(?:feel|feeling) faint",
    r"heart (?:is )?(?:racing|pounding)",
    r"irregular heartbeat",
    r"new (?:rash|bruis\w+|bleeding)",
    r"bruising easily",
    r"blood in (?:my )?(?:urine|stool)",
    r"yellow(?:ing)? (?:skin|eyes)",
    r"jaundice",
    r"severe (?:dizziness|vomiting|diarrhea|headache)",
    r"(?:can'?t|cannot) (?:keep|hold) (?:anything|food|water) down",
    r"swelling in (?:my )?(?:legs|ankles|face)",
    r"confus(?:ed|ion) (?:since|after)",
)

# Topics that earn a care note alongside the normal output.
_SENSITIVE = (
    r"suicid\w*",
    r"kill myself",
    r"end (?:it|my life)",
    r"self[- ]harm",
    r"hurting myself",
    r"stopped taking",
    r"ran out of",
    r"can'?t afford",
    r"skipping doses",
    r"pregnan\w*",
    r"breastfeed\w*",
    r"drinking (?:heavily|a lot)",
)

_URGENT_MESSAGE = (
    "What you have described needs help now, not a medication review. "
    "Call emergency services (911 in the US) or get to an emergency room. "
    "If you can, take your medication list or the bottles with you. "
    "This panel did not run - it would have been the wrong thing to give you."
)

_CONCERN_MESSAGE = (
    "What you described is worth raising with a pharmacist or your prescriber "
    "today rather than at your next appointment. The review below still ran, "
    "and the panel was told what you wrote."
)

_SENSITIVE_MESSAGE = (
    "Some of what you wrote touches on things this tool is not the right "
    "support for on its own. The review still ran. If any of it involves "
    "thoughts of harming yourself, please talk to someone today - in the US "
    "you can call or text 988."
)


def _matches(text: str, patterns: tuple[str, ...]) -> list[str]:
    found = []
    for pattern in patterns:
        match = re.search(rf"\b{pattern}\b", text, flags=re.IGNORECASE)
        if match:
            found.append(match.group(0).lower())
    return found


def screen(note: str) -> UrgencyScreen:
    """Grade a free-text note. Empty text is always clear."""

    text = (note or "").strip()
    if not text:
        return UrgencyScreen()

    if found := _matches(text, _URGENT):
        return UrgencyScreen(level=Urgency.urgent, matched=found, message=_URGENT_MESSAGE)
    if found := _matches(text, _CONCERN):
        return UrgencyScreen(level=Urgency.concern, matched=found, message=_CONCERN_MESSAGE)
    if found := _matches(text, _SENSITIVE):
        return UrgencyScreen(level=Urgency.sensitive, matched=found, message=_SENSITIVE_MESSAGE)
    return UrgencyScreen()
