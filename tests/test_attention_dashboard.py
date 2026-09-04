from __future__ import annotations

import importlib.util
from pathlib import Path
import sys
import unittest
from unittest.mock import patch


MODULE_PATH = Path(__file__).parents[1] / "addon" / "attention_dashboard.py"
SPEC = importlib.util.spec_from_file_location("attention_dashboard", MODULE_PATH)
dashboard = importlib.util.module_from_spec(SPEC)
assert SPEC and SPEC.loader
sys.modules[SPEC.name] = dashboard
SPEC.loader.exec_module(dashboard)


class AttentionDashboardTest(unittest.TestCase):
    def test_detect_quality_flags_structural_risks(self) -> None:
        problems = dashboard.detect_quality(
            "请分别回答：为什么？如何处理？",
            "1. 第一项\n2. 第二项\n3. 第三项\n4. 第四项\n5. 第五项\n6. 第六项",
            False,
        )

        self.assertIn("疑似列表卡", problems)
        self.assertIn("多问合一", problems)
        self.assertIn("答案多行", problems)

    def test_daily_budget_checks_every_rolling_week(self) -> None:
        self.assertTrue(dashboard.within_daily_budget([20 * 60] * 14, 30 * 60))
        self.assertFalse(dashboard.within_daily_budget([31 * 60] * 14, 30 * 60))

    def test_new_card_search_can_exceed_one_hundred(self) -> None:
        days = 14
        baseline = [{
            "dailyReviewCount": [1] * days,
            "dailyNewCount": [0] * days,
            "dailyTimeCost": [10.0] * days,
        }]

        def simulation(_decks, simulation_days, candidate):
            daily_cost = 10.0 + candidate / 100.0
            return [{
                "dailyReviewCount": [1] * simulation_days,
                "dailyNewCount": [0] * simulation_days,
                "dailyTimeCost": [daily_cost] * simulation_days,
            }]

        with patch.object(dashboard, "native_simulation", side_effect=simulation):
            result = dashboard.recommended_new_cards(
                ["ALL"],
                baseline,
                days,
                daily_budget_seconds=20,
            )

        self.assertEqual(result, 1000)


if __name__ == "__main__":
    unittest.main()
