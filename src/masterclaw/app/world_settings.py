from __future__ import annotations

from collections.abc import Mapping

WORLD_SETTING_DEFAULTS: dict[str, object] = {
    "genre": "приключенческая фантастика",
    "tone": "серьёзный, но не безнадёжный",
    "scale": "один регион с несколькими значимыми локациями",
    "player_role": "самостоятельная группа героев, связанная общей проблемой",
    "themes": ["исследование", "выбор и последствия"],
    "content_constraints": ["без дополнительных ограничений"],
    "narrative_style": "кинематографичный, атмосферный, с фокусом на действиях игроков",
    "narrative_perspective": "третье лицо, ограниченная перспектива текущей сцены",
    "narrative_detail": "balanced",
    "narrator_rights_level": "minor",
    "reserve_recovery_mode": "both",
    "progression_enabled": False,
    "locale": "ru",
    "pregenerated_character_count": 3,
    "pregenerated_character_briefs": [],
}

WORLD_SETTING_GROUPS: tuple[tuple[str, tuple[tuple[str, str], ...]], ...] = (
    (
        "Мир и сюжет",
        (
            ("genre", "Жанр"),
            ("tone", "Тон"),
            ("scale", "Масштаб"),
            ("player_role", "Роль игроков"),
            ("themes", "Темы"),
            ("content_constraints", "Ограничения и стоп-лист"),
        ),
    ),
    (
        "Нарратив",
        (
            ("narrative_style", "Стиль прозы"),
            ("narrative_perspective", "Перспектива"),
            ("narrative_detail", "Детализация"),
            ("locale", "Язык"),
        ),
    ),
    (
        "Правила игры",
        (
            ("narrator_rights_level", "Права рассказчика"),
            ("reserve_recovery_mode", "Восстановление кубов запаса"),
            ("progression_enabled", "Прогрессия персонажей"),
        ),
    ),
    (
        "Готовые персонажи",
        (
            ("pregenerated_character_count", "Количество прегенов"),
            ("pregenerated_character_briefs", "Заданные концепты"),
        ),
    ),
)

WORLD_SETTING_VALUE_LABELS: dict[str, dict[object, str]] = {
    "narrative_detail": {
        "concise": "краткая (`concise`)",
        "balanced": "средняя (`balanced`)",
        "detailed": "подробная (`detailed`)",
    },
    "narrator_rights_level": {
        "disabled": "только системный рассказчик (`disabled`)",
        "minor": "малые права игрока (`minor`)",
        "significant": "значительные права игрока (`significant`)",
        "madness": "максимальная свобода (`madness`)",
    },
    "reserve_recovery_mode": {
        "safe_rest": "только безопасный отдых (`safe_rest`)",
        "roleplay_award": "только награда за отыгрыш (`roleplay_award`)",
        "both": "оба способа (`both`)",
    },
    "locale": {
        "ru": "русский (`ru`)",
        "en": "английский (`en`)",
    },
}

WORLD_SETTING_HINTS: dict[str, str] = {
    "narrative_style": "можно описать свободно",
    "narrative_perspective": "можно описать свободно",
    "narrative_detail": "варианты: concise / balanced / detailed",
    "narrator_rights_level": "варианты: disabled / minor / significant / madness",
    "reserve_recovery_mode": "варианты: safe_rest / roleplay_award / both",
    "progression_enabled": "можно включить или выключить",
    "locale": "варианты: ru / en",
    "pregenerated_character_count": "от 3 до 6",
    "pregenerated_character_briefs": "можно перечислить желаемые архетипы",
}


def complete_world_settings(
    settings: Mapping[str, object], sources: Mapping[str, str]
) -> tuple[dict[str, object], dict[str, str]]:
    complete = dict(WORLD_SETTING_DEFAULTS)
    complete.update(settings)
    complete_sources = {key: "default" for key in WORLD_SETTING_DEFAULTS}
    complete_sources.update(sources)
    return complete, complete_sources


def game_defaults(settings: Mapping[str, object]) -> dict[str, object]:
    return {
        key: settings.get(key, default)
        for key, default in WORLD_SETTING_DEFAULTS.items()
        if key
        in {
            "narrative_style",
            "narrative_perspective",
            "narrative_detail",
            "narrator_rights_level",
            "reserve_recovery_mode",
            "progression_enabled",
            "locale",
        }
    }


def render_world_settings(
    settings: Mapping[str, object], sources: Mapping[str, str], *, locale: str = "ru"
) -> str:
    complete, complete_sources = complete_world_settings(settings, sources)
    title = "НАСТРОЙКИ МИРА" if locale == "ru" else "WORLD SETTINGS"
    source_labels = (
        {"player": "задано игроком", "default": "по умолчанию"}
        if locale == "ru"
        else {"player": "player supplied", "default": "default"}
    )
    rows = [f"## ⚙️ {title}"]
    for group, fields in WORLD_SETTING_GROUPS:
        rows.append(f"### {group}" if locale == "ru" else "### Settings")
        for key, label in fields:
            value = complete[key]
            rendered = ", ".join(str(item) for item in value) if isinstance(value, list) else value
            if not isinstance(value, list):
                rendered = WORLD_SETTING_VALUE_LABELS.get(key, {}).get(value, rendered)
            source = source_labels.get(complete_sources.get(key, "default"), "default")
            rows.append(f"- **{label if locale == 'ru' else key}**: {rendered} _({source})_")
    return "\n".join(rows)
