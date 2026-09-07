"""Anki data module.

A *deep* module: a narrow ``AttentionData`` interface that hides all
collection access (deck lookup, card search, revlog reads, FSRS simulation)
and its batching. Business logic talks only to this interface, so it can be
tested with a trivial fake and never imports ``aqt``/``anki``.

The FSRS private backend is delegated to :mod:`fsrs_adapter`, the single
place that touches Anki's protobuf. ``AnkiData`` itself stays importable
without Anki installed.
"""

from __future__ import annotations

from typing import Any, Protocol

try:  # real addon package
    from . import fsrs_adapter as _fsrs
except ImportError:  # tests load this module standalone
    import fsrs_adapter as _fsrs


_CARD_BATCH = 100
_REVIEW_BATCH = 80


def chunks(items: list[int], size: int) -> list[list[int]]:
    return [items[i : i + size] for i in range(0, len(items), size)]


class AttentionData(Protocol):
    def deck_refs(self) -> list[dict[str, Any]]: ...
    def current_deck_id(self) -> int | None: ...
    def resolve_deck(self, deck_id: int) -> str | None: ...
    def deck_names(self) -> list[str]: ...
    def find_cards(self, query: str) -> list[int]: ...
    def card_infos(self, card_ids: list[int]) -> dict[int, dict[str, Any]]: ...
    def reviews_of_cards(self, card_ids: list[int]) -> dict[int, list[dict[str, Any]]]: ...
    def simulate_fsrs(
        self, deck: str, additional_new: int, days: int, new_limit: int
    ) -> dict[str, Any]: ...
    def easy_days_percentages(self, deck: str) -> list[float]: ...
    def config(self) -> dict[str, Any]: ...


class AnkiData:
    """``AttentionData`` backed by a live Anki collection.

    ``col`` is duck-typed (the methods/attributes Anki's ``Collection`` exposes),
    and ``addon_config`` is a plain dict snapshot of the add-on's config. Both are
    injected so this class has no hard dependency on ``aqt`` and can be unit-tested
    with stubs.
    """

    def __init__(self, col, addon_config: dict[str, Any]) -> None:
        self._col = col
        self._addon_config = dict(addon_config)

    def deck_names(self) -> list[str]:
        return [item.name for item in self._col.decks.all_names_and_ids()]

    def deck_refs(self) -> list[dict[str, Any]]:
        items = list(self._col.decks.all_names_and_ids())
        ids_by_name = {item.name: int(item.id) for item in items}
        refs = []
        for item in items:
            parent_name = item.name.rpartition("::")[0] or None
            refs.append(
                {
                    "deckId": int(item.id),
                    "name": item.name,
                    "parentId": ids_by_name.get(parent_name) if parent_name else None,
                    "descendantCount": len(self._col.decks.children(int(item.id))),
                }
            )
        return sorted(refs, key=lambda ref: ref["name"].casefold())

    def current_deck_id(self) -> int | None:
        selected = self._col.decks.selected()
        return int(selected) if selected is not None else None

    def resolve_deck(self, deck_id: int) -> str | None:
        return self._col.decks.name_if_exists(int(deck_id)) or None

    def find_cards(self, query: str) -> list[int]:
        return list(self._col.find_cards(query))

    def card_infos(self, card_ids: list[int]) -> dict[int, dict[str, Any]]:
        infos: dict[int, dict[str, Any]] = {}
        for part in chunks(list(card_ids), _CARD_BATCH):
            for card_id in part:
                card = self._col.get_card(int(card_id))
                note = card.note()
                fields = {
                    name: {"value": value, "order": index}
                    for index, (name, value) in enumerate(note.items())
                }
                infos[int(card.id)] = {
                    "cardId": card.id,
                    "noteId": card.nid,
                    "deckId": card.did,
                    "fields": fields,
                    "deckName": self._col.decks.name_if_exists(card.did) or "",
                    "noteType": str(note.note_type().get("name") or ""),
                    "tags": list(note.tags),
                    "suspended": int(card.queue) == -1,
                }
        return infos

    def card_detail(self, card_id: int) -> dict[str, Any]:
        card = self._col.get_card(int(card_id))
        note = card.note()
        card_ids = sorted(int(cid) for cid in note.card_ids())
        return {
            "cardId": int(card.id),
            "noteId": int(card.nid),
            "deckId": int(card.did),
            "deckName": self._col.decks.name_if_exists(card.did) or "",
            "noteType": str(note.note_type().get("name") or ""),
            "fields": [
                {"name": name, "value": value}
                for name, value in note.items()
            ],
            "questionHtml": card.question(),
            "answerHtml": card.answer(),
            "cardCss": str(note.note_type().get("css") or ""),
            "tags": list(note.tags),
            "suspended": int(card.queue) == -1,
            "noteCardCount": len(card_ids),
            "noteCardIds": card_ids,
        }

    def reviews_of_cards(self, card_ids: list[int]) -> dict[int, list[dict[str, Any]]]:
        result: dict[int, list[dict[str, Any]]] = {int(cid): [] for cid in card_ids}
        if not card_ids:
            return result
        for part in chunks(list(card_ids), _REVIEW_BATCH):
            placeholders = ",".join("?" for _ in part)
            rows = self._col.db.all(
                f"select id, cid, ease, time, type from revlog where cid in ({placeholders})",
                *part,
            )
            for review_id, card_id, ease, review_time, review_type in rows:
                result[int(card_id)].append(
                    {
                        "id": review_id,
                        "cardId": card_id,
                        "ease": ease,
                        "time": review_time,
                        "type": review_type,
                    }
                )
        return result

    def simulate_fsrs(
        self, deck: str, additional_new: int, days: int, new_limit: int
    ) -> dict[str, Any]:
        deck_id = self._col.decks.id_for_name(deck)
        if deck_id is None:
            raise ValueError(f"牌组不存在：{deck}")
        config = self._col.decks.config_dict_for_deck_id(deck_id)
        return _fsrs.simulate(self._col, config, deck, additional_new, days, new_limit)

    def easy_days_percentages(self, deck: str) -> list[float]:
        """Per-weekday review-load percentages for a deck (Anki native FSRS
        Easy Days). Rest days are 0.0; absent -> full load every day.

        This is the single shared source of truth both FSRS Helper and the
        FSRS simulator read, so the budget side can use it too without any
        coupling to FSRS Helper's private config.
        """
        deck_id = self._col.decks.id_for_name(deck)
        if deck_id is None:
            return [1.0] * 7
        config = self._col.decks.config_dict_for_deck_id(deck_id)
        return list(config.get("easyDaysPercentages") or [1.0] * 7)

    def config(self) -> dict[str, Any]:
        available = (
            {item.name for item in self._col.decks.all_names_and_ids()}
            if self._col is not None
            else set()
        )
        configured = self._addon_config.get("managedDecks") or []
        return {
            "managedDecks": [name for name in configured if name in available],
            "simulationDays": int(self._addon_config.get("simulationDays", 90)),
            "dailyBudgetMinutes": int(self._addon_config.get("dailyBudgetMinutes", 30)),
        }
