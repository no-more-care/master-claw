# BlackBirdPie rules — decisions and open contradictions

MasterClaw v2 does not silently resolve contradictions in v1 materials. A disputed value is represented as explicit configuration/policy until the owner decides it.

## Decided: starting trait distribution

Found and decided 2026-07-13.

- `skills/rules/SKILL.md` §5 says each trait has level `2–6`.
- `skills/characters/SKILL.md` schema and validation also say `2–6`.
- `CLAUDE.md`, cheap-tier baseline, marks a generated level 6 as invalid (`>5`).

Owner decision:

- a starting trait has level `2–6`;
- starting trait levels sum to exactly `18`;
- any combination satisfying those constraints is allowed, including `3 × 6`, `6 × 3` and `9 × 2`;
- therefore the effective starting trait count is `3–9`, and the `3–7` limit in the current skills is not valid for v2;
- character advancement follows separate rules and must not reuse starting-character limits implicitly.

Impact:

- character creation validation;
- character migration/import fixtures;
- model evaluation assertions.

Implementation:

- `STARTING_CHARACTER_RULES` fixes count `3–9`, level `2–6`, total `18`;
- `CharacterRules` remains explicit so advancement can receive a separate policy;
- tests cover nine level-2 traits as well as mixed distributions.

## Decided: experience and advancement

Decided 2026-07-13.

- Starting-character constraints do not apply after creation. Advancement can raise a trait above level 6 and can increase total trait points above 18.
- Raising a trait from level `N` to `N+1` costs `N+1` XP: `6→7` costs 7, `9→10` costs 10.
- Raising a trait adds one new aspect so aspect count remains equal to level.
- A new trait costs 3 XP and is created at level 2 with two aspects.
- Advancement is allowed only in a safe situation where the character can spend time developing, such as downtime between missions or a safe city scene.
- A new trait additionally requires a justification explaining where and how it was learned.
- Progression is enabled or disabled in session settings before the game starts. A short one-session game can run with progression disabled.

### Discord active-time accounting

- The application records the time of each player game event.
- For consecutive events, credited active time is `min(actual interval, 5 minutes)`.
- Thus continuous play is credited fully, while a day-long pause adds only five minutes.
- Out-of-order/duplicate timestamps add no time.
- When progression is enabled, crossing each new full 30-minute interval automatically awards one XP to every character.
- Awarded intervals are persisted so duplicate and out-of-order events cannot award the same time twice.
- Players have equal rights; there is no human GM/owner permission in Discord.
- Whether advancement is fictionally safe at the current moment is decided by a bounded GM/state pipeline from the current scene and request. Its typed permit is bound to the scene revision; code still validates XP and the requested sheet change.
