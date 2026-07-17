from __future__ import annotations

import re

_CHANNEL_ID = re.compile(r"(?:<#(?P<mention>[0-9]{1,20})>|(?P<bare>[0-9]{1,20}))")


def parse_discord_channel_id(value: str) -> str:
    match = _CHANNEL_ID.fullmatch(value.strip())
    if match is None:
        raise ValueError("Discord channel id must be 1 to 20 decimal digits")
    return match.group("mention") or match.group("bare")
