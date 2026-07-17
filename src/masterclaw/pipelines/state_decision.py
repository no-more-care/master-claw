from __future__ import annotations

from functools import lru_cache
from typing import Literal, cast

from pydantic import BaseModel, ConfigDict, Field, create_model

from masterclaw.app.scenarios import CommandId, Scenario, ScenarioId
from masterclaw.pipelines.base import BoundedJsonPipeline, CompletionPort


class StateDecisionBase(BaseModel):
    """A state model chooses a command; it never writes the player-facing answer."""

    model_config = ConfigDict(extra="forbid")

    argument: str | None
    confidence: float = Field(ge=0, le=1)
    evidence: str = Field(min_length=1, max_length=300)


@lru_cache(maxsize=len(ScenarioId))
def state_decision_type(scenario_id: ScenarioId) -> type[StateDecisionBase]:
    """Build a Pydantic contract whose command enum is closed to one scenario."""
    from masterclaw.app.scenarios import SCENARIOS

    scenario = SCENARIOS[scenario_id]
    allowed = tuple(sorted(scenario.llm_commands, key=lambda command: command.value))
    command_literal = Literal.__getitem__(allowed)
    model = create_model(
        f"{scenario_id.value.title().replace('_', '')}StateDecision",
        __base__=StateDecisionBase,
        command=(command_literal, ...),
    )
    return cast(type[StateDecisionBase], model)


def create_state_decision_pipeline(
    completion: CompletionPort, scenario: Scenario
) -> BoundedJsonPipeline[StateDecisionBase]:
    allowed = ", ".join(sorted(command.value for command in scenario.llm_commands))
    return BoundedJsonPipeline(
        completion=completion,
        output_type=state_decision_type(scenario.id),
        output_tool=f"submit_{scenario.id.value}_decision",
        static_system=(
            "You are a state router for one tabletop-game scenario. Choose exactly one "
            "command from the supplied typed contract. You decide what command should run; "
            "you do not answer the player, reveal state, resolve an action, or invent facts. "
            f"Scenario: {scenario.id.value}. Allowed commands: {allowed}. Use argument only "
            "when the command needs a name or other short selector. When participant identity "
            "matters, use current_scene.participant_characters to resolve player ids to character "
            "names. Never emit a Discord user mention in argument or evidence."
            " When compound_play is allowed, choose it for a single message containing two or "
            "more ordered gameplay parts that must all be preserved; do not collapse such a "
            "message into only its final action or question."
        ),
    )


class StateDecisionRouter:
    def __init__(self, completion: CompletionPort) -> None:
        self._completion = completion

    def pipeline_for(self, scenario: Scenario) -> BoundedJsonPipeline[StateDecisionBase]:
        return create_state_decision_pipeline(self._completion, scenario)


def command_of(decision: StateDecisionBase) -> CommandId:
    return CommandId(decision.model_dump()["command"])
