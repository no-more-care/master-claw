from __future__ import annotations

import json
import logging
import time
from typing import Protocol

from pydantic import BaseModel, ValidationError

logger = logging.getLogger(__name__)


class CompletionPort(Protocol):
    async def complete(self, *, system: str, user: str) -> str: ...


class PipelineValidationError(ValueError):
    pass


class BoundedJsonPipeline[OutputT: BaseModel]:
    """One initial completion and at most one schema-repair completion."""

    def __init__(
        self,
        *,
        completion: CompletionPort,
        output_type: type[OutputT],
        static_system: str,
    ) -> None:
        self._completion = completion
        self._output_type = output_type
        self._static_system = static_system

    async def run(self, *, task: str, dynamic_context: str) -> OutputT:
        started = time.monotonic()
        schema = json.dumps(self._output_type.model_json_schema(), ensure_ascii=False)
        user = (
            f"TASK\n{task}\n\nDYNAMIC CONTEXT\n{dynamic_context}\n\n"
            f"Return JSON only. Output schema:\n{schema}"
        )
        first = await self._completion.complete(system=self._static_system, user=user)
        try:
            parsed = self._parse(first)
            logger.info(
                "pipeline_complete output=%s repaired=false duration_ms=%d",
                self._output_type.__name__,
                int((time.monotonic() - started) * 1000),
            )
            return parsed
        except PipelineValidationError as first_error:
            repair = (
                "Repair the candidate to match the schema. Do not add facts. "
                f"Validation error: {first_error}\nSchema: {schema}\nCandidate:\n{first}"
            )
            second = await self._completion.complete(system=self._static_system, user=repair)
            try:
                parsed = self._parse(second)
                logger.info(
                    "pipeline_complete output=%s repaired=true duration_ms=%d",
                    self._output_type.__name__,
                    int((time.monotonic() - started) * 1000),
                )
                return parsed
            except PipelineValidationError as second_error:
                logger.error(
                    "pipeline_invalid output=%s duration_ms=%d",
                    self._output_type.__name__,
                    int((time.monotonic() - started) * 1000),
                )
                raise PipelineValidationError(
                    f"pipeline output invalid after one repair: {second_error}"
                ) from second_error

    def _parse(self, value: str) -> OutputT:
        candidate = value.strip()
        if candidate.startswith("```"):
            lines = candidate.splitlines()
            candidate = "\n".join(lines[1:-1])
            if candidate.lstrip().startswith("json"):
                candidate = candidate.lstrip()[4:].lstrip()
        try:
            return self._output_type.model_validate_json(candidate)
        except (ValidationError, ValueError) as error:
            raise PipelineValidationError(str(error)) from error
