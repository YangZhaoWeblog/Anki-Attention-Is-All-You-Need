from __future__ import annotations

import importlib.util
import time
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


DAY_MS = 24 * 3600 * 1000


def make_summary(card_id: int = 1, avg_ms: int = 10_000) -> dashboard.CardReview:
    return dashboard.CardReview(
        card_id=card_id,
        total_ms_90=600_000,
        total_ms_30=200_000,
        reviews_90=10,
        slow_count=1,
        again_count=0,
        avg_ms=avg_ms,
        last_times=[30, 25],
    )


def make_info(
    card_id: int = 1,
    front: str = "题干",
    back: str = "答案",
    *,
    image: bool = False,
    tags: list[str] | None = None,
    deck: str = "A",
    note_type: str = "Basic",
) -> dict:
    back_value = f'<img src="x.png">{back}' if image else back
    return {
        "cardId": card_id,
        "fields": {
            "Front": {"value": front, "order": 0},
            "Back": {"value": back_value, "order": 1},
        },
        "deckName": deck,
        "noteType": note_type,
        "tags": list(tags or []),
    }


class FakeData:
    """固定场景数据源：牌组 A；卡 1 是注意力黑洞，卡 2 题干过长，FSRS 基线超预算。

    实现 AttentionData 协议，供 build_dashboard 注入；与真实 AnkiData 同构。"""

    def __init__(self) -> None:
        now = int(time.time() * 1000)
        self.simulate_calls: list[dict] = []
        self.queries_seen: list[str] = []
        self._easy_days: dict[str, list[float]] = {}
        self._deck_names = ["A"]
        self.cards = {
            1: make_info(1, front="题干一", back="答案一", tags=["tagA"]),
            2: make_info(2, front="题" * 200, back="答"),
        }
        self.reviews = {
            1: [
                {"id": now, "ease": 3, "time": 60_000},
                {"id": now - 40 * DAY_MS, "ease": 1, "time": 30_000},
                {"id": now - 40 * DAY_MS - 1_000, "ease": 3, "time": 31_000},
            ],
            2: [{"id": now, "ease": 3, "time": 4_000}],
        }
        self.queries = {
            '-is:suspended (deck:"A")': [1, 2],
            '-is:suspended -tag:看板忽略 -tag:需优化卡片 (deck:"A")': [1, 2],
            'rated:90 -is:suspended (deck:"A")': [1, 2],
            'is:due -is:suspended (deck:"A")': list(range(10)),
            'added:1 -is:suspended (deck:"A")': [101, 102],
            'added:7 -is:suspended (deck:"A")': [101, 102, 103, 104],
            'introduced:1 (deck:"A")': [101, 102],
            'introduced:7 (deck:"A")': [101, 102, 103, 104],
            'is:suspended (deck:"A")': [999],
            'tag:需优化卡片 (deck:"A")': [],
            'is:due -is:new -is:suspended (deck:"A")': list(range(10)),
            'is:review prop:due=1 -is:suspended (deck:"A")': list(range(5)),
        }
        self._config = {
            "managedDecks": ["A"],
            "simulationDays": 90,
            "dailyBudgetMinutes": 30,
        }

    def deck_names(self) -> list:
        return list(self._deck_names)

    def find_cards(self, query: str) -> list:
        self.queries_seen.append(query)
        return list(self.queries.get(query, []))

    def card_infos(self, card_ids) -> dict:
        return {
            int(cid): self.cards[int(cid)]
            for cid in card_ids
            if int(cid) in self.cards
        }

    def reviews_of_cards(self, card_ids) -> dict:
        return {int(cid): self.reviews.get(int(cid), []) for cid in card_ids}

    def simulate_fsrs(self, deck, additional_new, days, new_limit) -> dict:
        self.simulate_calls.append(
            {
                "deck": deck,
                "additionalNew": additional_new,
                "days": days,
                "newLimit": new_limit,
            }
        )
        return {
            "dailyReviewCount": [5] * days,
            "dailyNewCount": [1] * days,
            "dailyTimeCost": [100_000.0] * days,
        }

    def config(self) -> dict:
        return dict(self._config)

    def easy_days_percentages(self, deck: str) -> list:
        return list(self._easy_days.get(deck, [1.0] * 7))


class AttentionDashboardTest(unittest.TestCase):
    """纯函数行为特征测试。"""

    def test_detect_quality_flags_structural_risks(self) -> None:
        problems = dashboard.detect_quality(
            "请分别回答：为什么？如何处理？",
            "1. 第一项\n2. 第二项\n3. 第三项\n4. 第四项\n5. 第五项\n6. 第六项",
            False,
        )

        self.assertIn("疑似列表卡", problems)
        self.assertIn("多问合一", problems)
        self.assertIn("答案多行", problems)

    def test_standard_cloze_and_custom_fields_remain_identifiable(self):
        for fields in ({'Text': {'value': '长题面' * 80}, 'Back Extra': {'value': '补充'}},
                       {'自定义问题': {'value': '长题面' * 80, 'order': 0}, '解释': {'value': '补充', 'order': 1}}):
            row = dashboard.make_card_row(dashboard.empty_review(1), {'fields': fields})
            self.assertTrue(row['summary'].startswith('长题面'))
            self.assertIn('题干过长', row['problems'])

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

        def simulation(_decks, simulation_days, candidate, _data):
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
                data=None,
            )

        self.assertEqual(result, 1000)

    def test_summarize_reviews_drops_invalid_and_old_entries(self) -> None:
        now = int(time.time() * 1000)
        reviews = [
            {"id": now, "ease": 0, "time": 5_000},
            {"id": now, "ease": 3, "time": 0},
            {"id": now - 91 * DAY_MS, "ease": 3, "time": 9_999_999},
            {"id": now - 10 * DAY_MS, "ease": 3, "time": 70_000},
            {"id": now - 40 * DAY_MS, "ease": 1, "time": 20_000},
        ]
        summary = dashboard.summarize_reviews(7, reviews, now)
        assert summary is not None
        self.assertEqual(summary.reviews_90, 2)
        self.assertEqual(summary.total_ms_90, 90_000)
        self.assertEqual(summary.total_ms_30, 70_000)
        self.assertEqual(summary.slow_count, 2)
        self.assertEqual(summary.again_count, 1)
        self.assertEqual(summary.avg_ms, 3_363_333)
        self.assertEqual(summary.last_times, [70, 20, 10000])

    def test_recent_five_crosses_ninety_day_boundary(self) -> None:
        now = int(time.time() * 1000)
        reviews = [
            {"id": now - days * DAY_MS, "ease": 1 if days in (100, 120) else 3, "time": 65_000}
            for days in (5, 40, 80, 100, 120, 150)
        ]
        summary = dashboard.summarize_reviews(7, reviews, now)
        assert summary is not None
        self.assertEqual(summary.reviews_90, 3)
        self.assertEqual(summary.total_ms_30, 65_000)
        self.assertEqual(summary.slow_count, 5)
        self.assertEqual(summary.again_count, 2)
        self.assertEqual(len(summary.recent_reviews), 5)

    def test_build_dashboard_uses_candidate_and_introduced_queries(self) -> None:
        fake = FakeData()
        fake.cards[3] = make_info(3, front="无历史但题干" * 40)
        fake.cards[4] = make_info(4, front="两次四十秒")
        now = int(time.time() * 1000)
        fake.reviews[4] = [
            {"id": now, "ease": 3, "time": 40_000},
            {"id": now - DAY_MS, "ease": 3, "time": 41_000},
        ]
        fake.queries['-is:suspended -tag:看板忽略 -tag:需优化卡片 (deck:"A")'] = [1, 2, 3, 4]
        result = dashboard.build_dashboard(data=fake)
        review_ids = [row["card_id"] for row in result["review_queue"]]
        self.assertIn(4, review_ids)
        self.assertNotIn(3, review_ids)
        self.assertEqual(result["balance"]["new_today"], 2)
        self.assertEqual(result["balance"]["new_7"], 4)
        self.assertIn('introduced:1 (deck:"A")', fake.queries_seen)
        self.assertNotIn('added:1 -is:suspended (deck:"A")', fake.queries_seen)

    def test_summarize_reviews_returns_none_without_valid_entries(self) -> None:
        now = int(time.time() * 1000)
        self.assertIsNone(
            dashboard.summarize_reviews(7, [{"id": now, "ease": 0, "time": 5_000}], now)
        )

    def test_make_card_row_recommends_action_by_rules(self) -> None:
        stage = dashboard.make_card_row(make_summary(), make_info(front="面试要点"))
        self.assertEqual(stage["action"], "阶段卡：考虑暂停并打标签")

        bad = dashboard.make_card_row(make_summary(), make_info(front="题" * 200))
        self.assertEqual(bad["action"], "疑似卡差：考虑拆小")
        self.assertEqual(bad["problems"], ["题干过长"])

        hard = dashboard.make_card_row(make_summary(avg_ms=70_000), make_info())
        self.assertEqual(hard["action"], "有价值但难：先观察")

        normal = dashboard.make_card_row(make_summary(), make_info())
        self.assertEqual(normal["action"], "审计")

    def test_make_card_row_marks_image_cards_without_text(self) -> None:
        row = dashboard.make_card_row(make_summary(), make_info(front="", image=True))
        self.assertEqual(row["summary"], "Basic · A · Front / Back · 卡片 1")
        self.assertEqual(row["front"], "")
        self.assertEqual(row["previewKind"], "image")
        self.assertEqual(row["thumbnail"], "x.png")
        self.assertNotIn("图片卡：不可按空题干判断", row["problems"])
        self.assertIn("图片无法分析", row["limitations"])

    def test_make_card_row_reads_common_image_src_forms(self) -> None:
        for html_value in ('<img src=x.png>', '<img src = "x.png">', "<img alt='x' src='x.png'>"):
            with self.subTest(html=html_value):
                row = dashboard.make_card_row(
                    make_summary(),
                    {
                        "cardId": 1,
                        "fields": {
                            "Front": {"value": "", "order": 0},
                            "Back": {"value": html_value, "order": 1},
                        },
                        "deckName": "A",
                        "noteType": "Basic",
                        "tags": [],
                    },
                )
                self.assertEqual(row["previewKind"], "image")
                self.assertEqual(row["thumbnail"], "x.png")

    def test_readable_text_without_image_uses_text_preview(self) -> None:
        row = dashboard.make_card_row(make_summary(), make_info(front="一段可读题面", image=False))
        self.assertEqual(row["summary"], "一段可读题面")
        self.assertEqual(row["previewKind"], "text")
        self.assertEqual(row["previewText"], "一段可读题面")
        self.assertEqual(row["thumbnail"], "")

    def test_empty_standard_front_falls_back_to_first_readable_field(self) -> None:
        row = dashboard.make_card_row(
            make_summary(),
            {
                "cardId": 1,
                "fields": {
                    "Front": {"value": "", "order": 0},
                    "Prompt": {"value": "真实可读题面", "order": 1},
                    "Back": {"value": "答案", "order": 2},
                },
                "deckName": "A",
                "noteType": "Basic",
                "tags": [],
            },
        )
        self.assertEqual(row["summary"], "真实可读题面")
        self.assertEqual(row["front"], "真实可读题面")
        self.assertEqual(row["previewKind"], "text")
        self.assertEqual(row["previewText"], "真实可读题面")
        self.assertEqual(row["titleFallback"], "Basic · A · Front / Prompt / Back · 卡片 1")

    def test_unreadable_card_uses_metadata_title_without_quality_reason(self) -> None:
        row = dashboard.make_card_row(
            dashboard.empty_review(9),
            make_info(card_id=9, front="", back="", deck="Kafka", note_type="Cloze"),
        )
        self.assertEqual(row["summary"], "Cloze · Kafka · Front / Back · 卡片 9")
        self.assertEqual(row["front"], "")
        self.assertEqual(row["previewKind"], "fallback")
        self.assertEqual(row["previewText"], "")
        self.assertEqual(row["reasons"], [])
        self.assertEqual(row["problems"], [])

    def test_recent_30_minutes_does_not_create_candidate_reason(self) -> None:
        review = dashboard.CardReview(1, 300_000, 300_000, 5, 1, 0, 35_000, [35, 35])
        row = dashboard.make_card_row(review, make_info())
        self.assertEqual(row["recent30Minutes"], 5.0)
        self.assertEqual(row["reasons"], [])

    def test_structural_problem_needs_performance_evidence_to_be_candidate(self) -> None:
        no_evidence = dashboard.make_card_row(dashboard.empty_review(1), make_info(front="题" * 200))
        self.assertEqual(no_evidence["problems"], ["题干过长"])
        self.assertEqual(no_evidence["reasons"], [])

        one_obvious_slow = dashboard.CardReview(1, 65_000, 65_000, 1, 1, 0, 65_000, [65], [
            {"durationMs": 65_000, "rating": 3, "reviewedAt": 1}
        ])
        with_evidence = dashboard.make_card_row(one_obvious_slow, make_info(front="题" * 200))
        self.assertIn("结构疑点", with_evidence["reasons"])

    def test_again_candidate_requires_two_days_or_three_recent_again(self) -> None:
        now = int(time.time() * 1000)
        two_same_day = dashboard.summarize_reviews(1, [
            {"id": now, "ease": 1, "time": 10_000},
            {"id": now - 1_000, "ease": 1, "time": 10_000},
        ], now)
        assert two_same_day is not None
        self.assertEqual(dashboard.make_card_row(two_same_day, make_info())["reasons"], [])

        two_days = dashboard.summarize_reviews(1, [
            {"id": now, "ease": 1, "time": 10_000},
            {"id": now - DAY_MS, "ease": 1, "time": 10_000},
        ], now)
        assert two_days is not None
        self.assertIn("跨 2 个复习日", dashboard.make_card_row(two_days, make_info())["reasons"][0])

        three_same_day = dashboard.summarize_reviews(1, [
            {"id": now, "ease": 1, "time": 10_000},
            {"id": now - 1_000, "ease": 1, "time": 10_000},
            {"id": now - 2_000, "ease": 1, "time": 10_000},
        ], now)
        assert three_same_day is not None
        self.assertIn("3 次 Again", dashboard.make_card_row(three_same_day, make_info())["reasons"][0])

    def test_image_only_card_without_review_evidence_is_not_a_candidate(self) -> None:
        row = dashboard.make_card_row(
            dashboard.empty_review(9),
            make_info(card_id=9, front="", back="", image=True),
        )
        self.assertEqual(row["reasons"], [])
        self.assertEqual(row["problems"], [])
        self.assertEqual(row["limitations"], ["图片无法分析"])

    def test_make_card_row_handles_missing_card_info(self) -> None:
        row = dashboard.make_card_row(make_summary(), None)
        self.assertEqual(row["front"], "")
        self.assertEqual(row["deck"], "")

    def test_html_cover_contract_removes_old_unavailable_preview(self) -> None:
        html = (Path(__file__).parents[1] / "addon" / "attention_dashboard.html").read_text()
        self.assertIn("function coverArt", html)
        self.assertIn("fallback-cover", html)
        self.assertIn("text-preview", html)
        self.assertIn("function detailFallbackTitle", html)
        self.assertIn("detail-fallback-title", html)
        self.assertNotIn("tile-symbol", html)
        self.assertNotIn("预览不可用", html)

    def test_query_builders_scope_and_escape_deck_names(self) -> None:
        self.assertEqual(dashboard.scoped_query("is:due", "A"), 'is:due deck:"A"')
        self.assertEqual(
            dashboard.scoped_query("is:due", 'My "Deck"'),
            'is:due deck:"My \\"Deck\\""',
        )
        self.assertEqual(
            dashboard.managed_query("is:due", ["A", "B"]),
            'is:due (deck:"A" or deck:"B")',
        )
        self.assertEqual(dashboard.managed_query("is:due", []), "is:due")
        self.assertEqual(dashboard.view_query("is:due", "A", ["B"]), 'is:due deck:"A"')
        self.assertEqual(dashboard.view_query("is:due", "", ["B"]), 'is:due (deck:"B")')
        self.assertEqual(dashboard.view_query("is:due", "", []), "is:due")

    def test_combined_simulation_merges_decks_and_truncates_days(self) -> None:
        simulations = [
            {
                "dailyReviewCount": [3, 3],
                "dailyNewCount": [1, 1],
                "dailyTimeCost": [10.0, 20.0],
            },
            {
                "dailyReviewCount": [2],
                "dailyNewCount": [0],
                "dailyTimeCost": [5.0],
            },
        ]

        counts, costs = dashboard.combined_simulation(simulations, 2)

        self.assertEqual(counts, [6, 4])
        self.assertEqual(costs, [15.0, 20.0])


class BuildDashboardTest(unittest.TestCase):
    """build_dashboard 端到端 JSON 特征测试。"""

    def setUp(self) -> None:
        self.fake = FakeData()

    def test_backend_failures_preserve_initial_dashboard_and_deck_choices(self):
        from types import SimpleNamespace
        from unittest.mock import Mock
        try:
            from test_fsrs_adapter import fsrs
        except ModuleNotFoundError:
            from tests.test_fsrs_adapter import fsrs

        for message in ("no cards to simulate", "invalid parameters"):
            with self.subTest(message=message):
                fake = FakeData()
                fake._deck_names = ["A", "Empty"]
                fake._config["managedDecks"] = []
                populated_simulation = fake.simulate_fsrs
                collection = SimpleNamespace(_backend=SimpleNamespace(
                    simulate_fsrs_review=Mock(side_effect=Exception(message))
                ))

                def simulate(deck, additional_new, days, new_limit):
                    if deck == "Empty":
                        return fsrs.simulate(collection, {}, deck, additional_new, days, new_limit)
                    return populated_simulation(deck, additional_new, days, new_limit)

                fake.simulate_fsrs = simulate
                with patch.object(fsrs, "_new_request", side_effect=lambda params: params):
                    result = dashboard.build_dashboard(data=fake)
                self.assertEqual(result["decks"], ["A", "Empty"])
                self.assertEqual(result["balance"]["budget_minutes"], 30)
                expected = "fsrs_simulator" if message == "no cards to simulate" else "schedule_estimate"
                self.assertEqual(result["capacity"]["source"], expected)
                self.assertNotIn("error", result)

    def test_recommendation_failure_keeps_schedule_estimate_consistent(self):
        with patch.object(dashboard, "native_simulation", side_effect=RuntimeError("offline")):
            baseline = dashboard.build_dashboard(data=FakeData())["capacity"]
        with patch.object(dashboard, "recommended_new_cards", side_effect=RuntimeError("offline")):
            result = dashboard.build_dashboard(data=FakeData())["capacity"]
        self.assertEqual(result, baseline)

    def test_over_budget_cannot_recommend_additions(self):
        fake = FakeData()
        fake._easy_days = {"A": [1, 1, 1, 1, 1, 0, 0]}
        fake.simulate_fsrs = lambda deck, additional_new, days, new_limit: {
            "dailyReviewCount": [1] * days, "dailyNewCount": [0] * days,
            "dailyTimeCost": [464 * 60 / 7] * days}
        cap = dashboard.build_dashboard(requested_daily_budget_minutes=70, data=fake)["capacity"]
        self.assertTrue(cap["overloaded"])
        self.assertEqual(cap["recommended_new"], 0)

    def test_future_allowance_excludes_previously_added_cards(self):
        fake = FakeData()
        fake.simulate_fsrs = lambda deck, additional_new, days, new_limit: {
            "dailyReviewCount": [1] * days, "dailyNewCount": [0] * days,
            "dailyTimeCost": [0] * days}
        with patch.object(dashboard, "recommended_new_cards", return_value=10):
            cap = dashboard.build_dashboard(data=fake)["capacity"]
        self.assertEqual(cap["added_new"], 4)
        self.assertEqual(cap["recommended_new"], 10)

    def test_rest_day_does_not_invite_new_cards(self):
        fake = FakeData()
        fake._easy_days = {"A": [1, 1, 1, 1, 1, 0, 0]}
        with patch.object(dashboard, "datetime") as clock:
            clock.now.return_value = __import__("datetime").datetime(2026, 9, 5, 13)
            clock.fromtimestamp.side_effect = __import__("datetime").datetime.fromtimestamp
            cap = dashboard.build_dashboard(data=fake)["capacity"]
        self.assertEqual(cap["decision"], "今日休息，不建议新增")

    def test_deck_filter_scopes_budget_forecast_and_decision(self):
        fake = FakeData()
        fake._deck_names = ["A", "B"]
        fake._easy_days = {"A": [1, 1, 1, 1, 1, 0, 0], "B": [1, 0, 0, 0, 0, 0, 0]}
        selected = dashboard.build_dashboard(deck="B", data=fake)["capacity"]
        self.assertEqual(selected["weekly_budget_minutes"], 30)
        self.assertEqual(selected["managed_decks"], ["B"])
        self.assertEqual(selected["added_new"], 0)
        self.assertEqual({call["deck"] for call in fake.simulate_calls}, {"B"})

    def test_selected_deck_changes_native_forecast_and_decision(self):
        fake = FakeData()
        fake._deck_names = ["A", "B"]
        original = fake.simulate_fsrs
        def simulate(deck, additional_new, days, new_limit):
            result = original(deck, additional_new, days, new_limit)
            if deck == "B":
                result["dailyTimeCost"] = [0.0] * days
            return result
        fake.simulate_fsrs = simulate
        a = dashboard.build_dashboard(deck="A", data=fake)["capacity"]
        with patch.object(dashboard, "recommended_new_cards", return_value=10):
            b = dashboard.build_dashboard(deck="B", data=fake)["capacity"]
        self.assertGreater(a["forecast_minutes"], b["forecast_minutes"])
        self.assertNotEqual(a["decision"], b["decision"])
        self.assertEqual(b["recommended_new"], 10)

    def test_selected_deck_scopes_fallback_forecast(self):
        fake = FakeData()
        fake._deck_names = ["A", "B"]
        fake.queries['is:due -is:new -is:suspended (deck:"B")'] = [200]
        with patch.object(dashboard, "native_simulation", side_effect=RuntimeError("offline")):
            cap = dashboard.build_dashboard(deck="B", data=fake)["capacity"]
        self.assertEqual(cap["forecast_reviews"], 1)
        self.assertEqual(cap["managed_decks"], ["B"])
        self.assertEqual(cap["source"], "schedule_estimate")

    def test_selected_full_week_does_not_inherit_other_deck_rest_days(self):
        fake = FakeData()
        fake._deck_names = ["A", "B"]
        fake._easy_days = {"A": [1, 0, 0, 0, 0, 0, 0]}
        cap = dashboard.build_dashboard(deck="B", data=fake)["capacity"]
        self.assertEqual(cap["weekly_budget_minutes"], 210)

    def test_no_reviews_does_not_claim_healthy_retrieval(self):
        fake = FakeData()
        fake.reviews = {}
        result = dashboard.build_dashboard(data=fake)
        for signal in result["signals"][:3]:
            self.assertEqual(signal["value"], "—")
            self.assertEqual(signal["status"], "暂无复习数据")
        self.assertIsInstance(result["signals"][3]["value"], int)

    def test_successful_simulation_has_no_pending_simulation_note(self):
        with patch.object(dashboard, "native_simulation", return_value=[{
            "dailyReviewCount": [0] * 90, "dailyNewCount": [0] * 90,
            "dailyTimeCost": [0.0] * 90,
        }]), patch.object(dashboard, "recommended_new_cards", return_value=10):
            cap = dashboard.build_dashboard(data=FakeData())["capacity"]
        self.assertNotIn("待", cap["forecast_note"])
        self.assertNotIn("仍需", cap["forecast_note"])

    def test_build_dashboard_returns_expected_json(self) -> None:
        result = dashboard.build_dashboard(data=self.fake)

        self.assertEqual(result["selected_deck"], "")
        self.assertEqual(result["decks"], ["A"])
        self.assertEqual(
            result["conclusion"], "今天系统基本可控，优先处理长期注意力黑洞。"
        )
        self.assertRegex(
            result["generated_at"], r"^\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}$"
        )

        self.assertEqual(
            result["today"],
            {
                "reviews": 2,
                "minutes": 1.1,
                "avg_s": 32,
                "slow_count": 1,
                "again_rate": 0.0,
                "over_budget_min": 0,
            },
        )
        self.assertEqual(
            result["balance"],
            {
                "due": 10,
                "new_today": 2,
                "new_7": 4,
                "suspended": 1,
                "needs_optimization": 0,
                "budget_minutes": 30,
            },
        )
        self.assertEqual(
            result["capacity"],
            {
                "forecast_counts": [6] * 7,
                "forecast_reviews": 42,
                "forecast_minutes": 11667,
                "scope_forecast_reviews": 15,
                "scope_forecast_minutes": 8,
                "weekly_budget_minutes": 210,
                "headroom_minutes": -11457,
                "recent_avg_s": 32,
                "recommended_new": 0,
                "added_new": 4,
                "managed_decks": ["A"],
                "source": "fsrs_simulator",
                "simulation_days": 90,
                "simulation_error": "",
                "active_days": 7,
                "easy_days": [1.0] * 7,
                "simulator_ready": True,
                "overloaded": True,
                "blocked": True,
                "decision": "未来 7 天暂停新增",
                "decision_basis": "已新增 4 张 · 建议新增 0 张；先处理积压。",
                "forecast_note": "超过预算 11457 分钟（每日）",
            },
        )

        self.assertEqual(len(result["attention_holes"]), 2)
        expected_row = {
                "card_id": 1,
                "deck": "A",
                "front": "题干一",
                "total_min_90": 2.0,
                "total_min_30": 1.0,
                "reviews_90": 3,
                "avg_s": 40,
                "slow_count": 1,
                "again_count": 1,
                "last_times": [60, 30, 31],
                "problems": [],
                "tags": ["tagA"],
                "action": "审计",
            }
        self.assertEqual(
            {key: result["attention_holes"][0][key] for key in expected_row},
            expected_row,
        )
        quality = result["quality_cards"]
        self.assertEqual([row["card_id"] for row in quality], [2])
        self.assertEqual(quality[0]["problems"], ["题干过长"])
        self.assertEqual(quality[0]["action"], "疑似卡差：考虑拆小")
        self.assertEqual(quality[0]["front"], "题" * 180)
        self.assertEqual([row["card_id"] for row in result["slow_cards"]], [1])
        self.assertEqual([row["card_id"] for row in result["again_slow"]], [1])

        # 基线已超预算时应短路搜索：只调用一次 FSRS 模拟。
        self.assertEqual(
            self.fake.simulate_calls,
            [
                {"deck": "A", "additionalNew": 0, "days": 90, "newLimit": 0},
            ],
        )

    def test_optimization_queue_includes_suspended_optimized_cards(self) -> None:
        fake = FakeData()
        fake.cards[5] = make_info(5, front="暂停但仍需优化", tags=["需优化卡片"])
        fake.cards[5]["suspended"] = True
        fake.queries['tag:需优化卡片 (deck:"A")'] = [5]
        result = dashboard.build_dashboard(data=fake)
        self.assertEqual([row["card_id"] for row in result["optimization_queue"]], [5])
        self.assertTrue(result["optimization_queue"][0]["suspended"])

    def test_build_dashboard_uses_requested_budget(self) -> None:
        result = dashboard.build_dashboard(
            requested_daily_budget_minutes=45, data=self.fake
        )
        self.assertEqual(result["balance"]["budget_minutes"], 45)
        self.assertEqual(result["capacity"]["weekly_budget_minutes"], 315)

    def test_build_dashboard_rejects_unknown_deck(self) -> None:
        with self.assertRaises(RuntimeError) as ctx:
            dashboard.build_dashboard(deck="不存在", data=self.fake)
        self.assertEqual(str(ctx.exception), "牌组不存在：不存在")

    def test_build_dashboard_rejects_out_of_range_budget(self) -> None:
        for budget in (0, 1441):
            with self.assertRaises(RuntimeError) as ctx:
                dashboard.build_dashboard(
                    requested_daily_budget_minutes=budget, data=self.fake
                )
            self.assertEqual(str(ctx.exception), "每日预算须为 1-1440 分钟。")

    def test_build_dashboard_without_data_raises(self) -> None:
        with self.assertRaises(RuntimeError) as ctx:
            dashboard.build_dashboard()
        self.assertIn("数据通道未初始化", str(ctx.exception))

    def test_budget_discounts_rest_days_via_easy_days(self) -> None:
        # Sat/Sun off (percentages 0) -> 5 active days -> budget 5*30=150,
        # not 7*30=210. This is the core #6 fix: budget side now reads the
        # same easyDaysPercentages the forecast side already uses.
        self.fake._easy_days = {"A": [1.0, 1.0, 1.0, 1.0, 1.0, 0.0, 0.0]}
        cap = dashboard.build_dashboard(data=self.fake)["capacity"]
        self.assertEqual(cap["active_days"], 5)
        self.assertEqual(cap["easy_days"], [1.0, 1.0, 1.0, 1.0, 1.0, 0.0, 0.0])
        self.assertEqual(cap["weekly_budget_minutes"], 150)
        self.assertEqual(cap["forecast_note"], "超过预算 11517 分钟（有效 5 天）")

    def test_budget_defaults_to_seven_days_when_no_easy_days(self) -> None:
        cap = dashboard.build_dashboard(data=self.fake)["capacity"]
        self.assertEqual(cap["active_days"], 7)
        self.assertEqual(cap["easy_days"], [1.0] * 7)
        self.assertEqual(cap["weekly_budget_minutes"], 210)

    def test_empty_managed_decks_simulates_top_level_decks(self) -> None:
        # Default "all decks": empty managedDecks should expand to top-level
        # decks for FSRS simulation (each top deck covers its subdecks via the
        # deck:"X" search), and must NOT raise "尚未设置管理范围".
        self.fake._deck_names = ["A", "A::B", "C"]
        self.fake._config["managedDecks"] = []
        result = dashboard.build_dashboard(data=self.fake)
        simulated = sorted(call["deck"] for call in self.fake.simulate_calls)
        self.assertEqual(simulated, ["A", "C"])  # subdeck A::B not simulated
        self.assertEqual(result["capacity"]["managed_decks"], [])

    def test_precomputes_capacity_decision_for_display(self) -> None:
        cap = dashboard.build_dashboard(data=self.fake)["capacity"]
        self.assertTrue(cap["simulator_ready"])
        self.assertTrue(cap["overloaded"])
        self.assertTrue(cap["blocked"])
        self.assertEqual(cap["decision"], "未来 7 天暂停新增")
        self.assertEqual(
            cap["decision_basis"], "已新增 4 张 · 建议新增 0 张；先处理积压。"
        )
        self.assertEqual(cap["forecast_note"], "超过预算 11457 分钟（每日）")

    def test_precomputes_today_signals_with_tones(self) -> None:
        signals = dashboard.build_dashboard(data=self.fake)["signals"]
        self.assertEqual(
            [s["label"] for s in signals],
            ["单卡均时", "40 秒以上", "Again 率", "当前到期"],
        )
        avg, slow, again, due = signals
        self.assertEqual(
            avg,
            {"label": "单卡均时", "value": 32, "unit": "秒", "tone": "tone-green", "status": "可控"},
        )
        self.assertEqual(
            slow,
            {"label": "40 秒以上", "value": 1, "unit": "次", "tone": "tone-amber", "status": "需要审计"},
        )
        self.assertEqual(
            again,
            {"label": "Again 率", "value": 0.0, "unit": "%", "tone": "tone-green", "status": "稳定"},
        )
        self.assertEqual(
            due,
            {"label": "当前到期", "value": 10, "unit": "张", "tone": "tone-green", "status": "可控"},
        )


if __name__ == "__main__":
    unittest.main()
