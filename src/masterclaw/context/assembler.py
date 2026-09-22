from __future__ import annotations

import json
import logging
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path

from masterclaw.config import ModelRole
from masterclaw.context.manifests import ContextManifest

logger = logging.getLogger(__name__)


class ContextAssemblyError(ValueError):
    pass


@dataclass(frozen=True, slots=True)
class AssembledContext:
    static_rules: str
    dynamic_context: str
    fragments: tuple[str, ...]
    estimated_tokens: int
    degradations: tuple[str, ...] = ()
    output_token_budget: int | None = None


@dataclass(frozen=True, slots=True)
class ContextHistory:
    domain_events: Sequence[Mapping[str, object]] = ()
    chat_messages: Sequence[Mapping[str, object]] = ()


class ContextAssembler:
    def __init__(
        self,
        prompt_root: str | Path,
        *,
        model_ids: Mapping[ModelRole, str] | None = None,
    ) -> None:
        self.root = Path(prompt_root).resolve()
        manifest_path = self.root / "manifest.json"
        self._registry = json.loads(manifest_path.read_text(encoding="utf-8"))["fragments"]
        self._model_ids = dict(model_ids or {})

    def assemble(
        self,
        manifest: ContextManifest,
        projections: Mapping[str, object],
        *,
        history: ContextHistory | None = None,
    ) -> AssembledContext:
        missing = set(manifest.state_projections) - projections.keys()
        if missing:
            raise ContextAssemblyError(f"missing state projections: {sorted(missing)}")

        rule_sections: list[str] = []
        fragments: list[str] = []
        for fragment_id in manifest.rule_fragments:
            try:
                metadata = self._registry[fragment_id]
            except KeyError as error:
                raise ContextAssemblyError(f"unknown prompt fragment: {fragment_id}") from error
            path = (self.root / metadata["path"]).resolve()
            if self.root not in path.parents:
                raise ContextAssemblyError(f"fragment escapes prompt root: {fragment_id}")
            rule_sections.append(
                f"## RULE {fragment_id}\n{path.read_text(encoding='utf-8').strip()}"
            )
            fragments.append(fragment_id)

        history = history or ContextHistory()
        domain_events = (
            list(history.domain_events[-manifest.recent_domain_events :])
            if manifest.recent_domain_events
            else []
        )
        chat_messages = (
            list(history.chat_messages[-manifest.recent_chat_messages :])
            if manifest.recent_chat_messages
            else []
        )
        static_rules = "\n\n".join(rule_sections)
        bounded_projections = dict(projections)
        degradations: list[str] = []

        def build_dynamic() -> str:
            sections: list[str] = []
            for projection_id in manifest.state_projections:
                payload = json.dumps(
                    bounded_projections[projection_id],
                    ensure_ascii=False,
                    sort_keys=True,
                    separators=(",", ":"),
                )
                sections.append(f"## STATE {projection_id}\n{payload}")
            if domain_events:
                payload = json.dumps(
                    domain_events, ensure_ascii=False, sort_keys=True, separators=(",", ":")
                )
                sections.append(f"## HISTORY domain_events\n{payload}")
            if chat_messages:
                payload = json.dumps(
                    chat_messages, ensure_ascii=False, sort_keys=True, separators=(",", ":")
                )
                sections.append(f"## HISTORY chat_messages\n{payload}")
            return "\n\n".join(sections)

        def estimate(dynamic: str) -> int:
            return self._estimate_tokens(
                manifest.model_role,
                static_rules + "\n\n" + dynamic,
            )

        dynamic_context = build_dynamic()
        estimated_tokens = estimate(dynamic_context)
        if estimated_tokens > manifest.input_token_budget and len(chat_messages) > 1:
            chat_messages = chat_messages[-1:]
            degradations.append("chat_messages:1")
            dynamic_context = build_dynamic()
            estimated_tokens = estimate(dynamic_context)
        if estimated_tokens > manifest.input_token_budget and len(domain_events) > 1:
            domain_events = domain_events[-1:]
            degradations.append("domain_events:1")
            dynamic_context = build_dynamic()
            estimated_tokens = estimate(dynamic_context)
        if estimated_tokens > manifest.input_token_budget:
            bounded_projections = {
                key: self._truncate_projection(value, max_string_chars=1000)
                for key, value in bounded_projections.items()
            }
            degradations.append("projection_strings:1000")
            dynamic_context = build_dynamic()
            estimated_tokens = estimate(dynamic_context)
        if estimated_tokens > manifest.input_token_budget:
            raise ContextAssemblyError(
                f"context budget exceeded: {estimated_tokens} > {manifest.input_token_budget}"
            )
        if degradations:
            logger.warning(
                "Context degraded for %s: %s",
                manifest.pipeline.value,
                ", ".join(degradations),
            )
        return AssembledContext(
            static_rules=static_rules,
            dynamic_context=dynamic_context,
            fragments=tuple(fragments),
            estimated_tokens=estimated_tokens,
            degradations=tuple(degradations),
            output_token_budget=manifest.output_token_budget,
        )

    @classmethod
    def _truncate_projection(cls, value: object, *, max_string_chars: int) -> object:
        if isinstance(value, str):
            if len(value) <= max_string_chars:
                return value
            if "Later player revision" not in value:
                return value[: max_string_chars - 1].rstrip() + "…"
            omission = "\n… [middle omitted] …\n"
            if max_string_chars <= len(omission):
                return value[-max_string_chars:]
            available = max_string_chars - len(omission)
            # Canonical briefs use later-wins revisions. Preserve both the original premise and
            # a larger tail so the newest, highest-priority revision survives budget degradation.
            head_length = available // 3
            tail_length = available - head_length
            return value[:head_length].rstrip() + omission + value[-tail_length:].lstrip()
        if isinstance(value, Mapping):
            return {
                str(key): cls._truncate_projection(item, max_string_chars=max_string_chars)
                for key, item in value.items()
            }
        if isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
            return [
                cls._truncate_projection(item, max_string_chars=max_string_chars) for item in value
            ]
        return value

    def _estimate_tokens(self, role: ModelRole, text: str) -> int:
        model = self._model_ids.get(role)
        if model is not None:
            try:
                from litellm import token_counter

                return max(1, int(token_counter(model=model, text=text)))
            except Exception:
                logger.warning(
                    "Tokenizer unavailable for %s; using conservative UTF-8 estimate",
                    model,
                    exc_info=True,
                )
        return max(1, (len(text.encode("utf-8")) + 2) // 3)
