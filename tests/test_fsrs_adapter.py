from __future__ import annotations

import importlib.util
import unittest
from unittest.mock import Mock, patch
from types import SimpleNamespace
from pathlib import Path
import sys


MODULE_PATH = Path(__file__).parents[1] / "addon" / "fsrs_adapter.py"
SPEC = importlib.util.spec_from_file_location("fsrs_adapter", MODULE_PATH)
fsrs = importlib.util.module_from_spec(SPEC)
assert SPEC and SPEC.loader
sys.modules[SPEC.name] = fsrs
SPEC.loader.exec_module(fsrs)


def deck_config(**overrides) -> dict:
    base = {
        "fsrsParams6": [1.0, 2.0, 3.0],
        "desiredRetention": 0.9,
        "rev": {"perDay": 200, "maxIvl": 36500},
        "new": {"delays": [1, 10]},
        "lapse": {"delays": [10], "leechAction": 1, "leechFails": 8},
        "easyDaysPercentages": [1.0] * 7,
        "reviewOrder": 0,
        "sm2Retention": 0.9,
    }
    base.update(overrides)
    return base


class FsrsParamsTest(unittest.TestCase):
    def test_prefers_fsrs_params_6(self) -> None:
        self.assertEqual(
            fsrs.fsrs_params(deck_config(fsrsParams6=[6, 6], fsrsParams5=[5])), [6, 6]
        )

    def test_falls_back_through_versions(self) -> None:
        self.assertEqual(fsrs.fsrs_params(deck_config(fsrsParams6=None, fsrsParams5=[5])), [5])
        self.assertEqual(
            fsrs.fsrs_params(deck_config(fsrsParams6=None, fsrsParams5=None, fsrsWeights=[4])), [4]
        )

    def test_empty_or_missing_weights_use_backend_defaults(self) -> None:
        for config in ({}, deck_config(fsrsParams6=[], fsrsParams5=[], fsrsWeights=[])):
            with self.subTest(config=config):
                params = fsrs.build_request_params(config, "系统默认", 0, 90, 0)
                self.assertEqual(params["params"], [])


class SimulateFailureTest(unittest.TestCase):
    def run_failure(self, message, additional_new=0, new_limit=0):
        collection = SimpleNamespace(_backend=SimpleNamespace(
            simulate_fsrs_review=Mock(side_effect=Exception(message))
        ))
        with patch.object(fsrs, "_new_request", side_effect=lambda params: params):
            return fsrs.simulate(collection, {}, "Empty", additional_new, 7, new_limit)

    def test_empty_baseline_is_zero_workload(self):
        self.assertEqual(self.run_failure("no cards to simulate"), {
            "dailyReviewCount": [0] * 7,
            "dailyNewCount": [0] * 7,
            "dailyTimeCost": [0.0] * 7,
        })

    def test_backend_errors_allow_dashboard_fallback(self):
        with self.assertRaisesRegex(RuntimeError, "Empty.*invalid parameters"):
            self.run_failure("invalid parameters")

    def test_empty_error_with_new_cards_does_not_invent_free_capacity(self):
        with self.assertRaisesRegex(RuntimeError, "no cards to simulate"):
            self.run_failure("no cards to simulate", additional_new=7, new_limit=1)


class BuildRequestParamsTest(unittest.TestCase):
    def test_returns_all_fields_with_defaults_applied(self) -> None:
        params = fsrs.build_request_params(
            deck_config(),
            deck="MyDeck",
            additional_new=50,
            days=90,
            new_limit=7,
        )
        self.assertEqual(params["params"], [1.0, 2.0, 3.0])
        self.assertEqual(params["desired_retention"], 0.9)
        self.assertEqual(params["deck_size"], 50)
        self.assertEqual(params["days_to_simulate"], 90)
        self.assertEqual(params["new_limit"], 7)
        self.assertEqual(params["review_limit"], 200)
        self.assertEqual(params["max_interval"], 36500)
        self.assertEqual(params["search"], 'deck:"MyDeck" -is:suspended -is:new')
        self.assertFalse(params["new_cards_ignore_review_limit"])
        self.assertEqual(params["easy_days_percentages"], [1.0] * 7)
        self.assertEqual(params["review_order"], 0)
        self.assertEqual(params["historical_retention"], 0.9)
        self.assertEqual(params["learning_step_count"], 2)
        self.assertEqual(params["relearning_step_count"], 1)
        self.assertNotIn("suspend_after_lapse_count", params)

    def test_clamps_non_positive_inputs(self) -> None:
        params = fsrs.build_request_params(
            deck_config(),
            deck="D",
            additional_new=-5,
            days=0,
            new_limit=-3,
        )
        self.assertEqual(params["deck_size"], 0)
        self.assertEqual(params["days_to_simulate"], 1)
        self.assertEqual(params["new_limit"], 0)

    def test_includes_suspend_count_when_leech_action_is_suspend(self) -> None:
        params = fsrs.build_request_params(
            deck_config(lapse={"delays": [10], "leechAction": 0, "leechFails": 6}),
            deck="D",
            additional_new=0,
            days=30,
            new_limit=0,
        )
        self.assertEqual(params["suspend_after_lapse_count"], 6)

    def test_uses_default_easy_days_when_missing(self) -> None:
        params = fsrs.build_request_params(
            deck_config(easyDaysPercentages=None),
            deck="D",
            additional_new=0,
            days=30,
            new_limit=0,
        )
        self.assertEqual(params["easy_days_percentages"], [1.0] * 7)

    def test_search_field_escapes_quotes_and_backslashes(self) -> None:
        # Deck names with " or \ must be escaped, same as the query builders
        # in attention_dashboard._escape_deck; otherwise FSRS gets a broken search.
        params = fsrs.build_request_params(
            deck_config(),
            deck='My "Deck" \\ X',
            additional_new=0,
            days=30,
            new_limit=0,
        )
        self.assertEqual(
            params["search"],
            'deck:"My \\"Deck\\" \\\\ X" -is:suspended -is:new',
        )


if __name__ == "__main__":
    unittest.main()
