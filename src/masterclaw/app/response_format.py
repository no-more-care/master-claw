from __future__ import annotations

from masterclaw.app.i18n import tr

DISCORD_MESSAGE_LIMIT = 2000
DISCORD_CHUNK_TARGET = 1800


def split_discord_message(text: str, *, limit: int = DISCORD_CHUNK_TARGET) -> list[str]:
    """Split a logical response without dropping content or breaking the API limit."""
    if limit < 1:
        raise ValueError("limit must be positive")
    remaining = text.strip()
    chunks: list[str] = []
    while remaining:
        if len(remaining) <= limit:
            chunks.append(remaining)
            break
        cut = remaining.rfind("\n\n", 0, limit + 1)
        if cut <= 0:
            cut = remaining.rfind("\n", 0, limit + 1)
        if cut <= 0:
            cut = remaining.rfind(" ", 0, limit + 1)
        if cut <= 0:
            cut = limit
        chunks.append(remaining[:cut].rstrip())
        remaining = remaining[cut:].lstrip()
    return chunks


def format_pool_confirmation(
    *,
    pool_size: int,
    difficulty: int,
    reserve: int,
    locale: str,
    sources: tuple[str, ...] = (),
) -> str:
    """Discord-safe adaptation of the legacy game_response/dice_pool templates."""
    breakdown = ""
    if sources:
        breakdown = " " + ", ".join(f"{source} +1" for source in sources) + "."
    return tr(
        locale,
        "pool_confirmation",
        pool_size=pool_size,
        breakdown=breakdown,
        difficulty=difficulty,
        reserve=reserve,
    )


def format_roll_result(
    *, dice: tuple[int, ...], hits: int, difficulty: int, rights: str, reserve: int, locale: str
) -> str:
    """Compact mechanics block; long prose belongs in the narrative channel."""
    values = " ".join(str(value) for value in dice)
    return tr(
        locale,
        "roll_result",
        values=values,
        hits=hits,
        difficulty=difficulty,
        rights=rights,
        reserve=reserve,
    )
