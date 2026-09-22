from __future__ import annotations

import json
import logging
import re
import time
from dataclasses import dataclass
from typing import Protocol

from pydantic import BaseModel, ValidationError

from masterclaw.context.assembler import AssembledContext
from masterclaw.telemetry import stage_span

logger = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class CompletionResult:
    payload: str
    used_tool: bool
    protocol_error: str | None = None


class CompletionPort(Protocol):
    async def complete(
        self,
        *,
        system: str,
        context: str,
        task: str,
        output_type: type[BaseModel],
        tool_name: str,
        max_output_tokens: int | None = None,
    ) -> CompletionResult: ...


class FallbackCompletionPort:
    """Use a secondary model when the primary call or its typed result is unusable."""

    def __init__(self, primary: CompletionPort, fallback: CompletionPort) -> None:
        self._primary = primary
        self._fallback = fallback

    async def complete(
        self,
        *,
        system: str,
        context: str,
        task: str,
        output_type: type[BaseModel],
        tool_name: str,
        max_output_tokens: int | None = None,
    ) -> CompletionResult:
        arguments = {
            "system": system,
            "context": context,
            "task": task,
            "output_type": output_type,
            "tool_name": tool_name,
            "max_output_tokens": max_output_tokens,
        }
        try:
            with stage_span(
                "fallback.primary",
                component="completion_fallback",
                operation=output_type.__name__,
            ):
                result = await self._primary.complete(**arguments)
            if result.protocol_error is not None:
                raise ValueError(result.protocol_error)
            candidate = result.payload.strip()
            if candidate.startswith("```"):
                lines = candidate.splitlines()
                candidate = "\n".join(lines[1:-1])
                if candidate.lstrip().startswith("json"):
                    candidate = candidate.lstrip()[4:].lstrip()
            output_type.model_validate_json(candidate)
            return result
        except Exception:
            logger.warning("primary completion failed typed validation; using fallback")
            with stage_span(
                "fallback.secondary",
                component="completion_fallback",
                operation=output_type.__name__,
            ):
                return await self._fallback.complete(**arguments)


class PipelineValidationError(ValueError):
    pass


class TransientProviderError(RuntimeError):
    """A provider/network failure that is safe to retry without changing the request."""


class DeterministicProviderError(RuntimeError):
    """A provider rejection that cannot recover by replaying the identical request."""


class BoundedJsonPipeline[OutputT: BaseModel]:
    """One initial completion and at most one schema-repair completion."""

    def __init__(
        self,
        *,
        completion: CompletionPort,
        output_type: type[OutputT],
        static_system: str,
        output_tool: str | None = None,
    ) -> None:
        self._completion = completion
        self._output_type = output_type
        self._static_system = static_system
        self._output_tool = output_tool or self._default_tool_name(output_type)

    @property
    def output_type(self) -> type[OutputT]:
        return self._output_type

    async def run(self, *, task: str, context: AssembledContext) -> OutputT:
        with stage_span(
            "pipeline.total",
            component="bounded_pipeline",
            operation=self._output_type.__name__,
            attributes={"output_type": self._output_type.__name__},
        ):
            return await self._run_bounded(task=task, context=context)

    async def _run_bounded(self, *, task: str, context: AssembledContext) -> OutputT:
        started = time.monotonic()
        system = (
            f"{self._static_system}\n\n"
            "Use only the supplied context. Complete the task by submitting exactly one "
            "final typed result through the output contract supplied by the caller."
        )
        if context.static_rules:
            system = f"{system}\n\n{context.static_rules}"
        with stage_span(
            "pipeline.completion",
            component="bounded_pipeline",
            operation=self._output_type.__name__,
            attributes={"attempt": 1, "kind": "initial"},
        ):
            first = await self._completion.complete(
                system=system,
                context=context.dynamic_context,
                task=task,
                output_type=self._output_type,
                tool_name=self._output_tool,
                max_output_tokens=context.output_token_budget,
            )
        try:
            with stage_span(
                "pipeline.validation",
                component="pydantic",
                operation=self._output_type.__name__,
                attributes={"attempt": 1},
            ):
                parsed = self._parse(first)
            logger.info(
                "pipeline_complete output=%s repaired=false duration_ms=%d",
                self._output_type.__name__,
                int((time.monotonic() - started) * 1000),
            )
            return parsed
        except PipelineValidationError as first_error:
            schema = json.dumps(strict_output_schema(self._output_type), ensure_ascii=False)
            repair = (
                "Repair the prior candidate and submit it again through the required output "
                "contract. Preserve the supplied context and do not add facts. "
                f"Validation error: {first_error}\nSchema: {schema}\n"
                f"Candidate:\n{first.payload}"
            )
            with stage_span(
                "pipeline.repair_completion",
                component="bounded_pipeline",
                operation=self._output_type.__name__,
                attributes={"attempt": 2, "kind": "repair"},
            ):
                second = await self._completion.complete(
                    system=system,
                    context=context.dynamic_context,
                    task=repair,
                    output_type=self._output_type,
                    tool_name=self._output_tool,
                    max_output_tokens=context.output_token_budget,
                )
            try:
                with stage_span(
                    "pipeline.validation",
                    component="pydantic",
                    operation=self._output_type.__name__,
                    attributes={"attempt": 2},
                ):
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

    def _parse(self, result: CompletionResult) -> OutputT:
        if result.protocol_error is not None:
            raise PipelineValidationError(result.protocol_error)
        candidate = result.payload.strip()
        if candidate.startswith("```"):
            lines = candidate.splitlines()
            candidate = "\n".join(lines[1:-1])
            if candidate.lstrip().startswith("json"):
                candidate = candidate.lstrip()[4:].lstrip()
        try:
            return self._output_type.model_validate_json(candidate)
        except (ValidationError, ValueError) as error:
            raise PipelineValidationError(
                _safe_validation_summary(error, self._output_type)
            ) from None

    @staticmethod
    def _default_tool_name(output_type: type[BaseModel]) -> str:
        value = re.sub(r"(?<!^)(?=[A-Z])", "_", output_type.__name__).lower()
        return f"submit_{value}"


def strict_output_schema(output_type: type[BaseModel]) -> dict[str, object]:
    """Return the strict JSON Schema subset expected by structured-output providers."""
    schema = output_type.model_json_schema()

    def normalize(node: object) -> None:
        if isinstance(node, dict):
            node.pop("default", None)
            properties = node.get("properties")
            if node.get("type") == "object" and isinstance(properties, dict):
                node["additionalProperties"] = False
                # OpenAI-compatible strict structured outputs require every declared
                # property to be present. Pydantic already represents semantically optional
                # values as a union with null; defaulted collections therefore become explicit
                # empty arrays rather than omitted keys.
                if properties:
                    node["required"] = list(properties)
            for value in node.values():
                normalize(value)
        elif isinstance(node, list):
            for value in node:
                normalize(value)

    normalize(schema)
    return schema


def _safe_validation_summary(
    error: ValidationError | ValueError,
    output_type: type[BaseModel],
) -> str:
    """Summarize validation without echoing provider output or player-controlled values."""
    if not isinstance(error, ValidationError):
        return "typed output validation failed (root:value_error)"

    schema = output_type.model_json_schema()
    known_fields: set[str] = set()

    def collect_fields(node: object) -> None:
        if isinstance(node, dict):
            properties = node.get("properties")
            if isinstance(properties, dict):
                known_fields.update(str(key) for key in properties)
            for value in node.values():
                collect_fields(value)
        elif isinstance(node, list):
            for value in node:
                collect_fields(value)

    collect_fields(schema)
    summaries: list[str] = []
    for issue in error.errors(
        include_url=False,
        include_context=False,
        include_input=False,
    )[:12]:
        location = issue.get("loc", ())
        safe_location = (
            ".".join(
                str(segment)
                if isinstance(segment, int)
                else segment
                if isinstance(segment, str) and segment in known_fields
                else "<field>"
                for segment in location
            )
            or "root"
        )
        error_type = str(issue.get("type", "validation_error"))
        summaries.append(f"{safe_location}:{error_type}")
    details = ", ".join(summaries) or "root:validation_error"
    return f"typed output validation failed ({details})"
