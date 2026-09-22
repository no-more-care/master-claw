"""Detached, local-only post-structuring semantic validation contracts."""

from __future__ import annotations

import json
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Protocol


class WorldSemanticGuardError(ValueError):
    """Translated to the existing WorldGenerationError at the application service boundary."""


@dataclass(frozen=True, slots=True)
class WorldSemanticSnapshot:
    settings_json: str
    draft_json: str
    adherence_json: str

    @classmethod
    def capture(
        cls,
        *,
        settings: Mapping[str, object],
        draft: Mapping[str, object],
        adherence: Mapping[str, object],
    ) -> WorldSemanticSnapshot:
        return cls(
            *(json.dumps(dict(value), ensure_ascii=False) for value in (settings, draft, adherence))
        )

    @property
    def settings(self) -> dict[str, object]:
        return json.loads(self.settings_json)

    @property
    def draft(self) -> dict[str, object]:
        return json.loads(self.draft_json)

    @property
    def adherence(self) -> dict[str, object]:
        return json.loads(self.adherence_json)


class WorldSemanticGuard(Protocol):
    def validate(self, snapshot: WorldSemanticSnapshot) -> None: ...
