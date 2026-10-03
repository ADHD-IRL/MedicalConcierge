"""Models for the conversational layer.

Responses are structured rather than prose-with-markup on purpose: a native
client should be able to render citations as tappable chips and proposed
actions as confirm cards without parsing HTML out of a text blob. See
docs/APP_ROADMAP.md.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from enum import Enum

from pydantic import BaseModel, Field

from app.panel.schemas import Urgency


class Role(str, Enum):
    user = "user"
    assistant = "assistant"


class CitationKind(str, Enum):
    med_item = "med_item"
    lab = "lab"
    history = "history"
    observation = "observation"
    review = "review"
    finding = "finding"


class Citation(BaseModel):
    """Where in the person's own record an answer came from. The whole point
    of this layer over a general chatbot is that answers are traceable."""

    kind: CitationKind
    ref_id: str = ""
    label: str


class ActionKind(str, Enum):
    add_item = "add_item"
    update_item = "update_item"
    stop_item = "stop_item"
    answer_observation = "answer_observation"


class ProposedAction(BaseModel):
    """Something the assistant believes should change in the record, based on
    what the person just told it.

    Proposed, never applied. "They upped my warfarin to 7.5" should not
    silently rewrite a dose - the person confirms each one, because a
    misheard sentence that edits a medication list is exactly the failure
    this whole app exists to prevent."""

    id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    kind: ActionKind
    description: str = Field(..., description="Plain language, shown on the confirm card.")
    target_id: str = ""
    payload: dict = Field(default_factory=dict)
    applied: bool = False


class AssistantTurn(BaseModel):
    id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    role: Role
    text: str = ""
    created_at: datetime = Field(default_factory=datetime.utcnow)

    citations: list[Citation] = Field(default_factory=list)
    actions: list[ProposedAction] = Field(default_factory=list)
    questions_for_clinician: list[str] = Field(default_factory=list)

    urgency: Urgency = Urgency.clear
    urgency_message: str = ""
    # Set when the directive-language backstop fired. Visible, not silent.
    guard_notice: str = ""
