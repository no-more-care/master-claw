from __future__ import annotations

from masterclaw.app.world_settings import (
    WORLD_SETTING_GROUPS,
    WORLD_SETTING_HINTS,
    WORLD_SETTING_VALUE_LABELS,
    complete_world_settings,
)
from masterclaw.domain.models import ChannelState, GameLifecycle
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


def _world_panel(
    store: SQLiteStore,
    workspace: dict[str, object],
    *,
    locale: str,
) -> str:
    world = store.world_state(str(workspace["world_id"]))
    content = store.world_content(str(workspace["world_id"])) or {}
    settings, sources = complete_world_settings(
        dict(workspace["settings"]), dict(workspace["sources"])
    )
    stage = str(workspace["stage"])

    english_groups = {
        "Мир и сюжет": "World and story",
        "Нарратив": "Narrative",
        "Правила игры": "Game rules",
        "Готовые персонажи": "Pregenerated characters",
    }

    def setting_rows(fields: tuple[tuple[str, str], ...]) -> list[str]:
        rows = []
        for key, label in fields:
            value = settings.get(key)
            if isinstance(value, bool):
                value = (
                    ("enabled" if value else "disabled")
                    if locale == "en"
                    else ("включена" if value else "выключена")
                )
            elif isinstance(value, list):
                value = ", ".join(str(item) for item in value) or (
                    "not specified — suggestions will be generated"
                    if locale == "en"
                    else "не заданы — будут предложены"
                )
            elif locale != "en":
                value = WORLD_SETTING_VALUE_LABELS.get(key, {}).get(value, value)
            provenance = (
                (
                    "player supplied"
                    if sources.get(key) == "player"
                    else "inferred from message language"
                    if sources.get(key) == "inferred"
                    else "default"
                )
                if locale == "en"
                else (
                    "задано игроками"
                    if sources.get(key) == "player"
                    else "определено по языку сообщения"
                    if sources.get(key) == "inferred"
                    else "по умолчанию"
                )
            )
            hint = None if locale == "en" else WORLD_SETTING_HINTS.get(key)
            note = provenance if hint is None else f"{provenance}; {hint}"
            rendered_label = key.replace("_", " ").title() if locale == "en" else label
            rows.append(f"- **{rendered_label}:** {_short(value, 180)}  _[{note}]_")
        return rows

    if stage == "collecting":
        status = (
            "collecting inputs — generation has not started"
            if locale == "en"
            else "сбор вводных — генерация ещё не запускалась"
        )
        next_step = (
            "Add details or say that the story draft can be generated."
            if locale == "en"
            else "Добавьте детали или напишите, что можно готовить черновик."
        )
    else:
        status = (
            "draft generated — awaiting player review"
            if locale == "en"
            else "черновик сгенерирован — ожидает проверки игроков"
        )
        next_step = (
            "Describe revisions or explicitly approve the world."
            if locale == "en"
            else "Напишите правки/дополнения или явно подтвердите мир."
        )
    sections: list[tuple[str | None, list[str]]] = [
        (
            None,
            [
                "**Mode:** world creation" if locale == "en" else "**Режим:** создание мира",
                f"**{'Status' if locale == 'en' else 'Состояние'}:** {status}",
            ],
        ),
    ]
    sections.extend(
        (
            english_groups.get(heading, heading) if locale == "en" else heading,
            setting_rows(fields),
        )
        for heading, fields in WORLD_SETTING_GROUPS
    )
    premise = content.get("premise")
    if premise:
        locations = [
            f"**{item.get('name', 'Untitled' if locale == 'en' else 'Без названия')}:** "
            f"{_short(item.get('description'), 150)}"
            for item in content.get("locations", [])[:3]
            if isinstance(item, dict)
        ]
        sections.append(
            (
                "Public premise" if locale == "en" else "Публичная вводная",
                [
                    f"> {_short(premise, 420)}",
                    (
                        f"**World themes:** {_short(', '.join(content.get('themes', [])), 180)}"
                        if locale == "en"
                        else f"**Темы мира:** {_short(', '.join(content.get('themes', [])), 180)}"
                    ),
                ],
            )
        )
        if locations:
            sections.append(("Key locations" if locale == "en" else "Ключевые места", locations))
        factions = content.get("factions") or []
        tensions = content.get("tensions") or []
        sections.append(
            (
                "Forces and conflicts" if locale == "en" else "Силы и конфликты",
                [
                    f"**{'Factions' if locale == 'en' else 'Фракции'}:** "
                    f"{_short(', '.join(factions), 220)}",
                    f"**{'Tensions' if locale == 'en' else 'Напряжения'}:** "
                    f"{_short('; '.join(tensions), 280)}",
                ],
            )
        )
    sections.append(("Next step" if locale == "en" else "Следующий шаг", [next_step]))
    default_title = "New world" if locale == "en" else "Новый мир"
    title = world.title if world is not None else str(settings.get("title", default_title))
    panel_title = "WORLD CREATION" if locale == "en" else "СОЗДАНИЕ МИРА"
    return _lines(f"🌍 {panel_title} · {title}", sections)


def _preparation_panel(store: SQLiteStore, game_id: str, *, locale: str) -> str:
    game = store.game_state(game_id)
    assert game is not None
    world = store.world_state(game.world_id)
    content = store.world_content(game.world_id) or {}
    templates = content.get("character_templates") or []
    template_rows = [
        f"**{item.get('name', 'Unnamed' if locale == 'en' else 'Без имени')}:** "
        f"{_short(item.get('concept'), 150)} — "
        f"{_short(item.get('hook'), 130)}\n"
        f"_{'Connections' if locale == 'en' else 'Связи'}:_ {_pregen_connections(item)}"
        for item in templates
        if isinstance(item, dict)
    ] or [
        (
            "No pregenerated characters are available yet; describe your own character freely."
            if locale == "en"
            else "Пока нет подготовленных болванчиков; можно описать своего персонажа свободно."
        )
    ]
    roster = store.character_roster(game_id)
    roster_rows = [
        f"**{item['name']}** · {'character ready' if locale == 'en' else 'персонаж готов'}"
        for item in roster
    ] or [
        (
            "No player has registered a character yet."
            if locale == "en"
            else "Никто ещё не зарегистрировал персонажа."
        )
    ]
    premise = _short(content.get("premise"), 360)
    preparation_ready = bool(store.character_count(game_id) and game.narrative_channel_id)
    progression_status = (
        "enabled"
        if locale == "en" and game.progression_enabled
        else "disabled"
        if locale == "en"
        else "включена"
        if game.progression_enabled
        else "выключена"
    )
    locations = ", ".join(
        str(item.get("name")) for item in content.get("locations", [])[:5] if isinstance(item, dict)
    )
    return _lines(
        (
            f"🧭 PREPARATION · {world.title if world else 'World'}"
            if locale == "en"
            else f"🧭 ПОДГОТОВКА · {world.title if world else 'Мир'}"
        ),
        [
            (
                None,
                [
                    (
                        "**Mode:** game preparation"
                        if locale == "en"
                        else "**Режим:** подготовка к игре"
                    ),
                    (
                        "**Status:** "
                        + ("ready to start" if preparation_ready else "waiting for characters")
                        if locale == "en"
                        else "**Состояние:** "
                        + ("можно начинать" if preparation_ready else "ожидаем персонажей")
                    ),
                ],
            ),
            (
                "What the players know" if locale == "en" else "Что известно игрокам",
                [
                    premise,
                    f"**{'Themes' if locale == 'en' else 'Темы'}:** "
                    f"{_short(', '.join(content.get('themes', [])), 180)}",
                    f"**{'Key locations' if locale == 'en' else 'Ключевые места'}:** "
                    f"{_short(locations, 220)}",
                    f"**{'Factions' if locale == 'en' else 'Фракции'}:** "
                    f"{_short(', '.join(content.get('factions', [])), 220)}",
                ],
            ),
            ("Pregenerated characters" if locale == "en" else "Готовые персонажи", template_rows),
            ("Players" if locale == "en" else "Игроки", roster_rows),
            (
                "Settings" if locale == "en" else "Настройки",
                [
                    (
                        f"Progression: {progression_status}"
                        if locale == "en"
                        else f"Прогрессия: {progression_status}"
                    ),
                    (
                        f"Narrator rights: {game.narrator_rights_level.value}"
                        if locale == "en"
                        else f"Права рассказчика: {game.narrator_rights_level.value}"
                    ),
                    (
                        f"Reserve recovery: {game.reserve_recovery_mode.value}"
                        if locale == "en"
                        else f"Восстановление запаса: {game.reserve_recovery_mode.value}"
                    ),
                ],
            ),
        ],
    )


def _play_panel(
    store: SQLiteStore,
    game_id: str,
    player_id: str,
    *,
    channel_id: str,
    locale: str,
) -> str:
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
        conditions = ", ".join(item["conditions"]) or (
            "no conditions" if locale == "en" else "без состояний"
        )
        here = (
            ("here" if scene_id and item["scene_id"] == scene_id else "in another scene")
            if locale == "en"
            else ("здесь" if scene_id and item["scene_id"] == scene_id else "в другой сцене")
        )
        roster_rows.append(
            f"**{item['name']}** · {'reserve' if locale == 'en' else 'запас'} "
            f"{item['reserve_current']}/{item['reserve_maximum']} · {conditions} · {here}"
        )
    if game.lifecycle is GameLifecycle.PAUSED:
        mode = "game paused" if locale == "en" else "игра на паузе"
        lifecycle_note = (
            "Scene actions are paused. Use `/game resume` to continue."
            if locale == "en"
            else "Действия сцены приостановлены. `/game resume` — продолжить."
        )
    elif game.lifecycle is GameLifecycle.FINISHED:
        mode = "game finished" if locale == "en" else "игра завершена"
        lifecycle_note = (
            "The session is closed. Use `/game new` to choose the next world."
            if locale == "en"
            else "Сессия закрыта. `/game new` — перейти к выбору следующего мира."
        )
    else:
        mode = "play" if locale == "en" else "игра"
        lifecycle_note = "The session is active." if locale == "en" else "Сессия активна."
    pending = store.open_pending(
        game_id=game_id,
        player_id=player_id,
        channel_id=channel_id,
    )
    current_scene_title = (
        str(scene["title"]) if scene else ("not set" if locale == "en" else "не определена")
    )
    sections = [
        (
            None,
            [
                f"**{'Mode' if locale == 'en' else 'Режим'}:** {mode}",
                f"**{'Status' if locale == 'en' else 'Состояние'}:** {lifecycle_note}",
                f"**{'Current scene' if locale == 'en' else 'Текущая сцена'}:** "
                f"{current_scene_title}",
            ],
        ),
        ("Location" if locale == "en" else "Локация", [_short(description, 360)]),
        (
            "Participants" if locale == "en" else "Участники",
            roster_rows
            or [
                (
                    "There are no characters in this scene yet."
                    if locale == "en"
                    else "В сцене пока нет персонажей."
                )
            ],
        ),
    ]
    if pending is not None:
        sections.append(
            (
                "Pending step" if locale == "en" else "Незавершённый шаг",
                [
                    f"**{pending.kind.value}:** {_short(pending.prompt, 260)}",
                    (
                        "Reply or write `cancel`."
                        if locale == "en"
                        else "Ответьте или напишите `отмена`."
                    ),
                ],
            )
        )
    return _lines(
        (
            f"🎭 PLAY · {world.title if world else 'World'}"
            if locale == "en"
            else f"🎭 ИГРА · {world.title if world else 'Мир'}"
        ),
        sections,
    )


def render_status_panel(
    store: SQLiteStore,
    *,
    channel_id: str,
    player_id: str,
    locale: str = "ru",
    channel_state: ChannelState | None = None,
) -> str:
    if channel_state is not None and channel_state.channel_id != channel_id:
        raise ValueError("status-panel channel state belongs to a different channel")
    channel = channel_state or store.channel_state(channel_id)
    # A queued message can legitimately retain game A after the live Discord channel has moved
    # to game B. In that case a live world workspace is unrelated to the message's frozen route.
    workspace = store.world_workspace(channel_id) if channel.game_id is None else None
    if workspace is not None:
        return _world_panel(store, workspace, locale=locale)
    if channel.game_id is None:
        if locale == "en":
            return _lines(
                "🌐 WORLD MANAGEMENT",
                [
                    (
                        None,
                        [
                            "**Mode:** world selection or creation",
                            "**Status:** no world has been selected for play",
                        ],
                    )
                ],
            )
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
    if channel.lifecycle in {GameLifecycle.DRAFT, GameLifecycle.PREPARING}:
        return _preparation_panel(store, channel.game_id, locale=locale)
    return _play_panel(
        store,
        channel.game_id,
        player_id,
        channel_id=channel_id,
        locale=locale,
    )
