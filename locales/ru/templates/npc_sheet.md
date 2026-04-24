# NPC sheet template (Russian locale)

Компактная YAML-шпаргалка для одного импровизированного NPC. Живёт в `games/<game>/npcs_adhoc/<npc_id>.md`.

Правила:
- Канонические NPC (из `worlds/<world>/npcs.md`) сюда НЕ копируем. Только импровизированные — те, кого не было в мире до игры.
- Только теги и короткие фразы. НИКАКОЙ прозы.
- `npc_id` — snake_case ASCII (`edwin_stranger`, `toby_stranger`, `old_woman_well`).
- Теги — на языке игры (в этом шаблоне — русский).

## Template

```yaml
# npc: <npc_id>
type: improvised                 # improvised | canonical_override
first_seen: "<день/время/сцена>"
known_name: <строка_или_null>    # null, если имя ещё не назвали

appearance: [<тег>, <тег>, ...]
voice: [<тег>, <тег>, ...]
known_facts: [<тег>, <тег>, ...]

relation_to_party: [<тег>, ...]  # друг, нейтрал, должник, враг, ...
recent_interactions:
  - "<день/время>: <краткое_описание>"
```

## Пример

```yaml
# npc: edwin_stranger
type: improvised
first_seen: "день 2, середина дня, таверна Бородатая рыба"
known_name: "Эдвин"

appearance: [худощавый, длинный_плащ_серый, шрам_над_левой_бровью]
voice: [тихий, отрывистый, южный_акцент]
known_facts: [пьёт_чёрное_пиво, с_Тоби_в_паре, интересуется_лесом]

relation_to_party: [нейтрал, настороженный]
recent_interactions:
  - "день 2, полдень: молча пил за дальним столом, следил за дверью"
```
