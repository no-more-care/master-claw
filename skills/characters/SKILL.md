# SKILL: Character Management (BlackBirdPie)

## When to use
- Creating a new character
- Adding aspects, flags, traits during play
- Updating conditions, reserve dice, experience
- Displaying character info to players

## Active game
Find GameMaster/games/ folder with status active before any action.
Path: GameMaster/games/<game>/characters/<name>.md

---

## Character file schema (machine format)

This is the canonical storage format. Always read and write characters in this schema.
```yaml
# CHARACTER: <name>

player: "<player name>"

biography: |
  <biography text>

traits:
  - name: "<trait name>"
    level: <2-6>
    aspects:
      - "<aspect 1>"
      - "<aspect 2>"
      # count must equal level
  - name: "<trait name>"
    level: <2-6>
    aspects:
      - "<aspect 1>"

traits_points_total: <sum of all levels, must equal 18>

flags:
  - text: "<flag text>"
    type: relationship  # relationship | personality | goal | belief
    locked: true        # true = set during play, cannot change
  - text: "<flag text>"
    type: personality
    locked: true

reserve_dice:
  current: <0-7>
  maximum: 7

experience:
  earned: <total earned>
  spent: <total spent>
  available: <earned - spent>

conditions:
  - text: "<condition>"
    source: "<how it was assigned>"

change_log:
  - date: "<date>"
    change: "<what changed>"
```

---

## Validation rules

Run these checks before saving any character change:

### Traits
- Count: 3–7 traits total
- Each level: 2–6
- traits_points_total must equal exactly 18
- Aspect count per trait must equal trait level
- Trait name not too broad ("Wizard" — reject) or too narrow ("Magic arrow attack" — reject)

### Aspects
- Must NOT apply in most situations with that trait — if it does, it's too broad
- Once locked: true — cannot be changed or removed
- New aspect: check count does not exceed trait level

### Flags
- Minimum 3 flags required (at least 1 with type: relationship) before game starts
- Once locked: true — cannot be changed
- Flag text should describe personality, not capability
- **Maximum 1 flag applies per roll** — even if multiple flags seem relevant

### Reserve dice
- current must be 0–7
- Never exceeds maximum: 7
- No automatic restoration

### Dice pool (reference only)
⛔ Before building any pool, re-read the character file from disk. See rules/SKILL.md section 1 for full pool rules.
Key reminder: each trait = +1 die (NOT trait level). Max 1 flag per roll.

---

## Procedures

### Create character
1. Collect from player: name, biography, traits with levels, aspects, flags
2. Validate all rules above
3. Write file in schema format
4. Display to player → use format from: `locales/{lang}/templates/character_display.md`
5. Add to players table in game.md
6. Confirm character creation to player

### Add aspect during play
1. Player declares new aspect for a trait
2. Check: trait has room (current aspects < trait.level)
3. Check: aspect is not too broad
4. Add to trait.aspects list, set locked: true
5. Confirm to player in readable format

### Add flag during play
1. Player declares new flag
2. Check: if type relationship — referenced character exists
3. Add to flags, set locked: true
4. Confirm to player

### Update reserve dice
1. Get roll result (success/failure) and dice spent
2. Success: current -= spent
3. Failure: current stays the same + current += 1 (spent returned, +1 added)
4. Cap at maximum: 7
5. Update file, confirm reserve change to player

### Award reserve dice (GM reward for roleplay)
1. Add to current, cap at 7
2. Log in change_log
3. Announce to player

### Spend experience
1. Check experience.available >= cost
2. Apply change (raise trait level, add new trait)
3. If raising level: add 1 new aspect to that trait
4. Update experience.spent
5. Add to change_log
6. Display updated character

### Assign condition
1. Add to conditions with source
2. Add to change_log
3. Announce condition to player

### Remove condition
1. Remove from conditions
2. Add to change_log with reason
3. Announce removal to player

---

## Display format

When showing character info to players:
→ Use format from: `locales/{lang}/templates/character_display.md`

Never show raw YAML to players.

⛔ Reserve dice display rules:
- NEVER call them "stress dice" — correct term is "reserve dice"
- NEVER add "+2" or any modifier — reserve dice give +1 each
- Display only the count, nothing else

---

## Interaction with other skills

| Situation | This skill does | Other skill does |
|---|---|---|
| Roll declared | Provide character data | actions/SKILL.md runs the roll |
| Roll completed | Update reserve, conditions | actions/SKILL.md logs the roll |
| Scene described | Nothing | narrator/SKILL.md describes |
| New fact established | Lock aspect/flag if declared | world/SKILL.md updates state.md |
