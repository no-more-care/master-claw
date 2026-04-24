# Scene sheet template (English locale)

Compact YAML cheatsheet for a single scene/location. Lives in `games/<game>/scenes/<scene_id>.md`.

Rules:
- Tags and short phrases only. NO prose.
- `scene_id` — snake_case ASCII, deterministic parent-location prefix (`ivren_tavern_bearded_fish`, `krekhol_herbalist_house`).
- Object names and tag values — in the game language (this template — English).
- YAML schema keys (`type`, `parent`, ...) — English, they ARE the schema.

## Template

```yaml
# scene: <scene_id>
type: location                   # location | sub_location | event_site
parent: <parent_scene_id_or_null>
first_visited: "<day/time>"
last_visited: "<day/time>"
linked_npcs: [<npc_id>, <npc_id>]

layout:
  - <position_and_property_tag>  # e.g. entrance_south, bar_north, hearth_west

props:
  <object>: [<tag>, <tag>, ...]  # deterministic visual details
  # e.g. bar: [oak, scratched, two_mugs]

atmosphere_tags: [<tag>, ...]    # smell/light/sound/tempo

state_changes:
  - "<day/time>: <brief_change>"
```

## Example

```yaml
# scene: ivren_tavern_bearded_fish
type: location
parent: ivren
first_visited: "day 1, evening"
last_visited: "day 2, midday"
linked_npcs: [bron, edwin_stranger, toby_stranger, dog_durak]

layout:
  - entrance_south
  - bar_north_oak
  - hearth_west
  - stairs_east_up
  - tables_center_4_long_benches

props:
  bar: [oak, scratched, coppers_under_counter_42, two_mugs]
  hearth: [brick, live_fire]
  windows: [two_south, inside_shutters]

atmosphere_tags: [warm, beer, roasting_meat, muted_hum]

state_changes:
  - "day 2, midday: dog by hearth, fed"
```
