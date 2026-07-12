from masterclaw.app.response_format import split_discord_message


def test_short_discord_message_stays_in_one_chunk() -> None:
    assert split_discord_message("hello") == ["hello"]


def test_long_discord_message_is_split_without_data_loss() -> None:
    text = "alpha beta gamma delta"
    chunks = split_discord_message(text, limit=10)
    assert all(len(chunk) <= 10 for chunk in chunks)
    assert " ".join(chunks) == text
