import pytest

from masterclaw.domain.outcomes import CanonicalOutcomePatch, SceneNpcState
from masterclaw.domain.text_safety import (
    SecretLeakError,
    contains_secret_fragment,
    ensure_no_secret_fragments,
    redact_secret_leak,
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
