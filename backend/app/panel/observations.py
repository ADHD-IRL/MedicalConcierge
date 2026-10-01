"""Turning a review's open questions into things a person can go and answer.

The panel's most useful sentences are the ones naming what would settle a
disagreement. On their own they are a to-do list the reader has to keep in
their head; tracked, they become the input that makes the next run converge
rather than repeat itself.
"""

from __future__ import annotations

import re

from app.panel.schemas import (
    ObservationSource,
    ObservationStatus,
    PanelReview,
    TrackedObservation,
)


def _normalise(text: str) -> str:
    """For de-duplication only: a panel re-run usually phrases the same
    observation slightly differently, and the reader should not be handed the
    same question twice because a comma moved."""
    return re.sub(r"[^a-z0-9 ]+", "", text.lower()).strip()


def derive(review: PanelReview, existing: list[TrackedObservation]) -> list[TrackedObservation]:
    """Observations worth tracking from one review, excluding any the person
    has already answered or dismissed."""

    seen = {_normalise(o.text) for o in existing}
    fresh: list[TrackedObservation] = []

    def add(text: str, source: ObservationSource, topic: str) -> None:
        text = (text or "").strip()
        key = _normalise(text)
        if len(key) < 8 or key in seen:
            return
        seen.add(key)
        fresh.append(TrackedObservation(
            text=text, source=source, topic=topic, panel_review_id=review.id
        ))

    # Dissent first: these settle an argument rather than merely informing one,
    # so they are the observations most worth the person's effort.
    for d in review.dissent:
        add(d.what_would_settle_it, ObservationSource.dissent, d.topic)
    for obs in review.discriminating_observations:
        add(obs, ObservationSource.discriminating, "")

    return fresh


def answered(observations: list[TrackedObservation]) -> list[TrackedObservation]:
    return [o for o in observations if o.status is ObservationStatus.answered and o.answer.strip()]


def open_only(observations: list[TrackedObservation]) -> list[TrackedObservation]:
    return [o for o in observations if o.status is ObservationStatus.open]
