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
