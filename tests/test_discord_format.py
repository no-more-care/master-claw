from masterclaw.app.response_format import (
    DISCORD_CHUNK_TARGET,
    format_pool_confirmation,
    format_roll_result,
    split_discord_message,
)


def test_short_discord_message_stays_in_one_chunk() -> None:
    assert split_discord_message("hello") == ["hello"]


def test_long_discord_message_is_split_without_data_loss() -> None:
    text = "alpha beta gamma delta"
    chunks = split_discord_message(text, limit=10)
    assert all(len(chunk) <= 10 for chunk in chunks)
    assert " ".join(chunks) == text


def test_default_split_prefers_logical_paragraph_boundaries() -> None:
    first = "A" * 1000
    second = "B" * 1000
    chunks = split_discord_message(first + "\n\n" + second)
    assert chunks == [first, second]
    assert all(len(chunk) <= DISCORD_CHUNK_TARGET for chunk in chunks)


def test_legacy_game_templates_are_rendered_as_discord_markdown() -> None:
    pool = format_pool_confirmation(pool_size=4, difficulty=2, reserve=5)
    result = format_roll_result(
        dice=(6, 4, 1), hits=2, difficulty=2, rights="player_success", reserve=4
    )
    assert "🎲 **Пул: 4 куб.**" in pool
    assert "Сколько кубов" in pool
    assert "`6 4 1`" in result
    assert "Права рассказчика" in result
