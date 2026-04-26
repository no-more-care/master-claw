#!/usr/bin/env python3
"""
BlackBirdPie Dice Roller — MasterClaw
Usage: python3 roll.py <pool_size> <difficulty>

Rolls pool_size d6 dice, counts hits (4-6), compares to difficulty.
Outputs formatted result for GM and players.
"""

import sys
import random


def roll(pool_size: int, difficulty: int) -> str:
    if pool_size < 1:
        return "❌ Error: pool size must be at least 1"
    if difficulty < 1:
        return "❌ Error: difficulty must be at least 1"

    dice = [random.randint(1, 6) for _ in range(pool_size)]
    hits = sum(1 for d in dice if d >= 4)

    # Format individual dice: bold hits
    dice_display = []
    for d in dice:
        if d >= 4:
            dice_display.append(f"**{d}**")
        else:
            dice_display.append(str(d))

    # Determine narrator rights
    if hits > difficulty:
        outcome = "YES_AND"
        outcome_ru = "Да, и кроме того..."
        narrator = "PLAYER"
        narrator_ru = "Игрок рассказывает"
    elif hits == difficulty:
        outcome = "YES_BUT"
        outcome_ru = "Да, но..."
        narrator = "GM"
        narrator_ru = "Мастер рассказывает"
    elif hits == difficulty - 1:
        outcome = "NO_BUT"
        outcome_ru = "Нет, но..."
        narrator = "PLAYER"
        narrator_ru = "Игрок рассказывает"
    else:
        outcome = "NO_AND"
        outcome_ru = "Нет, и вдобавок..."
        narrator = "GM"
        narrator_ru = "Мастер рассказывает"

    success = hits >= difficulty

    # Build output
    lines = []
    lines.append("─── БРОСОК ───")
    lines.append(f"🎲 Кубы ({pool_size}d6): [{', '.join(dice_display)}]")
    lines.append(f"✊ Успехи: {hits} (из {pool_size})")
    lines.append(f"🎯 Сложность: {difficulty}")
    lines.append(f"📋 Результат: {'✅ УСПЕХ' if success else '❌ ПРОВАЛ'} → {outcome_ru}")
    lines.append(f"🗣️ {narrator_ru}")
    lines.append("──────────────")

    # GM-only section (internal, for log)
    lines.append("")
    lines.append(f"[GM LOG: dice={dice}, hits={hits}, diff={difficulty}, outcome={outcome}, narrator={narrator}]")
    # Reminder for the agent — playtest showed turn_commit.py is frequently
    # skipped after a roll because the model gets pulled into narrating the
    # outcome. This sentinel is read by the agent (not the player) and is
    # the only thing standing between a clean turn and a silent state-drift.
    lines.append("[NEXT STEP: call turn_commit.py with this roll's data BEFORE emitting any narrative response. "
                 "If a new scene/NPC was introduced, scene_note.py FIRST, then turn_commit.py. "
                 "Do NOT respond to the player without these writes.]")

    return "\n".join(lines)


def main():
    if len(sys.argv) != 3:
        print("Usage: python3 roll.py <pool_size> <difficulty>")
        print("Example: python3 roll.py 5 3")
        sys.exit(1)

    try:
        pool_size = int(sys.argv[1])
        difficulty = int(sys.argv[2])
    except ValueError:
        print("Error: pool_size and difficulty must be integers")
        sys.exit(1)

    print(roll(pool_size, difficulty))


if __name__ == "__main__":
    main()
