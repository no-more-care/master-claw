from __future__ import annotations

DISCORD_MESSAGE_LIMIT = 2000


def split_discord_message(text: str, *, limit: int = DISCORD_MESSAGE_LIMIT) -> list[str]:
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
