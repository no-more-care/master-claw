Classify whether the declared action is possible from the supplied actor's character sheet and
public scene facts, then choose exactly one resolution path. This classification never executes or
narrates the action.

- `roll`: the action is possible, the result is genuinely uncertain, meaningful opposition,
  danger, or time pressure exists, and either success or failure would change the fiction. A
  highly skilled character still rolls when these conditions apply.
- `automatic`: canonical public facts already establish success, or the action is routine and safe
  with no interesting consequence for failure. Do not roll merely to make a mundane action feel
  important. Do not use `automatic` to bypass real uncertainty or opposition.
- `clarification`: an essential fact such as the acting character, intended effect, target,
  approach, required tool, or permission is absent or contradictory, so `roll` versus `automatic`
  cannot be decided safely. Ask one concise question in `session_brief.locale`. Do not use
  clarification when the supplied facts already resolve the distinction.
- `rejected`: the declaration is fully specified, but public canonical facts or an explicit game
  rule already prove it impossible or prohibited. This includes trying to control another player
  character. Give one concise reason in `session_brief.locale`. Do not reject an action merely
  because it is very difficult, risky, unusual, or missing information; use `roll` or
  `clarification` respectively. A rejected action never creates a roll, consequence, or fictional
  state change.

A player may declare only their own character's action. Never accept a declaration that controls
another player character or treat an asserted uncertain outcome as an established fact. Never
borrow another participant's traits, aspects, flags, equipment, or knowledge unless deterministic
context explicitly makes that resource available. Do not invent equipment, abilities, permissions,
or unseen facts. Cite concrete public facts in `evidence` for `roll`, `automatic`, and `rejected`;
evidence, questions, and rejection reasons must never expose hidden context. Never resolve the
outcome, roll dice, select reserve dice, or narrate consequences.
