"""A backstop against the assistant telling someone to change their treatment.

This is deliberately narrow. The naive version - scanning for "stop taking" -
fires constantly on perfectly correct output, because reporting what a
clinician said ("they told you to stop the fish oil") and recording what the
person did ("you stopped it in March") are both core to what this assistant is
for. A guard that cried wolf on those would either be switched off or trained
around.

So it matches only language in which the ASSISTANT ITSELF is the one
recommending: a first-person recommendation, or a second-person imperative
about a medication change. When it fires, the answer is shown with a visible
notice rather than silently rewritten, because quietly editing a medical
answer is its own failure mode.

It is a backstop, not a guarantee. The primary control is the system prompt
and the structured response, which gives clinical advice nowhere to live.
"""

from __future__ import annotations

import logging
import re

# Stems, not whole words: "increase" would not match "increasing", which is
# exactly the form a recommendation tends to take.
_CHANGE = (
    r"(?:stop|start|begin|increas|decreas|reduc|rais|lower|doubl|halv|skip"
    r"|discontinu|switch|wean|taper|come off|get off|cut back)"
)

_PATTERNS = (
    # "I recommend you stop ...", "I'd suggest reducing ..."
    rf"\bI(?:'d| would)?\s+(?:recommend|suggest|advise)\b[^.?!]{{0,80}}\b{_CHANGE}\w*\b",
    # "you should stop ...", "you ought to increase ..."
    rf"\byou\s+(?:should|ought to|need to|must)\b[^.?!]{{0,60}}\b{_CHANGE}\w*\b",
    # "my advice is to stop ..."
    rf"\bmy\s+(?:advice|recommendation)\b[^.?!]{{0,60}}\b{_CHANGE}\w*\b",
    # "it would be best to stop taking ..."
    rf"\bit(?:'s| is| would be)\s+(?:best|safer|better|advisable)\b[^.?!]{{0,60}}\b{_CHANGE}\w*\b",
)

NOTICE = (
    "Heads up: part of that answer reads like advice about changing a medication. "
    "This assistant can't make that call and neither can anything it's built on - "
    "please put it to your pharmacist or prescriber before acting on it."
)


def check(text: str) -> str:
    """Returns the notice to attach, or an empty string when the answer is clean."""

    for pattern in _PATTERNS:
        match = re.search(pattern, text or "", flags=re.IGNORECASE)
        if match:
            logging.getLogger(__name__).warning(
                "Directive-language backstop fired on assistant output: %r",
                match.group(0)[:160],
            )
            return NOTICE
    return ""
