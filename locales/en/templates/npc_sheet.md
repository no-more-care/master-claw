# NPC sheet template (English locale)

Compact YAML cheatsheet for a single improvised NPC. Lives in `games/<game>/npcs_adhoc/<npc_id>.md`.

Rules:
- Canonical NPCs (from `worlds/<world>/npcs.md`) do NOT get copied here. Only improvised ones — NPCs that did not exist in the world before play.
- Tags and short phrases only. NO prose.
- `npc_id` — snake_case ASCII (`edwin_stranger`, `toby_stranger`, `old_woman_well`).
- Tag values — in the game language (this template — English).

## Template

```yaml
# npc: <npc_id>
type: improvised                 # improvised | canonical_override
first_seen: "<day/time/scene>"
known_name: <string_or_null>     # null if name not revealed yet

appearance: [<tag>, <tag>, ...]
voice: [<tag>, <tag>, ...]
known_facts: [<tag>, <tag>, ...]

relation_to_party: [<tag>, ...]  # friend, neutral, indebted, enemy, ...
recent_interactions:
  - "<day/time>: <brief_description>"
```

## Example

```yaml
# npc: edwin_stranger
type: improvised
first_seen: "day 2, midday, tavern Bearded Fish"
known_name: "Edwin"

appearance: [lean, long_grey_cloak, scar_above_left_brow]
voice: [quiet, clipped, southern_accent]
known_facts: [drinks_stout, paired_with_toby, interested_in_forest]

relation_to_party: [neutral, wary]
recent_interactions:
  - "day 2, midday: drank silently at far table, watched the door"
```
