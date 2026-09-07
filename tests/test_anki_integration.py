"""Run with Anki's bundled Python packages for an isolated collection smoke test."""

from __future__ import annotations

import tempfile
import time
from pathlib import Path
import sys
import unittest


try:
    from anki.collection import Collection
except ImportError:  # normal developer Python does not include Anki
    Collection = None

sys.path.insert(0, str(Path(__file__).parents[1] / "addon"))
import dashboard_actions
from data import AnkiData


@unittest.skipUnless(Collection, "requires Anki's Python packages")
class IsolatedAnkiCollectionTest(unittest.TestCase):
    def test_introduced_search_counts_first_answer_not_imported_cards(self):
        with tempfile.TemporaryDirectory(prefix="attention-anki-") as folder:
            col = Collection(str(Path(folder) / "collection.anki2"))
            try:
                model = col.models.by_name("Basic")
                deck_id = col.decks.id_for_name("Default")
                card_ids = []
                for index in range(100):
                    note = col.new_note(model)
                    note["Front"] = f"Q{index}"
                    note["Back"] = f"A{index}"
                    col.add_note(note, deck_id)
                    card_ids.append(int(note.card_ids()[0]))
                now = int(time.time() * 1000)
                rows = []
                for index, card_id in enumerate(card_ids[:5]):
                    rows.append((now + index, card_id, -1, 3, 1, 0, 0, 1000, 0))
                rows.append((now + 100, card_ids[0], -1, 3, 1, 0, 0, 1000, 1))
                rows.append((now - 10 * 86400 * 1000, card_ids[5], -1, 3, 1, 0, 0, 1000, 0))
                col.db.executemany(
                    "insert into revlog (id,cid,usn,ease,ivl,lastIvl,factor,time,type) values (?,?,?,?,?,?,?,?,?)",
                    rows,
                )
                self.assertEqual(len(col.find_cards("introduced:1")), 5)
                self.assertEqual(len(col.find_cards("introduced:7")), 5)
            finally:
                col.close()

    def test_rich_note_detail_and_native_writes(self):
        with tempfile.TemporaryDirectory(prefix="attention-anki-") as folder:
            col = Collection(str(Path(folder) / "collection.anki2"))
            try:
                model = col.models.by_name("Basic")
                self.assertIsNotNone(model)
                note = col.new_note(model)
                note["Front"] = '<b>Question</b><a href="obsidian://open?vault=Test">source</a>'
                note["Back"] = '<img src="missing-for-smoke.png">Answer'
                col.add_note(note, col.decks.id_for_name("Default"))
                card_id = int(note.card_ids()[0])

                data = AnkiData(col, {})
                detail = data.card_detail(card_id)
                self.assertIn("<b>Question</b>", detail["questionHtml"])
                self.assertEqual(detail["fields"][1]["value"], '<img src="missing-for-smoke.png">Answer')

                saved = dashboard_actions.save_note(col, note.id, {"Front": "<i>Changed</i>"}, ["keep"])
                self.assertEqual(saved["cardIds"], [card_id])
                self.assertEqual(col.get_note(note.id)["Back"], '<img src="missing-for-smoke.png">Answer')

                optimized = dashboard_actions.set_optimization(col, card_id, True)
                self.assertTrue(optimized["suspended"])
                self.assertIn("需优化卡片", col.get_note(note.id).tags)
                dashboard_actions.set_optimization(col, card_id, False)
                self.assertTrue(data.card_detail(card_id)["suspended"])

                dashboard_actions.set_hidden(col, card_id, True)
                self.assertIn("看板忽略", col.get_note(note.id).tags)
                prepared = dashboard_actions.prepare_delete(col, card_id)
                dashboard_actions.delete_note(col, note.id, prepared["cardIds"])
                self.assertEqual(col.card_ids_of_note(note.id), [])
            finally:
                col.close()

    def test_sibling_scope_native_undo_and_cross_deck_delete(self):
        with tempfile.TemporaryDirectory(prefix="attention-anki-") as folder:
            col = Collection(str(Path(folder) / "collection.anki2"))
            try:
                note = col.new_note(col.models.by_name("Basic (and reversed card)"))
                note['Front'], note['Back'] = 'Q', 'A'
                col.add_note(note, 1)
                cards = sorted(note.card_ids())
                col.set_deck([cards[1]], col.decks.id('Other'))
                detail = AnkiData(col, {}).card_detail(cards[0])
                self.assertEqual(detail['noteCardIds'], cards)
                result = dashboard_actions.set_optimization(col, cards[0], True)
                self.assertTrue(result.changes.note)
                self.assertTrue(result.changes.card)
                self.assertEqual(col.get_card(cards[0]).queue, -1)
                self.assertNotEqual(col.get_card(cards[1]).queue, -1)
                col.undo()
                self.assertNotIn('需优化卡片', col.get_note(note.id).tags)
                self.assertTrue(all(col.get_card(cid).queue != -1 for cid in cards))
                dashboard_actions.set_hidden(col, cards[0], True)
                self.assertEqual(sorted(col.find_cards('tag:看板忽略')), cards)
                dashboard_actions.set_hidden(col, cards[0], False)
                self.assertFalse(col.find_cards('tag:看板忽略'))
                with self.assertRaisesRegex(ValueError, '影响范围已变化'):
                    dashboard_actions.delete_note(col, note.id, [cards[0]])
                self.assertEqual(sorted(col.card_ids_of_note(note.id)), cards)
                dashboard_actions.delete_note(col, note.id, cards)
                self.assertEqual(col.card_ids_of_note(note.id), [])
            finally:
                col.close()

    def test_cloze_original_fields_survive_explicit_save(self):
        with tempfile.TemporaryDirectory(prefix="attention-anki-") as folder:
            col = Collection(str(Path(folder) / "collection.anki2"))
            try:
                note = col.new_note(col.models.by_name('Cloze'))
                note['Text'] = '<b>{{c1::answer}}</b><img src="picture.png">'
                extra = '<a href="marginnote4app://note/QA">source</a>'
                note['Back Extra'] = extra
                col.add_note(note, 1)
                dashboard_actions.save_note(col, note.id, {'Text': note['Text'] + ' edited'}, ['keep'])
                stored = col.get_note(note.id)
                self.assertIn('{{c1::answer}}', stored['Text'])
                self.assertEqual(stored['Back Extra'], extra)
            finally:
                col.close()


if __name__ == "__main__":
    unittest.main()
