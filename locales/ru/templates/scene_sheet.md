# Scene sheet template (Russian locale)

Компактная YAML-шпаргалка для одной сцены/локации. Живёт в `games/<game>/scenes/<scene_id>.md`.

Правила:
- Только теги и короткие фразы. НИКАКОЙ прозы.
- `scene_id` — snake_case ASCII, детерминированный префикс по родительской локации (`ivren_tavern_bearded_fish`, `krekhol_herbalist_house`).
- Имена объектов, тегов — на языке игры (в этом шаблоне — русский).
- Служебные ключи YAML (`type`, `parent`, ...) — на английском, они являются частью схемы.

## Template

```yaml
# scene: <scene_id>
type: location                   # location | sub_location | event_site
parent: <parent_scene_id_or_null>
first_visited: "<день/время>"
last_visited: "<день/время>"
linked_npcs: [<npc_id>, <npc_id>]

layout:
  - <тег_положения_и_свойства>   # напр. вход_юг, стойка_север, очаг_запад

props:
  <объект>: [<тег>, <тег>, ...]  # детерминированные визуальные детали
  # напр. стойка: [дубовая, царапины, две_кружки]

atmosphere_tags: [<тег>, ...]    # запах/свет/звук/темп

state_changes:
  - "<день/время>: <краткая_правка>"
```

## Пример

```yaml
# scene: ivren_tavern_bearded_fish
type: location
parent: ivren
first_visited: "день 1, вечер"
last_visited: "день 2, середина дня"
linked_npcs: [bron, edwin_stranger, toby_stranger, dog_durak]

layout:
  - вход_юг
  - стойка_север_дубовая
  - очаг_запад
  - лестница_восток_наверх
  - столы_центр_4_длинные_скамьи

props:
  стойка: [дубовая, царапины, медяки_под_стойкой_42, две_кружки]
  очаг: [кирпич, живой_огонь]
  окна: [два_на_юг, ставни_внутри]

atmosphere_tags: [тёплый, пиво, жарят_мясо, приглушённый_гул]

state_changes:
  - "день 2, полдень: собака у очага, покормлена"
```
