from masterclaw.app.world_settings import render_world_settings


def test_world_settings_renderer_includes_values_and_provenance() -> None:
    rendered = render_world_settings(
        {"genre": "нуар", "themes": ["память", "долг"]},
        {"genre": "player", "themes": "player"},
    )
    assert "НАСТРОЙКИ МИРА" in rendered
    assert "нуар" in rendered
    assert "память, долг" in rendered
    assert "задано игроком" in rendered
    assert "по умолчанию" in rendered
