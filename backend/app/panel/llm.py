"""Async Anthropic access for the panel.

The ingestion path is synchronous and single-request; the panel is neither -
round 2 fans out to every seated agent at once - so it gets its own thin
client layer with a concurrency bound and a forced-tool-call helper for
structured rounds, matching the pattern already used in the extractor.
"""

from __future__ import annotations

import asyncio
import json
from typing import Any

import anthropic

from app.config import get_settings

_TOOL_NAME = "record"


class PanelUnavailable(RuntimeError):
    """Raised when the panel cannot run at all - no API key configured."""


def _client() -> anthropic.AsyncAnthropic:
    settings = get_settings()
    if not settings.anthropic_api_key.strip():
        raise PanelUnavailable(
            "The panel needs an Anthropic API key. Add one to backend/.env and restart."
        )
    return anthropic.AsyncAnthropic(api_key=settings.anthropic_api_key)


def semaphore() -> asyncio.Semaphore:
    """One bound per run, so a wide panel cannot open thirty sockets at once."""
    return asyncio.Semaphore(get_settings().panel_concurrency)


async def prose(
    system: str,
    user: str,
    *,
    gate: asyncio.Semaphore,
    max_tokens: int = 1200,
    model: str | None = None,
) -> str:
    """Free-text turn - an agent's take or challenge."""

    settings = get_settings()
    async with gate:
        response = await _client().messages.create(
            model=model or settings.panel_model,
            max_tokens=max_tokens,
            system=system,
            messages=[{"role": "user", "content": user}],
        )
    return "".join(block.text for block in response.content if block.type == "text").strip()


async def structured(
    system: str,
    user: str,
    schema: dict[str, Any],
    *,
    gate: asyncio.Semaphore,
    max_tokens: int = 4000,
    model: str | None = None,
) -> dict[str, Any]:
    """A round whose output has to be machine-readable. Uses a forced tool
    call rather than JSON-in-prose, the same way extraction does, because a
    round that silently returns prose would break the round after it."""

    settings = get_settings()
    async with gate:
        response = await _client().messages.create(
            model=model or settings.panel_model,
            max_tokens=max_tokens,
            system=system,
            tools=[{
                "name": _TOOL_NAME,
                "description": "Record this round's output.",
                "input_schema": schema,
            }],
            tool_choice={"type": "tool", "name": _TOOL_NAME},
            messages=[{"role": "user", "content": user}],
        )

    for block in response.content:
        if block.type == "tool_use" and block.name == _TOOL_NAME:
            return dict(block.input)

    # A forced tool call that produced no tool block means the response was
    # truncated or refused; surface it rather than returning a silent empty.
    raise RuntimeError(f"Round produced no structured output (stop_reason={response.stop_reason}).")


def dumps(value: Any) -> str:
    return json.dumps(value, indent=2, default=str)
