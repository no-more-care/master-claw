BlackBirdPie uses a deterministic d6 dice pool. Build the pool only from canonical character and
scene state. Each applicable trait contributes exactly one die; trait level is never a die count.
Each applicable aspect belonging to a selected trait contributes exactly one die. One genuinely
applicable character flag contributes one die. There is no base die. A matching temporary
`extra_die` bonus contributes one die; a matching `difficulty_reduction` bonus reduces difficulty
by one, never below 1. At most one temporary bonus may be selected for a roll. The acting player
chooses reserve dice only after the proposed pool is shown, and each chosen reserve die adds one
die.

The unreduced base difficulty is an integer from 2 through 7. Difficulty 1 exists only as the
effective result of applying one canonical `difficulty_reduction` bonus to base difficulty 2.

Every d6 result of 4, 5, or 6 is one hit. Hits greater than or equal to difficulty are a success;
fewer hits are a failure. Narrator rights are exact: hits greater than difficulty are
`player_success`; hits equal to difficulty are `gm_success`; hits equal to difficulty minus one are
`player_failure`; lower hits are `gm_failure`.

Reserve starts at 7 and cannot exceed 7. On success, reserve dice spent by the acting player are
lost. On failure, those spent dice return and the acting player gains one additional reserve die,
capped at 7. Low or empty reserve never increases difficulty and never prevents an action.

Helping requires another player with a character in the same scene and an open pool awaiting
confirmation. The helper explicitly contributes exactly one die from their reserve without a roll,
penalty, condition, or difficulty change. The helper must have at least one reserve die and cannot
help their own roll or help the same roll twice. The contributed die is lost when the action
succeeds and returned when it fails.
