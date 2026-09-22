"""Detached context inputs captured after shared enrichment and bounded history reads."""

from __future__ import annotations

import json
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Protocol

from masterclaw.context.assembler import ContextHistory
from masterclaw.context.manifests import ContextManifest


@dataclass(frozen=True, slots=True)
class ContextInputSnapshot:
    projections_json: str
    domain_events_json: str
    chat_messages_json: str

    @classmethod
    def capture(
        cls,
        projections: Mapping[str, object],
        domain_events: Sequence[Mapping[str, object]],
        chat_messages: Sequence[Mapping[str, object]],
    ) -> ContextInputSnapshot:
        return cls(
            *(
                json.dumps(value, ensure_ascii=False)
                for value in (projections, domain_events, chat_messages)
            )
        )

    @property
    def projections(self) -> dict[str, object]:
        return json.loads(self.projections_json)

    @property
    def history(self) -> ContextHistory:
        return ContextHistory(
            json.loads(self.domain_events_json), json.loads(self.chat_messages_json)
        )


class ContextInputCapture(Protocol):
    def __call__(
        self,
        manifest: ContextManifest,
        projections: dict[str, object],
        *,
        game_id: str,
        channel_id: str | None = None,
        player_id: str | None = None,
    ) -> ContextInputSnapshot: ...
