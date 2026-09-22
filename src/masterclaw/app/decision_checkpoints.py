from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping
from typing import Protocol

from pydantic import BaseModel

from masterclaw.context.assembler import AssembledContext
from masterclaw.storage.sqlite import SQLiteStore


class DecisionPipeline[OutputT: BaseModel](Protocol):
    @property
    def output_type(self) -> type[OutputT]: ...

    async def run(self, *, task: str, context: AssembledContext) -> OutputT: ...


class DecisionContextChangedError(RuntimeError):
    """A durable decision exists, but it was made for a different input snapshot."""


def decision_output_type_name(output_type: type[BaseModel]) -> str:
    schema = json.dumps(
        output_type.model_json_schema(),
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode()
    fingerprint = hashlib.sha256(schema).hexdigest()[:16]
    return f"{output_type.__module__}.{output_type.__qualname__}:v1:{fingerprint}"


def decision_input_fingerprint(payload: Mapping[str, object]) -> str:
    canonical = json.dumps(
        dict(payload),
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode()
    return hashlib.sha256(canonical).hexdigest()


async def run_checkpointed_decision[OutputT: BaseModel](
    *,
    store: SQLiteStore,
    event_id: str,
    pipeline_key: str,
    pipeline: DecisionPipeline[OutputT],
    task: str,
    context: AssembledContext,
    game_id: str | None,
    input_fingerprint: str | None = None,
) -> OutputT:
    """Return the first typed decision durably chosen for one pipeline stage.

    The checkpoint is written immediately after typed validation and before the caller may
    mutate canonical state. A retry therefore cannot switch branches or targets merely because
    the model produced a different valid answer.
    """

    output_type = pipeline.output_type
    output_type_name = decision_output_type_name(output_type)
    try:
        checkpoint = store.decision_checkpoint(
            event_id=event_id,
            pipeline_key=pipeline_key,
            output_type=output_type_name,
            game_id=game_id,
            input_fingerprint=input_fingerprint,
        )
    except RuntimeError as error:
        if str(error) == "decision checkpoint input fingerprint mismatch":
            raise DecisionContextChangedError(str(error)) from error
        raise
    if checkpoint is not None:
        return output_type.model_validate(checkpoint)
    decision = await pipeline.run(task=task, context=context)
    try:
        canonical = store.checkpoint_decision(
            event_id=event_id,
            pipeline_key=pipeline_key,
            output_type=output_type_name,
            payload=decision.model_dump(mode="json"),
            game_id=game_id,
            input_fingerprint=input_fingerprint,
        )
    except RuntimeError as error:
        if str(error) == "decision checkpoint input fingerprint mismatch":
            raise DecisionContextChangedError(str(error)) from error
        raise
    return output_type.model_validate(canonical)
