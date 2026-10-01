"""Models for one run of the eight-round panel.

These stay separate from ``app.schemas`` because the panel is a distinct
subsystem: it consumes the medication list but nothing in the ingestion,
storage, or screening path depends on it.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from enum import Enum

from pydantic import BaseModel, Field


class Urgency(str, Enum):
    """Graded, not binary. The mental-health original halts on any match,
    and its own notes admit that a halt helps nobody; here the grade decides
    how the output is framed rather than whether it exists."""

    urgent = "urgent"        # emergency symptoms - route to care, do not debate
    concern = "concern"      # same-day pharmacist or prescriber contact, then continue
    sensitive = "sensitive"  # a care note alongside the normal output
    clear = "clear"


class UrgencyScreen(BaseModel):
    level: Urgency = Urgency.clear
    matched: list[str] = Field(default_factory=list, description="Phrases that triggered the grade.")
    message: str = ""


class EvidenceGrade(str, Enum):
    outcome = "outcome"              # outcome data in a comparable population
    surrogate = "surrogate"          # surrogate or lab endpoint
    pharmacokinetic = "pharmacokinetic"  # healthy-volunteer exposure study
    case_report = "case_report"
    mechanism = "mechanism"          # plausible mechanism only


class AccessTier(str, Enum):
    free_now = "free_now"            # costs nothing, can be done today
    pharmacist = "pharmacist"        # a free walk-in conversation
    appointment = "appointment"      # routine visit
    specialist = "specialist"        # referral, likely a wait


class Seat(BaseModel):
    agent_id: str
    name: str = ""
    reason: str = ""


class Take(BaseModel):
    """One agent's independent first read, written without seeing any other."""

    agent_id: str
    text: str


class Challenge(BaseModel):
    agent_id: str
    target_id: str
    about: str = ""
    text: str


class Concern(BaseModel):
    """A surviving finding, after pruning, with everything the reader needs
    to judge how much weight it deserves."""

    title: str
    detail: str
    involved: list[str] = Field(default_factory=list, description="Drugs or supplements involved.")
    raised_by: list[str] = Field(default_factory=list, description="Agent ids that raised it.")
    evidence_grade: EvidenceGrade = EvidenceGrade.mechanism
    confidence: float = Field(0.5, ge=0.0, le=1.0)
    data_caveat: str = Field(
        "", description="Set when the concern rests on a low-confidence reading of the list."
    )


class DroppedConcern(BaseModel):
    """Something the panel considered and dismissed. Kept and shown: the
    reader learning that a scary-sounding pair was examined and judged inert
    is as useful as the concerns that survived."""

    title: str
    reason: str
    dropped_by: list[str] = Field(default_factory=list)


class Dissent(BaseModel):
    """An unresolved disagreement. Never collapsed to make the output tidier."""

    topic: str
    side_a: str
    side_a_agents: list[str] = Field(default_factory=list)
    side_b: str
    side_b_agents: list[str] = Field(default_factory=list)
    what_would_settle_it: str


class Question(BaseModel):
    """Something the person can say out loud to a pharmacist or prescriber."""

    question: str
    why: str
    tier: AccessTier = AccessTier.pharmacist
    about: list[str] = Field(default_factory=list, description="Drugs the question concerns.")


class ObservationStatus(str, Enum):
    open = "open"
    answered = "answered"
    dismissed = "dismissed"


class ObservationSource(str, Enum):
    dissent = "dissent"              # what_would_settle_it on an unresolved split
    discriminating = "discriminating"  # a general discriminating observation


class TrackedObservation(BaseModel):
    """Something the panel said would settle a question, turned into something
    the person can actually go and find out.

    This is the loop the review would otherwise leave open: the panel names
    the observation that would decide a disagreement, and without this the
    reader is left holding it on a notepad. Answered observations are fed back
    into the next run, where an agent whose concede-when condition is now met
    is expected to concede."""

    id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    text: str
    source: ObservationSource = ObservationSource.discriminating
    topic: str = Field("", description="The dissent or concern this came from.")
    panel_review_id: str = ""
    created_at: datetime = Field(default_factory=datetime.utcnow)

    status: ObservationStatus = ObservationStatus.open
    answer: str = ""
    answered_at: datetime | None = None


class Resolved(BaseModel):
    """A question a previous panel left open that the answers have now settled."""

    topic: str
    settled_by: str = Field(..., description="The answer that did it, in the person's words.")
    outcome: str = Field(..., description="What the panel now concludes, and who conceded.")


class PanelReview(BaseModel):
    """The full output of one panel run."""

    id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    created_at: datetime = Field(default_factory=datetime.utcnow)
    registry_version: str = ""

    context_note: str = Field("", description="Free text the person supplied, if any.")
    urgency: Urgency = Urgency.clear
    urgency_message: str = ""

    framing: str = ""
    unknowns: list[str] = Field(default_factory=list)
    seated: list[Seat] = Field(default_factory=list)
    not_seated: list[Seat] = Field(default_factory=list)

    headline: str = ""
    summary: str = ""
    concerns: list[Concern] = Field(default_factory=list)
    dropped: list[DroppedConcern] = Field(default_factory=list)
    discriminating_observations: list[str] = Field(default_factory=list)
    questions: list[Question] = Field(default_factory=list)
    dissent: list[Dissent] = Field(default_factory=list)
    resolved: list[Resolved] = Field(default_factory=list)
    what_this_cannot_tell: list[str] = Field(default_factory=list)
    correlated_model_note: str = ""

    # The work, kept so the reader can see how a conclusion was reached.
    takes: list[Take] = Field(default_factory=list)
    challenges: list[Challenge] = Field(default_factory=list)

    halted: bool = False
    halt_reason: str = ""
    notices: list[str] = Field(default_factory=list)
