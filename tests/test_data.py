from __future__ import annotations

import importlib.util
import unittest
from unittest.mock import Mock, patch
from types import SimpleNamespace
from pathlib import Path
import sys


def _load(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    assert spec and spec.loader
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


ADDON = Path(__file__).parents[1] / "addon"
_fsrs = _load("fsrs_adapter", ADDON / "fsrs_adapter.py")
data_mod = _load("data", ADDON / "data.py")


class FakeDeck:
    def __init__(self, name: str, deck_id: int) -> None:
        self.name = name
        self.id = deck_id


class FakeNote:
    def __init__(self, fields: dict[str, str], tags: list[str]) -> None:
        self._fields = fields
        self.tags = list(tags)

    def items(self):
        return list(self._fields.items())

    def note_type(self):
        return {"name": "Basic", "css": ".card { color: red; }"}


class FakeCard:
    def __init__(self, card_id: int, did: int, note: FakeNote, *, note_id: int = 1) -> None:
        self.id = card_id
        self.did = did
        self.nid = note_id
        self.queue = 0
        self._note = note

    def note(self) -> FakeNote:
        return self._note


class FakeDecks:
    def __init__(self, names, config_for_deck=None) -> None:
        self._names = names
        self._config = config_for_deck or {}

    def all_names_and_ids(self):
        return [FakeDeck(n, self.id_for_name(n)) for n in self._names]

    def id_for_name(self, deck: str):
        if deck in self._names:
            return abs(hash(deck)) % 1000
        return None

    def config_dict_for_deck_id(self, deck_id):
        return self._config.get(deck_id, {})

    def name_if_exists(self, did: int) -> str:
        return self._config_name_by_did.get(did, "")

    def selected(self):
        return self.id_for_name(self._names[0])

    def children(self, did):
        name = self.name_if_exists(did)
        return [(n, self.id_for_name(n)) for n in self._names if n.startswith(name + "::")]


class FakeBackend:
    def simulate_fsrs_review(self, request):
        raise AssertionError("unexpected backend call")


class FakeDB:
    def __init__(self, rows_by_query: dict) -> None:
        self._rows = rows_by_query
        self.queries: list[str] = []

    def all(self, sql: str, *params):
        self.queries.append(sql)
        return self._rows.get(tuple(params), [])


class FakeCollection:
    def __init__(self, decks, cards, db_rows=None, fsrs_config=None) -> None:
        self.decks = decks
        self._cards = cards
        self.db = FakeDB(db_rows or {})
        self._fsrs_config = fsrs_config or {}
        self._backend = FakeBackend()

    def find_cards(self, query: str):
        return [c for c in self._cards if query == "any"]

    def get_card(self, card_id: int) -> FakeCard:
        return self._cards[card_id]


def make_data(
    deck_names=("A", "B"),
    cards=None,
    revlog_rows=None,
    addon_config=None,
    fsrs_config=None,
):
    cards = cards or {}
    decks = FakeDecks(list(deck_names), config_for_deck=fsrs_config or {})
    decks._config_name_by_did = {decks.id_for_name(n): n for n in deck_names}
    col = FakeCollection(decks, cards, db_rows=revlog_rows, fsrs_config=fsrs_config)
    return data_mod.AnkiData(col, dict(addon_config or {}))


class AnkiDataConfigTest(unittest.TestCase):
    def test_deck_refs_use_ids_and_parent_relationships(self) -> None:
        data = make_data(deck_names=("A", "A::B", "C"))
        refs = data.deck_refs()
        by_name = {ref["name"]: ref for ref in refs}
        self.assertEqual(by_name["A"]["parentId"], None)
        self.assertEqual(by_name["A"]["descendantCount"], 1)
        self.assertEqual(by_name["A::B"]["parentId"], by_name["A"]["deckId"])
        self.assertEqual(data.current_deck_id(), by_name["A"]["deckId"])
        self.assertEqual(data.resolve_deck(by_name["A::B"]["deckId"]), "A::B")
        self.assertIsNone(data.resolve_deck(999999))

    def test_config_filters_managed_decks_to_available(self) -> None:
        data = make_data(deck_names=("A", "B"), addon_config={"managedDecks": ["A", "Gone"]})
        self.assertEqual(data.config()["managedDecks"], ["A"])

    def test_config_applies_defaults(self) -> None:
        data = make_data(addon_config={})
        cfg = data.config()
        self.assertEqual(cfg["simulationDays"], 90)
        self.assertEqual(cfg["dailyBudgetMinutes"], 30)
        self.assertEqual(cfg["managedDecks"], [])


class AnkiDataCardInfosTest(unittest.TestCase):
    def test_builds_field_map_and_deck_tags(self) -> None:
        note = FakeNote({"Front": "Q", "Back": "A"}, ["tag1"])
        card = FakeCard(100, did=10, note=note)
        data = make_data(deck_names=("A",), cards={100: card})

        infos = data.card_infos([100])

        self.assertEqual(list(infos), [100])
        info = infos[100]
        self.assertEqual(info["cardId"], 100)
        self.assertEqual(
            info["fields"],
            {"Front": {"value": "Q", "order": 0}, "Back": {"value": "A", "order": 1}},
        )
        self.assertEqual(info["tags"], ["tag1"])
        self.assertEqual(info["noteId"], 1)
        self.assertEqual(info["deckId"], 10)
        self.assertEqual(info["noteType"], "Basic")
        self.assertFalse(info["suspended"])

    def test_returns_empty_for_empty_input(self) -> None:
        data = make_data(cards={})
        self.assertEqual(data.card_infos([]), {})


class AnkiDataReviewsTest(unittest.TestCase):
    def test_maps_revlog_rows_by_card_id(self) -> None:
        rows = {
            (100, 200): [
                (11, 100, 3, 5000, 1),
                (12, 100, 1, 8000, 2),
                (13, 200, 2, 3000, 3),
            ],
        }
        data = make_data(revlog_rows=rows)

        reviews = data.reviews_of_cards([100, 200])

        self.assertEqual(
            reviews[100],
            [
                {"id": 11, "cardId": 100, "ease": 3, "time": 5000, "type": 1},
                {"id": 12, "cardId": 100, "ease": 1, "time": 8000, "type": 2},
            ],
        )
        self.assertEqual(reviews[200], [{"id": 13, "cardId": 200, "ease": 2, "time": 3000, "type": 3}])

    def test_empty_input_returns_empty_without_query(self) -> None:
        data = make_data()
        self.assertEqual(data.reviews_of_cards([]), {})
        self.assertEqual(data._col.db.queries, [])


class AnkiDataSimulateTest(unittest.TestCase):
    def test_raises_value_error_for_unknown_deck(self) -> None:
        data = make_data(deck_names=("A",), fsrs_config={})
        with self.assertRaises(ValueError) as ctx:
            data.simulate_fsrs("不存在", additional_new=0, days=30, new_limit=0)
        self.assertIn("牌组不存在", str(ctx.exception))

    def test_missing_params_reach_backend_as_defaults(self) -> None:
        data = make_data(deck_names=("A",))
        runner = Mock(return_value=SimpleNamespace(
            daily_review_count=[2], daily_new_count=[0], daily_time_cost=[15.0]
        ))
        data._col._backend.simulate_fsrs_review = runner
        # Replace only protobuf construction; exercise data -> adapter -> backend.
        with patch.object(_fsrs, "_new_request", side_effect=lambda params: params):
            result = data.simulate_fsrs("A", additional_new=0, days=30, new_limit=0)
        self.assertEqual(runner.call_args.args[0]["params"], [])
        self.assertEqual(result, {
            "dailyReviewCount": [2], "dailyNewCount": [0], "dailyTimeCost": [15.0]
        })

    def test_degrades_to_runtime_error_when_protobuf_unavailable(self) -> None:
        # FSRS params present + backend exposes the method, but the generated
        # protobuf module is not importable (older/incompatible Anki) -> the
        # adapter converts the ImportError into an explicit degradation.
        decks = FakeDecks(["A"])
        deck_id = decks.id_for_name("A")
        decks._config = {deck_id: {"fsrsParams6": [1.0, 2.0], "rev": {}, "new": {}, "lapse": {}}}
        decks._config_name_by_did = {deck_id: "A"}
        col = FakeCollection(decks, {}, {}, {})
        data = data_mod.AnkiData(col, {"managedDecks": ["A"]})
        with self.assertRaises(RuntimeError) as ctx:
            data.simulate_fsrs("A", additional_new=0, days=30, new_limit=0)
        self.assertIn("降级", str(ctx.exception))


if __name__ == "__main__":
    unittest.main()
