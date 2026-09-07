from __future__ import annotations

import importlib.util
from pathlib import Path
import sys
import unittest


MODULE_PATH = Path(__file__).parents[1] / "addon" / "settings.py"
SPEC = importlib.util.spec_from_file_location("settings", MODULE_PATH)
settings = importlib.util.module_from_spec(SPEC)
assert SPEC and SPEC.loader
sys.modules[SPEC.name] = settings
SPEC.loader.exec_module(settings)


class FakeData:
    def __init__(self):
        self.refs = [
            {"deckId": 1, "name": "A", "parentId": None, "descendantCount": 1},
            {"deckId": 2, "name": "A::B", "parentId": 1, "descendantCount": 0},
        ]

    def deck_refs(self):
        return list(self.refs)

    def current_deck_id(self):
        return 2

    def resolve_deck(self, deck_id):
        return next((ref["name"] for ref in self.refs if ref["deckId"] == deck_id), None)


class SettingsTest(unittest.TestCase):
    def test_first_upgrade_uses_current_deck_and_default_budget(self):
        result = settings.initialize(FakeData(), {"managedDecks": ["ALL"], "dailyBudgetMinutes": 45}, "User 1")
        self.assertEqual(result["selectedDeckId"], 2)
        self.assertEqual(result["dailyBudgetMinutes"], 45)
        self.assertFalse(result["selectionRequired"])

    def test_saved_id_follows_rename_and_deleted_id_requires_selection(self):
        config = {"settingsByProfile": {"User 1": {"deckId": 1, "dailyBudgetMinutes": 20}}}
        data = FakeData()
        self.assertEqual(settings.initialize(data, config, "User 1")["selectedDeckId"], 1)
        data.refs[0]["name"] = "Renamed"
        self.assertFalse(settings.initialize(data, config, "User 1")["selectionRequired"])
        data.refs.pop(0)
        result = settings.initialize(data, config, "User 1")
        self.assertTrue(result["selectionRequired"])
        self.assertIsNone(result["selectedDeckId"])

    def test_save_preserves_unknown_configuration(self):
        original = {"simulationDays": 30, "unknown": {"keep": True}}
        updated, saved = settings.save(FakeData(), original, "User 1", 1, 25)
        self.assertEqual(updated["unknown"], {"keep": True})
        self.assertEqual(updated["settingsByProfile"]["User 1"], saved)
        self.assertEqual(saved, {"deckId": 1, "dailyBudgetMinutes": 25})

    def test_save_preserves_other_profiles_and_unknown_profile_fields(self):
        original = {'settingsByProfile': {'User 1': {'deckId': 2, 'dailyBudgetMinutes': 30, 'futureKey': True},
                                          'User 2': {'deckId': 2, 'dailyBudgetMinutes': 90}}}
        updated, _ = settings.save(FakeData(), original, 'User 1', 1, 25)
        self.assertTrue(updated['settingsByProfile']['User 1']['futureKey'])
        self.assertEqual(updated['settingsByProfile']['User 2'], original['settingsByProfile']['User 2'])
        self.assertEqual(original['settingsByProfile']['User 1']['deckId'], 2)


if __name__ == "__main__":
    unittest.main()
