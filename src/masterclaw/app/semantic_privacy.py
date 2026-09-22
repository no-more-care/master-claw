"""Shared structural minimization helpers for application semantic sidecars."""

from __future__ import annotations

import re


def canonical_identifiers(value: object) -> set[str]:
    identifiers = set()
    if isinstance(value, dict):
        for key, item in value.items():
            if (key == "id" or key.endswith("_id")) and isinstance(item, str) and item:
                identifiers.add(item)
            elif key == "participants" and isinstance(item, (list, tuple)):
                identifiers.update(entry for entry in item if isinstance(entry, str) and entry)
            identifiers.update(canonical_identifiers(item))
    elif isinstance(value, (list, tuple)):
        for item in value:
            identifiers.update(canonical_identifiers(item))
    return identifiers


def public_text(value: object, identifiers: set[str], *, limit: int) -> str:
    if not isinstance(value, str):
        return ""
    value = re.sub(r"<[@#](?:[!&])?\d+>", "[participant]", value)
    value = re.sub(r"(?<!\d)\d{17,20}(?!\d)", "[identifier]", value)
    for identifier in sorted(identifiers, key=len, reverse=True):
        value = re.sub(
            rf"(?<!\w){re.escape(identifier)}(?!\w)",
            "[identifier]",
            value,
            flags=re.IGNORECASE,
        )
    return value[:limit]
