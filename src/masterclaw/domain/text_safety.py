from __future__ import annotations

import re
import unicodedata
from collections.abc import Iterator, Mapping, Sequence
from dataclasses import fields, is_dataclass


class SecretLeakError(ValueError):
    """A public value overlaps normalized hidden world material."""


def _normalize(value: str) -> str:
    normalized = unicodedata.normalize("NFKC", value).casefold()
    return " ".join(re.findall(r"[^\W_]+", normalized, flags=re.UNICODE))


def _secret_fragments(secret_plot: str) -> tuple[str, ...]:
    full = _normalize(secret_plot)
    if not full:
        return ()
    fragments = {full}
    for section in re.split(r"(?:[\r\n]+|(?<=[.!?])\s+)", secret_plot):
        normalized = _normalize(section)
        if len(normalized) >= 16 and len(normalized.split()) >= 3:
            fragments.add(normalized)
    tokens = full.split()
    for index in range(max(0, len(tokens) - 3)):
        candidate = " ".join(tokens[index : index + 4])
        if len(candidate) >= 20:
            fragments.add(candidate)
    return tuple(sorted(fragments, key=len, reverse=True))


def _text_values(value: object) -> Iterator[str]:
    if isinstance(value, str):
        yield value
    elif isinstance(value, Mapping):
        for item in value.values():
            yield from _text_values(item)
    elif is_dataclass(value) and not isinstance(value, type):
        for field in fields(value):
            yield from _text_values(getattr(value, field.name))
    elif isinstance(value, Sequence) and not isinstance(value, (bytes, bytearray)):
        for item in value:
            yield from _text_values(item)


def contains_secret_fragment(value: object, secret_plot: str | None) -> bool:
    if not secret_plot:
        return False
    fragments = _secret_fragments(secret_plot)
    if not fragments:
        return False
    return any(
        fragment in normalized
        for text in _text_values(value)
        if (normalized := _normalize(text))
        for fragment in fragments
    )


def ensure_no_secret_fragments(value: object, secret_plot: str | None) -> None:
    if contains_secret_fragment(value, secret_plot):
        raise SecretLeakError("public output overlaps hidden world material")


def redact_secret_leak(text: str, secret_plot: str | None, *, replacement: str) -> str:
    """Replace the whole generated message when safe span-level redaction is ambiguous."""
    return replacement if contains_secret_fragment(text, secret_plot) else text
