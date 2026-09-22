from __future__ import annotations

import re
import unicodedata
from collections.abc import Mapping, Sequence
from types import SimpleNamespace

from masterclaw.app.world_semantic_guard import WorldSemanticGuardError, WorldSemanticSnapshot

_NEGATIVE_BOUNDARY_PREFIXES = (
    "must not include",
    "do not include",
    "не должно быть",
    "не включать",
    "without",
    "exclude",
    "avoid",
    "никаких",
    "никакого",
    "никакой",
    "исключить",
    "избегать",
    "без",
    "no",
)
_CONCEPT_STOPWORDS = frozenset(
    {
        "a",
        "an",
        "and",
        "character",
        "concept",
        "for",
        "person",
        "people",
        "hero",
        "heroes",
        "of",
        "the",
        "to",
        "with",
        "from",
        "that",
        "this",
        "а",
        "в",
        "и",
        "из",
        "на",
        "персонаж",
        "концепт",
        "герой",
        "герои",
        "героиня",
        "который",
        "которая",
        "этот",
        "эта",
        "для",
        "с",
    }
)
_RUSSIAN_SUFFIXES = (
    "иями",
    "остью",
    "ывать",
    "ивать",
    "енный",
    "анная",
    "енные",
    "овать",
    "евать",
    "ями",
    "ами",
    "его",
    "ого",
    "ему",
    "ому",
    "ыми",
    "ими",
    "иях",
    "ение",
    "ания",
    "ая",
    "яя",
    "ое",
    "ее",
    "ые",
    "ие",
    "ый",
    "ий",
    "ой",
    "ую",
    "юю",
    "ых",
    "их",
    "ам",
    "ям",
    "ах",
    "ях",
    "ом",
    "ем",
    "ов",
    "ев",
    "ия",
    "ья",
    "ию",
    "ью",
    "ить",
    "ыть",
    "ать",
    "ять",
    "ено",
    "ана",
    "ены",
    "аны",
    "ло",
    "ла",
    "ли",
    "ть",
    "ся",
    "сь",
    "а",
    "я",
    "ы",
    "и",
    "е",
    "о",
    "у",
    "ю",
    "ь",
)
_BOUNDARY_LEAD_NEGATORS = frozenset(
    {
        "no",
        "without",
        "none",
        "без",
        "нет",
        "никаких",
        "никакого",
        "никакой",
    }
)
_BOUNDARY_INHERENT_NEGATING_ACTIONS = frozenset(
    {
        "avoid",
        "ban",
        "exclude",
        "forbid",
        "prohibit",
        "protect",
        "safeguard",
        "shield",
        "избег",
        "исключ",
        "запрещ",
        "защищ",
    }
)
_BOUNDARY_NEGATABLE_ACTIONS = frozenset(
    {
        "allow",
        "contain",
        "depict",
        "describe",
        "feature",
        "glorify",
        "include",
        "involve",
        "mention",
        "permit",
        "show",
        "допуск",
        "описыва",
        "показыва",
        "разреш",
        "упомина",
    }
)
_BOUNDARY_ABSENCE_STEMS = frozenset(
    {
        "absent",
        "ban",
        "exclude",
        "forbid",
        "illegal",
        "prohibit",
        "unmention",
        "запрещ",
        "исключ",
        "недопуст",
        "отсутств",
    }
)
_BOUNDARY_EXISTENCE_STEMS = frozenset(
    {
        "appear",
        "depict",
        "exist",
        "occur",
        "show",
        "встреча",
        "показыва",
        "присутств",
        "существ",
    }
)
_ADHERENCE_SETTINGS = ("locale", "genre", "tone", "scale", "player_role")


def _normalize_text(value: str) -> str:
    normalized = unicodedata.normalize("NFKC", value).casefold().replace("ё", "е")
    return " ".join(re.findall(r"[^\W_]+", normalized, flags=re.UNICODE))


def _is_cyrillic_token(token: str) -> bool:
    return any("CYRILLIC" in unicodedata.name(character, "") for character in token)


def _english_stem(token: str) -> str:
    stem = token
    if len(stem) > 5 and stem.endswith("ies"):
        stem = stem[:-3] + "y"
    elif len(stem) > 5 and stem.endswith("ing"):
        stem = stem[:-3]
    elif len(stem) > 4 and stem.endswith("ed"):
        stem = stem[:-2]
    elif len(stem) > 4 and stem.endswith("es"):
        stem = stem[:-2]
    elif len(stem) > 4 and stem.endswith("s"):
        stem = stem[:-1]
    if len(stem) > 3 and stem[-1:] == stem[-2:-1]:
        stem = stem[:-1]
    if len(stem) > 4 and stem.endswith("e"):
        stem = stem[:-1]
    return stem


def _russian_stem(token: str) -> str:
    for suffix in _RUSSIAN_SUFFIXES:
        if len(token) - len(suffix) >= 3 and token.endswith(suffix):
            return token[: -len(suffix)]
    return token


def _stem_token(token: str) -> str:
    return _russian_stem(token) if _is_cyrillic_token(token) else _english_stem(token)


def _russian_consonants(value: str) -> str:
    return "".join(
        character for character in value if character not in "аеёиоуыэюяьъ" and character.isalpha()
    )


def _stems_match(left: str, right: str) -> bool:
    if left == right:
        return True
    if _is_cyrillic_token(left) and _is_cyrillic_token(right):
        left_consonants = _russian_consonants(left)
        right_consonants = _russian_consonants(right)
        if len(left_consonants) >= 3 and left_consonants == right_consonants:
            return True
        minimum = min(len(left), len(right))
        return minimum >= 4 and left[:4] == right[:4] and abs(len(left) - len(right)) <= 2
    minimum = min(len(left), len(right))
    return minimum >= 5 and left[:5] == right[:5] and abs(len(left) - len(right)) <= 3


def _prohibited_boundary_targets(constraint: str) -> tuple[str, ...]:
    with_delimiters = re.sub(
        r"[,;/]+",
        " or ",
        unicodedata.normalize("NFKC", constraint).casefold().replace("ё", "е"),
    )
    normalized = _normalize_text(with_delimiters)
    if not normalized:
        return ()
    tokens = set(normalized.split())
    if (
        {"additional", "constraints"} <= tokens
        or {"additional", "boundaries"} <= tokens
        or any(token.startswith("дополнительн") for token in tokens)
        and any(token.startswith("ограничен") for token in tokens)
    ):
        return ()
    # Intensity/veil boundaries are not blanket bans, but generated prose must not depict their
    # subject directly. Treat the subject as prohibited unless the occurrence itself is clearly
    # framed as off-screen/fade-to-black by `_boundary_mention_is_negated`.
    veiled_patterns = (
        r"^(?:keep |show |depict |describe )?(?P<target>.+?) "
        r"(?:only )?(?:off screen|fade to black)$",
        r"^(?:off screen|fade to black) (?:for|on) (?P<target>.+)$",
        r"^(?:показывать |описывать )?(?P<target>.+?) "
        r"(?:только )?(?:за кадром|через затемнение)$",
        r"^(?:за кадром|затемнение) (?:для|при) (?P<target>.+)$",
    )
    for pattern in veiled_patterns:
        if match := re.match(pattern, normalized):
            target = match.group("target").strip()
            return (target,) if target else ()
    for prefix in _NEGATIVE_BOUNDARY_PREFIXES:
        if normalized.startswith(prefix + " "):
            target = normalized[len(prefix) :].strip()
            if not target:
                return ()
            clauses = tuple(
                item.strip() for item in re.split(r"\s+(?:and|or|и|или)\s+", target) if item.strip()
            )
            return clauses or (target,)
    return ()


def _concept_terms(value: str) -> set[str]:
    stopword_terms = {_stem_token(token) for token in _CONCEPT_STOPWORDS}
    terms = {_stem_token(token) for token in _normalize_text(value).split() if len(token) >= 3}
    return {
        term
        for term in terms
        if not any(_stems_match(term, stopword) for stopword in stopword_terms)
    }


def _text_values(value: object) -> list[str]:
    if isinstance(value, str):
        return [value]
    if isinstance(value, Mapping):
        return [text for item in value.values() for text in _text_values(item)]
    if isinstance(value, Sequence) and not isinstance(value, (bytes, bytearray)):
        return [text for item in value for text in _text_values(item)]
    return []


def _matching_term_position_sets(
    requested_terms: Sequence[str], candidate_tokens: Sequence[str]
) -> list[list[int]]:
    candidate_stems = [_stem_token(token) for token in candidate_tokens]
    matches: list[list[int]] = []
    for anchor, candidate in enumerate(candidate_stems):
        if not _stems_match(requested_terms[0], candidate):
            continue
        positions = [anchor]
        search_from = max(0, anchor - len(requested_terms) - 5)
        search_until = min(len(candidate_stems), anchor + len(requested_terms) + 6)
        for requested in requested_terms[1:]:
            position = next(
                (
                    index
                    for index in range(search_from, search_until)
                    if index not in positions and _stems_match(requested, candidate_stems[index])
                ),
                None,
            )
            if position is None:
                break
            positions.append(position)
        if len(positions) == len(requested_terms):
            matches.append(positions)
    return matches


def _concept_matches_character(brief: str, character: object) -> bool:
    candidate = _normalize_text(" ".join(_text_values(character)))
    requested = _normalize_text(brief)
    if requested and f" {requested} " in f" {candidate} ":
        return True
    requested_terms = _concept_terms(brief)
    if not requested_terms:
        return False
    candidate_terms = {_stem_token(token) for token in candidate.split()}
    matched = sum(
        any(_stems_match(requested_term, candidate) for candidate in candidate_terms)
        for requested_term in requested_terms
    )
    required = 1 if len(requested_terms) == 1 else max(2, (len(requested_terms) * 3 + 4) // 5)
    return matched >= required


def _stem_matches_any(stem: str, candidates: frozenset[str]) -> bool:
    return any(_stems_match(stem, candidate) for candidate in candidates)


def _boundary_mention_is_negated(tokens: list[str], positions: list[int]) -> bool:
    start = min(positions)
    end = max(positions)
    stems = [_stem_token(token) for token in tokens]
    before_start = max(0, start - 4)
    before_tokens = tokens[before_start:start]
    before_stems = stems[before_start:start]
    # Veil wording often follows the subject as a short predicate ("torture fades to
    # black and remains off-screen"), so keep enough local context to include the
    # complete marker without treating a remote sentence-level disclaimer as proof.
    after_end = min(len(tokens), end + 8)
    after_tokens = tokens[end + 1 : after_end]
    after_stems = stems[end + 1 : after_end]
    between_stems = stems[start : end + 1]
    local_tokens = before_tokens + tokens[start : end + 1] + after_tokens
    local_phrase = " ".join(local_tokens)
    direct_depiction_markers = (
        "on screen",
        "onscreen",
        "на экране",
        "в кадре",
    )
    veiled_markers = (
        "off screen",
        "offscreen",
        "fade to black",
        "за кадром",
        "через затемнение",
    )
    has_veil_marker = any(marker in local_phrase for marker in veiled_markers) or bool(
        re.search(r"\bfad(?:e|es|ed|ing) to black\b", local_phrase)
    )
    if not any(marker in local_phrase for marker in direct_depiction_markers) and has_veil_marker:
        return True

    conjunctions = {"and", "or", "и", "или"}
    list_window = tokens[max(0, start - 8) : start]
    list_conjunction = next(
        (
            index
            for index in range(len(list_window) - 1, -1, -1)
            if list_window[index] in conjunctions
        ),
        None,
    )
    if list_conjunction is not None:
        list_has_negator = any(
            token in _BOUNDARY_LEAD_NEGATORS for token in list_window[:list_conjunction]
        )
        if list_has_negator and len(list_window[list_conjunction + 1 :]) <= 1:
            return True

    prior_conjunction = next(
        (
            index
            for index in range(len(before_tokens) - 1, -1, -1)
            if before_tokens[index] in conjunctions
        ),
        None,
    )
    if prior_conjunction is not None:
        list_has_negator = any(
            token in _BOUNDARY_LEAD_NEGATORS for token in before_tokens[:prior_conjunction]
        )
        if list_has_negator and len(before_tokens[prior_conjunction + 1 :]) <= 1:
            return True
        before_tokens = before_tokens[prior_conjunction + 1 :]
        before_stems = before_stems[prior_conjunction + 1 :]

    next_conjunction = next(
        (index for index, token in enumerate(after_tokens) if token in conjunctions),
        None,
    )
    if next_conjunction is not None:
        after_tokens = after_tokens[:next_conjunction]
        after_stems = after_stems[:next_conjunction]

    if any(token in _BOUNDARY_LEAD_NEGATORS for token in before_tokens[-3:]):
        return True
    if any(_stem_matches_any(stem, _BOUNDARY_INHERENT_NEGATING_ACTIONS) for stem in between_stems):
        return True
    if any(_stem_matches_any(stem, _BOUNDARY_INHERENT_NEGATING_ACTIONS) for stem in before_stems):
        return True
    if len(before_tokens) >= 2 and before_tokens[-2:] in (["free", "of"], ["free", "from"]):
        return True
    if any(token in {"not", "не"} for token in before_tokens):
        if start > 0 and tokens[start - 1] in {"not", "не"}:
            return True
        if any(_stem_matches_any(stem, _BOUNDARY_NEGATABLE_ACTIONS) for stem in before_stems):
            return True

    for offset, stem in enumerate(after_stems):
        if not _stem_matches_any(stem, _BOUNDARY_ABSENCE_STEMS):
            continue
        absolute = end + 1 + offset
        # "not absent" / "не запрещены" asserts the opposite and must still be rejected.
        if absolute > 0 and tokens[absolute - 1] in {"not", "не"}:
            return False
        return True
    if any(token in {"not", "не"} for token in after_tokens):
        return any(
            _stem_matches_any(stem, _BOUNDARY_EXISTENCE_STEMS)
            or _stem_matches_any(stem, _BOUNDARY_NEGATABLE_ACTIONS)
            for stem in after_stems
        )
    return False


def _text_violates_boundary(text: str, target: str) -> bool:
    relation_words = {
        "against",
        "at",
        "of",
        "to",
        "toward",
        "towards",
        "для",
        "к",
        "над",
        "по",
        "против",
    }
    target_terms = tuple(
        _stem_token(token)
        for token in _normalize_text(target).split()
        if token not in relation_words
    )
    if not target_terms:
        return False
    segments = re.split(
        r"[,.!?;:\r\n]+|\s+(?:but|however|though|yet|nevertheless|но|однако|зато|хотя)\s+",
        unicodedata.normalize("NFKC", text).casefold().replace("ё", "е"),
    )
    for segment in segments:
        tokens = _normalize_text(segment).split()
        for positions in _matching_term_position_sets(target_terms, tokens):
            if not _boundary_mention_is_negated(tokens, positions):
                return True
    return False


def _validate_setting_adherence(
    snapshot: WorldSemanticSnapshot,
    settings: Mapping[str, object],
) -> None:
    public_texts = _text_values(
        {key: value for key, value in snapshot.draft.items() if key != "secret_plot"}
    )
    normalized_public_texts = [_normalize_text(text) for text in public_texts]
    for setting_name in _ADHERENCE_SETTINGS:
        if setting_name not in settings:
            continue
        confirmed = settings[setting_name]
        claim_data = snapshot.adherence[setting_name]
        claim = None if claim_data is None else SimpleNamespace(**claim_data)
        if claim is None:
            raise WorldSemanticGuardError(
                f"structured world lacks {setting_name} adherence evidence"
            )
        if _normalize_text(claim.confirmed_value) != _normalize_text(str(confirmed)):
            raise WorldSemanticGuardError(f"structured world changed the confirmed {setting_name}")
        normalized_evidence = _normalize_text(claim.evidence)
        if not any(
            normalized_evidence and normalized_evidence in public_text
            for public_text in normalized_public_texts
        ):
            raise WorldSemanticGuardError(
                f"structured world {setting_name} evidence is not public draft text"
            )
        if any(_normalize_text(term) not in normalized_evidence for term in claim.applied_terms):
            raise WorldSemanticGuardError(
                f"structured world {setting_name} evidence omits its applied terms"
            )


def _requested_concepts_have_distinct_templates(
    concepts: list[str], characters: list[object]
) -> bool:
    candidates = [
        [
            index
            for index, character in enumerate(characters)
            if _concept_matches_character(brief, character)
        ]
        for brief in concepts
    ]
    if any(not indexes for indexes in candidates):
        return False

    def assign(concept_index: int, used: frozenset[int]) -> bool:
        if concept_index == len(candidates):
            return True
        return any(
            index not in used and assign(concept_index + 1, used | {index})
            for index in candidates[concept_index]
        )

    return assign(0, frozenset())


class LegacyWorldSemanticGuard:
    """The historical deterministic semantic rules, not a provider or an external projection."""

    def validate(self, snapshot: WorldSemanticSnapshot) -> None:
        draft = snapshot.draft
        settings = snapshot.settings
        requested_concepts = settings.get("pregenerated_character_briefs")
        if isinstance(requested_concepts, (list, tuple)) and requested_concepts:
            serialized_characters = draft["character_templates"]
            if not _requested_concepts_have_distinct_templates(
                [str(item) for item in requested_concepts], serialized_characters
            ):
                raise WorldSemanticGuardError(
                    "structured world is missing a requested pregen concept"
                )

        generated_texts = _text_values(draft)
        for constraint in settings.get("content_constraints", ()):
            targets = _prohibited_boundary_targets(str(constraint))
            if any(
                _text_violates_boundary(text, target)
                for target in targets
                for text in generated_texts
            ):
                raise WorldSemanticGuardError("structured world violates a content boundary")

        _validate_setting_adherence(snapshot, settings)
