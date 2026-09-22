"""Shared legacy draft-conflict heuristic; intentionally not semantic authority."""

import re

from masterclaw.app.scenarios import normalize_phrase

_NEW_WORLD_REQUEST_PATTERNS = (
    re.compile(
        r"\b(?:создай|создать|создадим|создам)\s+(?:мне\s+)?"
        r"(?:(?:новый|другой)\s+|еще\s+один\s+)?(?:мир|сеттинг)\b"
    ),
    re.compile(
        r"\b(?:сделай|начни)\s+(?:мне\s+)?"
        r"(?:(?:новый|другой)\s+|еще\s+один\s+)(?:мир|сеттинг)\b"
    ),
    re.compile(
        r"\b(?:create|design)\s+(?:(?:a|the)\s+)?"
        r"(?:(?:new|another)\s+)?(?:world|setting)\b"
    ),
    re.compile(r"\bmake\s+(?:(?:a|the)\s+)?(?:new|another)\s+(?:world|setting)\b"),
    re.compile(r"\bstart\s+(?:(?:a|the)\s+)?(?:new|another)\s+(?:world|setting)\b"),
)


def _is_detailed_new_world_request(content: str) -> bool:
    """Recognize only anchored creation requests, not ordinary edits of the current world."""

    normalized = normalize_phrase(content)
    return any(pattern.search(normalized) is not None for pattern in _NEW_WORLD_REQUEST_PATTERNS)
