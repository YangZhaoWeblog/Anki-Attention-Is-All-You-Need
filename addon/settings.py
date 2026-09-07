"""Profile-scoped dashboard settings with Anki config as the sole source."""

from __future__ import annotations

from copy import deepcopy
from typing import Any


def _budget(value: Any, default: int = 30) -> int:
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        parsed = default
    if not 1 <= parsed <= 1440:
        raise ValueError("每日预算须为 1-1440 分钟。")
    return parsed


def initialize(data, config: dict[str, Any], profile_name: str) -> dict[str, Any]:
    refs = data.deck_refs()
    saved = (config.get("settingsByProfile") or {}).get(profile_name)
    budget = _budget(
        saved.get("dailyBudgetMinutes") if saved else config.get("dailyBudgetMinutes", 30)
    )
    deck_id = int(saved["deckId"]) if saved and saved.get("deckId") is not None else data.current_deck_id()
    valid = deck_id is not None and data.resolve_deck(deck_id) is not None
    return {
        "selectedDeckId": int(deck_id) if valid else None,
        "selectionRequired": not valid,
        "deckRefs": refs,
        "dailyBudgetMinutes": budget,
    }


def save(
    data,
    config: dict[str, Any],
    profile_name: str,
    deck_id: int,
    daily_budget_minutes: int,
) -> tuple[dict[str, Any], dict[str, int]]:
    deck_id = int(deck_id)
    if data.resolve_deck(deck_id) is None:
        raise ValueError("所选牌组不存在，请重新选择。")
    saved = {
        "deckId": deck_id,
        "dailyBudgetMinutes": _budget(daily_budget_minutes),
    }
    updated = deepcopy(config)
    by_profile = dict(updated.get("settingsByProfile") or {})
    by_profile[profile_name] = {**by_profile.get(profile_name, {}), **saved}
    updated["settingsByProfile"] = by_profile
    return updated, saved
