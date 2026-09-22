import pytest

from masterclaw.domain.outcomes import CanonicalOutcomePatch, SceneNpcState
from masterclaw.domain.text_safety import (
    SecretLeakError,
    contains_secret_fragment,
    ensure_no_secret_fragments,
    hidden_secret_plot,
    redact_secret_leak,
    secret_fact_catalog,
)

SECRET = "The bell keeper is the storm's forgotten name. The bronze key wakes it at midnight."


def test_secret_boundary_rejects_exact_and_normalized_fragments() -> None:
    assert contains_secret_fragment(f"Hidden: {SECRET}", SECRET)
    assert contains_secret_fragment(
        "THE BELL-KEEPER... is the storm’s forgotten NAME!",
        SECRET,
    )
    assert (
        redact_secret_leak(
            "The bronze-key wakes it at MIDNIGHT.",
            SECRET,
            replacement="safe fallback",
        )
        == "safe fallback"
    )
    assert contains_secret_fragment(
        "The storm forgot the bell keeper's true name.",
        SECRET,
    )


def test_secret_boundary_recursively_validates_public_patch() -> None:
    patch = CanonicalOutcomePatch(
        summary="A harmless summary.",
        upsert_scene_npcs=(
            SceneNpcState(
                "keeper",
                "Keeper",
                "The BELL keeper is the storm's forgotten name",
            ),
        ),
    )
    with pytest.raises(SecretLeakError, match="hidden world material"):
        ensure_no_secret_fragments(patch, SECRET)


def test_secret_boundary_allows_unrelated_public_text() -> None:
    ensure_no_secret_fragments("The cracked bell sways in the rain.", SECRET)


def test_secret_boundary_allows_public_setting_vocabulary_without_the_reveal() -> None:
    secret = "Ancient storm anchors imprison a sleeping god beneath Sky City."
    public = "Sky City survives on ancient storm anchors."

    ensure_no_secret_fragments(public, secret)


def test_secret_boundary_detects_identity_reveal_with_common_synonyms() -> None:
    assert contains_secret_fragment(
        "The guardian is the tempest itself.",
        "The bell keeper is the storm's forgotten name.",
    )


def test_secret_catalog_supports_stable_selective_disclosure() -> None:
    catalog = secret_fact_catalog(SECRET)

    assert len(catalog) == 2
    assert catalog == secret_fact_catalog(SECRET)
    hidden = hidden_secret_plot(SECRET, {catalog[0].secret_id})
    assert hidden == catalog[1].text
    ensure_no_secret_fragments(catalog[0].text, hidden)
