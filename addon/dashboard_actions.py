"""Native Anki mutations used by the attention dashboard.

Every function accepts a collection and returns the state the UI should show.
The WebView never predicts a successful mutation.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any


OPTIMIZATION_TAG = "需优化卡片"
HIDDEN_TAG = "看板忽略"


@dataclass
class ActionResult:
    changes: Any
    payload: dict[str, Any]

    def __getitem__(self, key: str) -> Any:
        return self.payload[key]

    def __eq__(self, other: object) -> bool:
        return self.payload == other


def _card_ids(note) -> list[int]:
    return sorted(int(card_id) for card_id in note.card_ids())


def _set_tag(note, tag: str, enabled: bool) -> None:
    tags = [value for value in note.tags if value != tag]
    if enabled:
        tags.append(tag)
    note.tags = tags


def save_note(
    col,
    note_id: int,
    changed_fields: dict[str, str],
    tags: list[str],
) -> ActionResult:
    note = col.get_note(int(note_id))
    available = set(note.keys())
    unknown = sorted(set(changed_fields) - available)
    if unknown:
        raise ValueError(f"字段不存在：{'、'.join(unknown)}")
    for name, value in changed_fields.items():
        note[name] = str(value)
    note.tags = list(dict.fromkeys(str(tag) for tag in tags if str(tag)))
    changes = col.update_note(note)
    return ActionResult(changes, {"noteId": int(note.id), "cardIds": _card_ids(note), "tags": list(note.tags)})


def set_suspended(col, card_id: int, suspended: bool) -> ActionResult:
    card_id = int(card_id)
    if suspended:
        changes = col.sched.suspend_cards([card_id])
    else:
        changes = col.sched.unsuspend_cards([card_id])
    actual = int(col.get_card(card_id).queue) == -1
    return ActionResult(changes, {"cardId": card_id, "suspended": actual})


def set_optimization(col, card_id: int, enabled: bool) -> ActionResult:
    card = col.get_card(int(card_id))
    note = col.get_note(int(card.nid))
    _set_tag(note, OPTIMIZATION_TAG, bool(enabled))
    if enabled:
        undo_target = col.add_custom_undo_entry("暂停并标记待优化")
        col.update_note(note)
        col.sched.suspend_cards([int(card.id)])
        changes = col.merge_undo_entries(undo_target)
    else:
        changes = col.update_note(note)
    return ActionResult(changes, {
        "noteId": int(note.id),
        "cardIds": _card_ids(note),
        "tags": list(note.tags),
        "cardId": int(card.id),
        "suspended": int(col.get_card(int(card.id)).queue) == -1,
    })


def set_hidden(col, card_id: int, enabled: bool) -> ActionResult:
    card = col.get_card(int(card_id))
    note = col.get_note(int(card.nid))
    _set_tag(note, HIDDEN_TAG, bool(enabled))
    changes = col.update_note(note)
    return ActionResult(changes, {
        "noteId": int(note.id),
        "cardIds": _card_ids(note),
        "tags": list(note.tags),
    })


def prepare_delete(col, card_id: int) -> dict[str, Any]:
    card = col.get_card(int(card_id))
    note = col.get_note(int(card.nid))
    card_ids = _card_ids(note)
    return {"noteId": int(note.id), "cardIds": card_ids, "cardCount": len(card_ids)}


def delete_note(col, note_id: int, confirmed_card_ids: list[int]) -> ActionResult:
    note = col.get_note(int(note_id))
    actual = _card_ids(note)
    confirmed = sorted(int(card_id) for card_id in confirmed_card_ids)
    if actual != confirmed:
        raise ValueError("笔记影响范围已变化，请重新确认删除。")
    changes = col.remove_notes([int(note_id)])
    return ActionResult(changes, {"noteId": int(note_id), "cardIds": actual, "cardCount": len(actual)})
