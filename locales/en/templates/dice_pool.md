# Dice Pool Calculation Format (compact)

Single line: list of applicable sources + total + difficulty. No tables, no headers, no explanations.

## Template
```
🎲 Pool (X dice): <trait A> +1, <aspect 1> +1, <trait B> +1, <aspect 2> +1, flag "<F>" +1. Reserve?
Difficulty: N.
```

## Example
```
🎲 Pool (5 dice): Smith's Instinct +1, hidden mechanisms +1, Magic Outsider +1, illusion skepticism +1, flag "Distrusts magic" +1. Reserve?
Difficulty: 2.
```

## Rules
- Single line enumeration, commas. Each element = "name +1".
- Do NOT specify which trait an aspect belongs to (noise — player remembers their sheet).
- Do NOT draw a wide markdown table with "Source | Reason | Dice" columns — wastes tokens.
- "Reserve?" at the end — short question to the player about how many reserve dice to add.
- After player answers, roll with reserve included.

## If the line is too long
Only if applicable sources are truly many (6+) and one line becomes unreadable — wrap in a Discord code block:
```
🎲 Pool (7 dice):
  Trait A +1
  Aspect 1 +1
  Trait B +1
  Aspect 2 +1
  Flag "F" +1
  Aspect 3 +1
  Trait C +1
Difficulty: 3. Reserve: 4/7. How many from reserve?
```

This is an exception, not the rule — one line is better in 90% of cases.

## Forbidden
- "Fits / Maybe / Doesn't fit" analysis tables — that's an ANALYSIS used when player explicitly asks "what are my options?". For a normal declaration — just the pool, nothing else.
- Markdown table (`| Source | Reason | Dice |`) WITHOUT wrapping in a code block — Discord doesn't render tables properly and it turns into unreadable mush.
