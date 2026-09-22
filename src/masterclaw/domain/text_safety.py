from __future__ import annotations

import hashlib
import re
import unicodedata
from collections.abc import Iterator, Mapping, Sequence
from dataclasses import dataclass, fields, is_dataclass


class SecretLeakError(ValueError):
    """A public value overlaps normalized hidden world material."""


@dataclass(frozen=True, slots=True)
class SecretFact:
    secret_id: str
    text: str


_SECRET_STOPWORDS = {
    "the",
    "a",
    "an",
    "and",
    "or",
    "of",
    "to",
    "in",
    "at",
    "is",
    "it",
    "its",
    "this",
    "that",
    "with",
    "for",
    "on",
    "и",
    "или",
    "но",
    "что",
    "это",
    "как",
    "для",
    "при",
    "его",
    "ее",
    "её",
    "он",
    "она",
    "они",
}

_SECRET_TOKEN_ALIASES = {
    "guardian": "keeper",
    "warden": "keeper",
    "tempest": "storm",
    "itself": "identity",
    "name": "identity",
    "identity": "identity",
    "страж": "хранитель",
    "стража": "хранитель",
    "буря": "шторм",
    "бури": "шторм",
    "имя": "личность",
    "личность": "личность",
}


def _normalize(value: str) -> str:
    normalized = unicodedata.normalize("NFKC", value).casefold()
    return " ".join(re.findall(r"[^\W_]+", normalized, flags=re.UNICODE))


def secret_fact_catalog(secret_plot: str | None) -> tuple[SecretFact, ...]:
    """Split GM material into stable, addressable facts for explicit disclosure."""
    if not secret_plot or not secret_plot.strip():
        return ()
    candidates = re.split(r"(?:[\r\n]+|(?<=[.!?])\s+)", secret_plot)
    facts: list[SecretFact] = []
    seen: set[str] = set()
    for candidate in candidates:
        text = candidate.strip().lstrip("-•* ").strip()
        normalized = _normalize(text)
        if not normalized or normalized in seen:
            continue
        seen.add(normalized)
        digest = hashlib.sha256(normalized.encode("utf-8")).hexdigest()[:16]
        facts.append(SecretFact(f"secret_{digest}", text))
    return tuple(facts)


def hidden_secret_plot(secret_plot: str | None, revealed_ids: set[str]) -> str | None:
    hidden = [
        fact.text for fact in secret_fact_catalog(secret_plot) if fact.secret_id not in revealed_ids
    ]
    return "\n".join(hidden) or None


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


def _meaningful_tokens(value: str) -> set[str]:
    return {
        _SECRET_TOKEN_ALIASES.get(token, token)
        for token in _normalize(value).split()
        if len(token) >= 4 and token not in _SECRET_STOPWORDS
    }


def _secret_token_sets(secret_plot: str) -> tuple[frozenset[str], ...]:
    candidates = [secret_plot, *re.split(r"(?:[\r\n]+|(?<=[.!?])\s+)", secret_plot)]
    token_sets = {
        frozenset(tokens)
        for candidate in candidates
        if len(tokens := _meaningful_tokens(candidate)) >= 5
    }
    return tuple(token_sets)


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
    token_sets = _secret_token_sets(secret_plot)
    for text in _text_values(value):
        normalized = _normalize(text)
        if not normalized:
            continue
        if any(fragment in normalized for fragment in fragments):
            return True
        public_tokens = _meaningful_tokens(text)
        for secret_tokens in token_sets:
            shared_tokens = public_tokens & secret_tokens
            overlap = len(shared_tokens)
            # World names and recurring setting nouns legitimately occur on both sides of the
            # public/GM boundary. Require most of one secret statement to survive a paraphrase;
            # four shared setting nouns alone are not evidence of a disclosure.
            if overlap >= 4 and overlap / len(secret_tokens) >= 0.75:
                return True
            # Identity revelations are commonly paraphrased with role and weather synonyms.
            # Three aligned predicate-bearing terms are stronger evidence here than shared place
            # names, while still avoiding the generic setting-vocabulary case above.
            if (
                {"identity", "личность"} & shared_tokens
                and overlap >= 3
                and overlap / len(secret_tokens) >= 0.6
            ):
                return True
    return False


def ensure_no_secret_fragments(value: object, secret_plot: str | None) -> None:
    if contains_secret_fragment(value, secret_plot):
        raise SecretLeakError("public output overlaps hidden world material")


def redact_secret_leak(text: str, secret_plot: str | None, *, replacement: str) -> str:
    """Replace the whole generated message when safe span-level redaction is ambiguous."""
    return replacement if contains_secret_fragment(text, secret_plot) else text
