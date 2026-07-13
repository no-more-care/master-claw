from __future__ import annotations

from contextvars import ContextVar, Token

_game_id: ContextVar[str | None] = ContextVar("masterclaw_game_id", default=None)


def current_game_id() -> str | None:
    return _game_id.get()


def bind_game(game_id: str | None) -> Token[str | None]:
    return _game_id.set(game_id)


def reset_game(token: Token[str | None]) -> None:
    _game_id.reset(token)
