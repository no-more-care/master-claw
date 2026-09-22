"""Detached public-only world observation, never the local legacy guard snapshot."""

from __future__ import annotations

import json
import re
from collections.abc import Mapping
from dataclasses import dataclass
from enum import StrEnum
from typing import Protocol

from masterclaw.app.semantic_privacy import canonical_identifiers, public_text


class WorldProjectionSkip(StrEnum):
    UNAVAILABLE = "projection_unavailable"
    TRUNCATED = "projection_truncated"


@dataclass(frozen=True, slots=True)
class PublicWorldSemanticSnapshot:
    public_json: str | None = None
    skipped: WorldProjectionSkip | None = None

    @property
    def public_state(self) -> dict:
        return json.loads(self.public_json) if self.public_json is not None else {}


class WorldSemanticObserver(Protocol):
    async def observe(self, snapshot: PublicWorldSemanticSnapshot) -> object: ...


class _UnsafeProjection(ValueError):
    def __init__(self, reason: WorldProjectionSkip):
        self.reason = reason
        super().__init__(reason.value)


def capture_public_world_semantics(
    *, settings: Mapping[str, object], content: Mapping[str, object]
) -> PublicWorldSemanticSnapshot:
    """Minimize locally; forbidden values are used only to prevent echoed private text."""
    unavailable, truncated = WorldProjectionSkip.UNAVAILABLE, WorldProjectionSkip.TRUNCATED

    def strings(value: object) -> set[str]:
        if isinstance(value, str):
            return {value} if value.strip() else set()
        if isinstance(value, dict):
            return set().union(*(strings(item) for item in value.values()))
        if isinstance(value, (list, tuple)):
            return set().union(*(strings(item) for item in value))
        return set()

    def private_strings(value: object) -> set[str]:
        if isinstance(value, dict):
            return set().union(
                *(
                    strings(item)
                    if key in {"secret_plot", "content_constraints", "hidden", "private"}
                    or key.startswith(("secret_", "private_", "hidden_", "gm_"))
                    else private_strings(item)
                    for key, item in value.items()
                )
            )
        if isinstance(value, (list, tuple)):
            return set().union(*(private_strings(item) for item in value))
        return set()

    try:
        identifiers = canonical_identifiers([dict(settings), dict(content)])
        forbidden = private_strings([dict(settings), dict(content)])

        def text(value: object, limit: int) -> str:
            if not isinstance(value, str) or not value.strip():
                raise _UnsafeProjection(unavailable)
            if len(value) > limit:
                raise _UnsafeProjection(truncated)
            if any(re.search(re.escape(secret), value, re.IGNORECASE) for secret in forbidden):
                raise _UnsafeProjection(unavailable)
            result = public_text(value, identifiers, limit=max(limit, len(value) * 20))
            if len(result) > limit:
                raise _UnsafeProjection(truncated)
            return result

        def items(value: object, maximum: int) -> list | tuple:
            if not isinstance(value, (list, tuple)):
                raise _UnsafeProjection(unavailable)
            if len(value) > maximum:
                raise _UnsafeProjection(truncated)
            return value

        def record(value: object, fields: dict[str, int]) -> dict:
            if not isinstance(value, dict):
                raise _UnsafeProjection(unavailable)
            if any(value.get(key) is True for key in ("hidden", "private", "is_hidden")) or (
                value.get("visibility", "public") != "public"
            ):
                raise _UnsafeProjection(unavailable)
            return {key: text(value[key], limit) for key, limit in fields.items() if key in value}

        record(dict(content), {})
        if content.get("degradations"):
            raise _UnsafeProjection(truncated)
        public_settings = {
            key: text(settings[key], 1000)
            for key in ("genre", "tone", "scale", "player_role")
            if key in settings
        }
        concepts = [
            text(item, 1000) for item in items(settings.get("pregenerated_character_briefs", []), 6)
        ]
        if not public_settings and not concepts:
            raise _UnsafeProjection(unavailable)
        state = {
            "confirmed_public_settings": public_settings,
            "requested_public_concepts": concepts,
            "world": {
                "premise": text(content.get("premise"), 4000),
                "themes": [text(item, 300) for item in items(content.get("themes", []), 8)],
                "locations": [
                    record(item, {"name": 200, "description": 2000})
                    for item in items(content.get("locations", []), 12)
                ],
                "factions": [text(item, 300) for item in items(content.get("factions", []), 12)],
                "tensions": [text(item, 1000) for item in items(content.get("tensions", []), 12)],
                "pregens": [
                    record(
                        item,
                        {
                            "name": 200,
                            "role": 300,
                            "concept": 1500,
                            "hook": 1500,
                            "biography": 3000,
                        },
                    )
                    for item in items(content.get("character_templates", []), 6)
                ],
            },
        }
        serialized = json.dumps(state, ensure_ascii=False, allow_nan=False)
        if len(serialized.encode("utf-8")) > 80_000:
            raise _UnsafeProjection(truncated)
        return PublicWorldSemanticSnapshot(public_json=serialized)
    except _UnsafeProjection as error:
        return PublicWorldSemanticSnapshot(skipped=error.reason)
    except (TypeError, ValueError, RecursionError):
        return PublicWorldSemanticSnapshot(skipped=unavailable)
