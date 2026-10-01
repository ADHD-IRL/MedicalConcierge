"""Typed access to the subject-matter-expert registry in
``backend/data/panel_registry.json``.

The registry is data, not code: the roster, the house rules every agent
inherits, and the eight-round protocol all live in the JSON so the panel's
composition can be reviewed and changed without touching the engine.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

_REGISTRY_PATH = Path(__file__).resolve().parent.parent.parent / "data" / "panel_registry.json"

# Agents that never take a seat in the debate itself - they run the rounds.
GOVERNANCE_IDS = frozenset({"gov_moderator", "gov_synthesizer", "gov_safety"})


@dataclass(frozen=True)
class Agent:
    id: str
    name: str
    panel: str
    discipline: str
    focus: tuple[str, ...]
    stance: str
    system_prompt: str
    privileged_evidence: tuple[str, ...]
    typically_challenges: tuple[str, ...]
    concedes_when: str
    bias_watch: str
    required_round_output: str

    @property
    def is_governance(self) -> bool:
        return self.id in GOVERNANCE_IDS


@dataclass(frozen=True)
class Registry:
    version: str
    disclaimer: str
    house_rules: tuple[str, ...]
    protocol: dict
    agents: dict[str, Agent]

    @property
    def seatable(self) -> list[Agent]:
        """Everyone eligible to hold a position in the debate."""
        return [a for a in self.agents.values() if not a.is_governance]

    def panel_of(self, name: str) -> list[Agent]:
        return [a for a in self.agents.values() if a.panel == name]

    def get(self, agent_id: str) -> Agent | None:
        return self.agents.get(agent_id)


@lru_cache
def load_registry() -> Registry:
    raw = json.loads(_REGISTRY_PATH.read_text(encoding="utf-8"))

    agents = {}
    for item in raw["agents"]:
        agents[item["id"]] = Agent(
            id=item["id"],
            name=item["name"],
            panel=item["panel"],
            discipline=item["discipline"],
            focus=tuple(item["focus"]),
            stance=item["stance"],
            system_prompt=item["system_prompt"],
            privileged_evidence=tuple(item["privileged_evidence"]),
            typically_challenges=tuple(item["typically_challenges"]),
            concedes_when=item["concedes_when"],
            bias_watch=item["bias_watch"],
            required_round_output=item["required_round_output"],
        )

    return Registry(
        version=raw["version"],
        disclaimer=raw["disclaimer"],
        house_rules=tuple(raw["house_rules"]),
        protocol=raw["protocol"],
        agents=agents,
    )
