from __future__ import annotations

from masterclaw.app.world_settings import (
    WORLD_SETTING_GROUPS,
    WORLD_SETTING_HINTS,
    WORLD_SETTING_VALUE_LABELS,
    complete_world_settings,
)
from masterclaw.domain.models import GameLifecycle
from masterclaw.storage.sqlite import SQLiteStore


def _short(value: object, limit: int) -> str:
    text = " ".join(str(value or "—").split())
    return text if len(text) <= limit else text[: limit - 1].rstrip() + "…"


def _pregen_connections(item: dict[str, object]) -> str:
    links = item.get("connections", [])
    rendered = [
        f"{link.get('character_name')}: {link.get('relationship')}"
        for link in links
        if isinstance(link, dict)
    ]
    return _short("; ".join(rendered), 220)


def _lines(title: str, sections: list[tuple[str | None, list[str]]]) -> str:
    rendered = [f"## {title}"]
    for index, (heading, rows) in enumerate(sections):
        if index:
            rendered.append("━━━━━━━━━━━━━━━━━━━━━━━━")
        if heading:
            rendered.append(f"### {heading}")
        rendered.extend(rows)
    return "\n".join(rendered)


def _world_panel(store: SQLiteStore, workspace: dict[str, object]) -> str:
    world = store.world_state(str(workspace["world_id"]))
    content = store.world_content(str(workspace["world_id"])) or {}
    settings, sources = complete_world_settings(
        dict(workspace["settings"]), dict(workspace["sources"])
    )
    stage = str(workspace["stage"])

    def setting_rows(fields: tuple[tuple[str, str], ...]) -> list[str]:
        rows = []
        for key, label in fields:
            value = settings.get(key)
            if isinstance(value, bool):
                value = "включена" if value else "выключена"
            elif isinstance(value, list):
                value = ", ".join(str(item) for item in value) or "не заданы — будут предложены"
            else:
                value = WORLD_SETTING_VALUE_LABELS.get(key, {}).get(value, value)
            provenance = "задано игроками" if sources.get(key) == "player" else "по умолчанию"
            hint = WORLD_SETTING_HINTS.get(key)
            note = provenance if hint is None else f"{provenance}; {hint}"
            rows.append(f"- **{label}:** {_short(value, 180)}  _[{note}]_")
        return rows

    if stage == "collecting":
        status = "сбор вводных — генерация ещё не запускалась"
        next_step = "Добавьте детали или напишите, что можно готовить черновик."
    else:
        status = "черновик сгенерирован — ожидает проверки игроков"
        next_step = "Напишите правки/дополнения или явно подтвердите мир."
    sections: list[tuple[str | None, list[str]]] = [
        (None, ["**Режим:** создание мира", f"**Состояние:** {status}"]),
    ]
    sections.extend((heading, setting_rows(fields)) for heading, fields in WORLD_SETTING_GROUPS)
    premise = content.get("premise")
    if premise:
        locations = [
            f"**{item.get('name', 'Без названия')}:** {_short(item.get('description'), 150)}"
            for item in content.get("locations", [])[:3]
            if isinstance(item, dict)
        ]
        sections.append(
            (
                "Публичная вводная",
                [
                    f"> {_short(premise, 420)}",
                    f"**Темы мира:** {_short(', '.join(content.get('themes', [])), 180)}",
                ],
            )
        )
        if locations:
            sections.append(("Ключевые места", locations))
        factions = content.get("factions") or []
        tensions = content.get("tensions") or []
        sections.append(
            (
                "Силы и конфликты",
                [
                    f"**Фракции:** {_short(', '.join(factions), 220)}",
                    f"**Напряжения:** {_short('; '.join(tensions), 280)}",
                ],
            )
        )
    sections.append(("Следующий шаг", [next_step]))
    title = world.title if world is not None else str(settings.get("title", "Новый мир"))
    return _lines(f"🌍 СОЗДАНИЕ МИРА · {title}", sections)


def _preparation_panel(store: SQLiteStore, game_id: str) -> str:
    game = store.game_state(game_id)
    assert game is not None
    world = store.world_state(game.world_id)
    content = store.world_content(game.world_id) or {}
    templates = content.get("character_templates") or []
    template_rows = [
        f"**{item.get('name', 'Без имени')}:** {_short(item.get('concept'), 150)} — "
        f"{_short(item.get('hook'), 130)}\n"
        f"_Связи:_ {_pregen_connections(item)}"
        for item in templates
        if isinstance(item, dict)
    ] or ["Пока нет подготовленных болванчиков; можно описать своего персонажа свободно."]
    roster = store.character_roster(game_id)
    roster_rows = [f"**{item['name']}** · персонаж готов" for item in roster] or [
        "Никто ещё не зарегистрировал персонажа."
    ]
    premise = _short(content.get("premise"), 360)
    preparation_ready = bool(store.character_count(game_id) and game.narrative_channel_id)
    locations = ", ".join(
        str(item.get("name")) for item in content.get("locations", [])[:5] if isinstance(item, dict)
    )
    return _lines(
        f"🧭 ПОДГОТОВКА · {world.title if world else 'Мир'}",
        [
            (
                None,
                [
                    "**Режим:** подготовка к игре",
                    "**Состояние:** "
                    + ("можно начинать" if preparation_ready else "ожидаем персонажей"),
                ],
            ),
            (
                "Что известно игрокам",
                [
                    premise,
                    f"**Темы:** {_short(', '.join(content.get('themes', [])), 180)}",
                    f"**Ключевые места:** {_short(locations, 220)}",
                    f"**Фракции:** {_short(', '.join(content.get('factions', [])), 220)}",
                ],
            ),
            ("Готовые персонажи", template_rows),
            ("Игроки", roster_rows),
            (
                "Настройки",
                [
                    f"Прогрессия: {'включена' if game.progression_enabled else 'выключена'}",
                    f"Права рассказчика: {game.narrator_rights_level.value}",
                    f"Восстановление запаса: {game.reserve_recovery_mode.value}",
                ],
            ),
        ],
    )


def _play_panel(store: SQLiteStore, game_id: str, player_id: str) -> str:
    game = store.game_state(game_id)
    assert game is not None
    world = store.world_state(game.world_id)
    scene = store.scene_projection(game_id=game_id, player_id=player_id)
    scene_id = str(scene["scene_id"]) if scene is not None else None
    state = dict(scene["state"]) if scene else {}
    description = state.get("description")
    if not description and scene:
        content = store.world_content(game.world_id) or {}
        for location in content.get("locations", []):
            if isinstance(location, dict) and location.get("name") == scene["title"]:
                description = location.get("description")
                break
    roster_rows = []
    for item in store.character_roster(game_id):
        conditions = ", ".join(item["conditions"]) or "без состояний"
        here = "здесь" if scene_id and item["scene_id"] == scene_id else "в другой сцене"
        roster_rows.append(
            f"**{item['name']}** · запас "
            f"{item['reserve_current']}/{item['reserve_maximum']} · {conditions} · {here}"
        )
    return _lines(
        f"🎭 ИГРА · {world.title if world else 'Мир'}",
        [
            (
                None,
                [
                    "**Режим:** игра",
                    f"**Текущая сцена:** {scene['title'] if scene else 'не определена'}",
                ],
            ),
            ("Локация", [_short(description, 360)]),
            ("Участники", roster_rows or ["В сцене пока нет персонажей."]),
        ],
    )


def render_status_panel(store: SQLiteStore, *, channel_id: str, player_id: str) -> str:
    workspace = store.world_workspace(channel_id)
    if workspace is not None:
        return _world_panel(store, workspace)
    channel = store.channel_state(channel_id)
    if channel.game_id is None:
        return _lines(
            "🌐 УПРАВЛЕНИЕ МИРАМИ",
            [
                (
                    None,
                    [
                        "**Режим:** выбор или создание мира",
                        "**Состояние:** мир для игры пока не выбран",
                    ],
                )
            ],
        )
    if channel.lifecycle is GameLifecycle.PREPARING:
        return _preparation_panel(store, channel.game_id)
    return _play_panel(store, channel.game_id, player_id)
