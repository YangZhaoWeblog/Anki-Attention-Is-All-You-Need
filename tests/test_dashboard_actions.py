from __future__ import annotations

import importlib.util
from pathlib import Path
import sys
import unittest


MODULE_PATH = Path(__file__).parents[1] / "addon" / "dashboard_actions.py"
SPEC = importlib.util.spec_from_file_location("dashboard_actions", MODULE_PATH)
actions = importlib.util.module_from_spec(SPEC)
assert SPEC and SPEC.loader
sys.modules[SPEC.name] = actions
SPEC.loader.exec_module(actions)


class FakeNote:
    def __init__(self, note_id, fields, tags, card_ids):
        self.id = note_id
        self._fields = dict(fields)
        self.tags = list(tags)
        self._card_ids = list(card_ids)

    def keys(self):
        return self._fields.keys()

    def __getitem__(self, key):
        return self._fields[key]

    def __setitem__(self, key, value):
        self._fields[key] = value

    def card_ids(self):
        return list(self._card_ids)


class FakeCard:
    def __init__(self, card_id, note_id, suspended=False):
        self.id = card_id
        self.nid = note_id
        self.queue = -1 if suspended else 2


class FakeSched:
    def __init__(self, cards):
        self.cards = cards

    def suspend_cards(self, card_ids):
        for card_id in card_ids:
            self.cards[card_id].queue = -1

    def unsuspend_cards(self, card_ids):
        for card_id in card_ids:
            self.cards[card_id].queue = 2


class FakeCollection:
    def __init__(self):
        self.notes = {10: FakeNote(10, {"Front": "<b>Q</b>", "Back": '<img src="x.png">A'}, ["keep"], [1, 2])}
        self.cards = {1: FakeCard(1, 10), 2: FakeCard(2, 10)}
        self.sched = FakeSched(self.cards)
        self.updated = []
        self.removed = []
        self.undo_entries = []

    def get_note(self, note_id):
        return self.notes[note_id]

    def get_card(self, card_id):
        return self.cards[card_id]

    def update_note(self, note):
        self.updated.append(note.id)

    def remove_notes(self, note_ids):
        self.removed.extend(note_ids)

    def add_custom_undo_entry(self, label):
        self.undo_entries.append(label)
        return 42

    def merge_undo_entries(self, target):
        self.undo_entries.append(target)
        return "merged-changes"


class DashboardActionsTest(unittest.TestCase):
    def setUp(self):
        self.col = FakeCollection()

    def test_save_note_changes_only_submitted_fields_and_preserves_rich_text(self):
        result = actions.save_note(self.col, 10, {"Front": "<i>new</i>"}, ["keep", "new"])
        note = self.col.notes[10]
        self.assertEqual(note["Front"], "<i>new</i>")
        self.assertEqual(note["Back"], '<img src="x.png">A')
        self.assertEqual(note.tags, ["keep", "new"])
        self.assertEqual(result["cardIds"], [1, 2])

    def test_optimization_marks_note_and_suspends_only_current_card(self):
        result = actions.set_optimization(self.col, 1, True)
        self.assertIn("需优化卡片", self.col.notes[10].tags)
        self.assertTrue(result["suspended"])
        self.assertEqual(self.col.cards[1].queue, -1)
        self.assertNotEqual(self.col.cards[2].queue, -1)
        self.assertEqual(result.changes, "merged-changes")
        actions.set_optimization(self.col, 1, False)
        self.assertNotIn("需优化卡片", self.col.notes[10].tags)
        self.assertEqual(self.col.cards[1].queue, -1)

    def test_hidden_changes_whole_note_without_suspending(self):
        result = actions.set_hidden(self.col, 1, True)
        self.assertIn("看板忽略", self.col.notes[10].tags)
        self.assertEqual(result["cardIds"], [1, 2])
        self.assertNotEqual(self.col.cards[1].queue, -1)

    def test_delete_requires_exact_confirmed_scope(self):
        prepared = actions.prepare_delete(self.col, 1)
        self.assertEqual(prepared, {"noteId": 10, "cardIds": [1, 2], "cardCount": 2})
        with self.assertRaisesRegex(ValueError, "影响范围已变化"):
            actions.delete_note(self.col, 10, [1])
        actions.delete_note(self.col, 10, [2, 1])
        self.assertEqual(self.col.removed, [10])


if __name__ == "__main__":
    unittest.main()
