#!/usr/bin/env python3
"""Attention dashboard business logic.

Pure: no ``aqt``/``anki`` imports, no HTTP, no global state. All collection
access goes through the :class:`~data.AttentionData` interface injected into
:func:`build_dashboard`, so the entire dashboard can be built and tested
against a fake data source. The add-on's webview layer (see
:mod:`attention_dashboard_window`) calls ``build_dashboard`` from a
``QueryOp`` and hands the resulting dict to JavaScript for rendering.
"""

from __future__ import annotations

import html
import math
import re
import time
from dataclasses import dataclass, field
from datetime import datetime
from html.parser import HTMLParser
from typing import Any, TYPE_CHECKING

if TYPE_CHECKING:  # avoid a runtime import cycle / anki dependency
    from .data import AttentionData


SLOW_MS = 40_000
OBVIOUS_SLOW_MS = 60_000
TARGET_DAILY_MINUTES = 30
WEEK_DAYS = 7


def strip_html(value: str) -> str:
    value = re.sub(r"<style.*?</style>", " ", value, flags=re.S | re.I)
    value = re.sub(r"<script.*?</script>", " ", value, flags=re.S | re.I)
    value = re.sub(r"<br\s*/?>", "\n", value, flags=re.I)
    value = re.sub(r"</(div|p|li|tr|h\d)>", "\n", value, flags=re.I)
    value = re.sub(r"<[^>]+>", " ", value)
    value = html.unescape(value)
    value = re.sub(r"[ \t]+", " ", value)
    value = re.sub(r"\n\s+", "\n", value)
    return value.strip()


def has_image(fields: dict[str, Any]) -> bool:
    return any("<img" in (field.get("value") or "").lower() for field in fields.values())


class _FirstImageSrcParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.src = ""

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if self.src or tag.lower() != "img":
            return
        for name, value in attrs:
            if name.lower() == "src" and value:
                self.src = value.strip()
                return


def first_image_src(fields: dict[str, Any]) -> str:
    html_value = "\n".join(str(field.get("value") or "") for field in fields.values())
    parser = _FirstImageSrcParser()
    try:
        parser.feed(html_value)
    except Exception:
        parser.src = ""
    if parser.src:
        return parser.src
    image_match = re.search(
        r"<img\b[^>]*\bsrc\s*=\s*(?:\"([^\"]+)\"|'([^']+)'|([^\s>]+))",
        html_value,
        flags=re.I,
    )
    if not image_match:
        return ""
    return next((group for group in image_match.groups() if group), "")


def first_readable_field(fields: dict[str, Any], names: list[str]) -> tuple[str, str]:
    for name in names:
        field = fields.get(name)
        if not field:
            continue
        text = strip_html(field.get("value") or "")
        if text:
            return name, text
    return "", ""


def field_text(fields: dict[str, Any], names: list[str]) -> str:
    values = []
    for name in names:
        field = fields.get(name)
        if field:
            values.append(strip_html(field.get("value") or ""))
    return "\n".join(v for v in values if v).strip()


def detect_quality(front: str, back: str, image_card: bool) -> list[str]:
    problems: list[str] = []
    combined = f"{front}\n{back}"
    if len(front) > 180:
        problems.append("题干过长")
    if len(back) > 320:
        problems.append("答案过长")
    if len(re.findall(r"\n", back)) >= 5:
        problems.append("答案多行")
    if re.search(r"(哪些|哪几个|几点|步骤|流程|优缺点|列举|分别|如何排查|原因.*有|包括)", combined):
        problems.append("疑似列表卡")
    if len(re.findall(r"[?？]", front)) >= 2:
        problems.append("多问合一")
    if re.search(r"(如下图|本题|第\s*\d+\s*行|上面|下面)", combined):
        problems.append("上下文依赖")
    return problems[:4]


@dataclass
class CardReview:
    card_id: int
    total_ms_90: int
    total_ms_30: int
    reviews_90: int
    slow_count: int
    again_count: int
    avg_ms: int
    last_times: list[int]
    recent_reviews: list[dict[str, Any]] = field(default_factory=list)
    again_day_count: int = 0


def summarize_reviews(card_id: int, reviews: list[dict[str, Any]], now_ms: int) -> CardReview | None:
    start_90 = now_ms - 90 * 24 * 3600 * 1000
    start_30 = now_ms - 30 * 24 * 3600 * 1000
    valid = [
        r
        for r in reviews
        if r.get("ease", 0) > 0 and r.get("time", 0) > 0 and r.get("id", 0) > 0
    ]
    if not valid:
        return None
    in_90 = [r for r in valid if int(r["id"]) >= start_90]
    recent = sorted(valid, key=lambda r: int(r["id"]), reverse=True)[:5]
    total_90 = sum(int(r["time"]) for r in in_90)
    total_30 = sum(int(r["time"]) for r in in_90 if int(r["id"]) >= start_30)
    slow = sum(1 for r in recent if int(r["time"]) >= SLOW_MS)
    again_reviews = [r for r in recent if int(r["ease"]) == 1]
    again = len(again_reviews)
    again_days = {datetime.fromtimestamp(int(r["id"]) / 1000).date() for r in again_reviews}
    last = [round(int(r["time"]) / 1000) for r in recent]
    events = [
        {
            "reviewId": int(r["id"]),
            "reviewedAt": int(r["id"]) / 1000,
            "durationMs": int(r["time"]),
            "rating": int(r["ease"]),
            "reviewType": int(r.get("type", 0)),
        }
        for r in recent
    ]
    return CardReview(
        card_id=card_id,
        total_ms_90=total_90,
        total_ms_30=total_30,
        reviews_90=len(in_90),
        slow_count=slow,
        again_count=again,
        again_day_count=len(again_days),
        avg_ms=round(sum(int(r["time"]) for r in recent) / len(recent)),
        last_times=last,
        recent_reviews=events,
    )


def empty_review(card_id: int) -> CardReview:
    return CardReview(card_id, 0, 0, 0, 0, 0, 0, [], [])


def has_single_obvious_time(review: CardReview) -> bool:
    return any(event.get("durationMs", 0) >= OBVIOUS_SLOW_MS for event in review.recent_reviews)


def has_again_signal(review: CardReview) -> bool:
    return review.again_count >= 3 or (review.again_count >= 2 and review.again_day_count >= 2)


def candidate_reasons(review: CardReview, problems: list[str]) -> list[str]:
    reasons: list[str] = []
    recent_count = len(review.recent_reviews)
    if review.slow_count >= 2:
        reasons.append(f"最近 {recent_count} 次中 {review.slow_count} 次≥40秒")
    if has_again_signal(review):
        if review.again_count >= 3:
            reasons.append(f"最近 {recent_count} 次中 {review.again_count} 次 Again")
        else:
            reasons.append(
                f"最近 {recent_count} 次中 {review.again_count} 次 Again，跨 {review.again_day_count} 个复习日"
            )
    if problems and (review.slow_count >= 2 or has_again_signal(review) or has_single_obvious_time(review)):
        reasons.append("结构疑点")
    return reasons


def make_card_row(review: CardReview, info: dict[str, Any] | None) -> dict[str, Any]:
    fields = (info or {}).get("fields") or {}
    front_names = ["Remarks", "TextFront", "ClozeFront", "Front", "Text", "Question"]
    back_names = ["TextBack", "ClozeBack", "Back", "Back Extra", "Answer"]
    ordered = sorted(fields, key=lambda name: fields[name].get("order", 0))
    ordered_names = [name for name in ordered]
    front_source, front = first_readable_field(fields, front_names)
    if not front:
        front_source, front = first_readable_field(
            fields, [name for name in ordered_names if name not in back_names]
        )
    back = field_text(fields, back_names)
    if not back:
        back = field_text(fields, [name for name in ordered_names if name != front_source])
    image_card = has_image(fields)
    deck = (info or {}).get("deckName", "")
    note_type = (info or {}).get("noteType", "")
    field_names = ordered_names
    tags = (info or {}).get("tags", [])
    problems = detect_quality(front, back, image_card)
    limitations = ["图片无法分析"] if image_card and not front else []
    thumbnail = first_image_src(fields)
    title_parts = [part for part in [note_type, deck, " / ".join(field_names[:3])] if part]
    title_fallback = " · ".join(title_parts + [f"卡片 {review.card_id}"])
    preview_kind = "image" if thumbnail else ("text" if front else "fallback")
    summary = front[:180] or title_fallback
    reasons = candidate_reasons(review, problems)
    action = "审计"
    if "面试" in front + back or "考试" in front + back:
        action = "阶段卡：考虑暂停并打标签"
    elif any(p in problems for p in ["题干过长", "答案过长", "疑似列表卡", "多问合一"]):
        action = "疑似卡差：考虑拆小"
    elif review.avg_ms >= SLOW_MS and review.again_count == 0:
        action = "有价值但难：先观察"
    return {
        "card_id": review.card_id,
        "cardId": review.card_id,
        "noteId": int((info or {}).get("noteId", 0)),
        "deckId": int((info or {}).get("deckId", 0)),
        "deck": deck,
        "summary": summary,
        "thumbnail": thumbnail,
        "previewKind": preview_kind,
        "previewText": front[:180],
        "titleFallback": title_fallback,
        "noteType": note_type,
        "fieldNames": field_names,
        "suspended": bool((info or {}).get("suspended", False)),
        "front": front[:180],
        "total_min_90": round(review.total_ms_90 / 60000, 1),
        "total_min_30": round(review.total_ms_30 / 60000, 1),
        "recent30Minutes": round(review.total_ms_30 / 60000, 1),
        "reviews_90": review.reviews_90,
        "avg_s": round(review.avg_ms / 1000),
        "slow_count": review.slow_count,
        "again_count": review.again_count,
        "again_day_count": review.again_day_count,
        "last_times": review.last_times,
        "recent_reviews": review.recent_reviews,
        "recentReviews": review.recent_reviews,
        "reasons": reasons,
        "problems": problems,
        "limitations": limitations,
        "tags": tags,
        "action": action,
    }


def query_count(query: str, data: "AttentionData") -> int:
    return len(data.find_cards(query))


_BS = chr(92)
_Q = chr(34)


def _escape_deck(deck: str) -> str:
    """Escape backslashes and double quotes for an Anki search deck filter."""
    return deck.replace(_BS, _BS + _BS).replace(_Q, _BS + _Q)


def scoped_query(query: str, deck: str) -> str:
    if not deck:
        return query
    return f'{query} deck:"{_escape_deck(deck)}"'


def managed_query(query: str, decks: list[str]) -> str:
    if not decks:
        return query
    clauses = [f'deck:"{_escape_deck(deck)}"' for deck in decks]
    return f'{query} ({" or ".join(clauses)})'


def view_query(query: str, deck: str, managed_decks: list[str]) -> str:
    return scoped_query(query, deck) if deck else managed_query(query, managed_decks)


def representative_easy_days(
    data: "AttentionData",
    deck: str,
    managed_decks: list[str],
    deck_names: list[str],
) -> list[float]:
    """The easy-days schedule that should shape the weekly budget.

    Picks the first in-scope deck that actually has rest days (a percentage
    below 1); falls back to full load when none is set. Rest days are normally
    uniform across a user's decks, so one representative suffices for the
    budget denominator. Source: deck config ``easyDaysPercentages`` (Anki
    native FSRS Easy Days) — the same field FSRS Helper and the FSRS
    simulator read.
    """
    candidates = [deck] if deck else (
        managed_decks or [n for n in deck_names if "::" not in n]
    )
    seen: set[str] = set()
    for name in candidates:
        if name in seen:
            continue
        seen.add(name)
        pct = data.easy_days_percentages(name)
        if pct and any(p < 1 for p in pct):
            return pct
    return [1.0] * WEEK_DAYS


def attention_config(data: "AttentionData") -> dict[str, Any]:
    return data.config() or {}


def native_simulation(
    decks: list[str],
    days: int,
    additional_new: int,
    data: "AttentionData",
) -> list[dict[str, Any]]:
    if not decks:
        raise RuntimeError("尚未设置管理范围。")
    new_limit = math.ceil(additional_new / WEEK_DAYS) if additional_new else 0
    return [
        data.simulate_fsrs(deck, additional_new, days, new_limit)
        for deck in decks
    ]


def combined_simulation(
    simulations: list[dict[str, Any]],
    days: int,
) -> tuple[list[int], list[float]]:
    counts = [0] * days
    costs = [0.0] * days
    for result in simulations:
        reviews = result.get("dailyReviewCount") or []
        new_cards = result.get("dailyNewCount") or []
        time_cost = result.get("dailyTimeCost") or []
        for index in range(min(days, len(time_cost))):
            counts[index] += int(reviews[index]) + int(new_cards[index])
            costs[index] += float(time_cost[index])
    return counts, costs


def within_daily_budget(costs: list[float], daily_budget_seconds: int) -> bool:
    if not costs:
        return False
    for start in range(len(costs)):
        window = costs[start : start + WEEK_DAYS]
        if sum(window) / len(window) > daily_budget_seconds:
            return False
    return True


def recommended_new_cards(
    decks: list[str],
    baseline: list[dict[str, Any]],
    days: int,
    daily_budget_seconds: int,
    data: "AttentionData",
) -> int:
    _, baseline_costs = combined_simulation(baseline, days)
    if not within_daily_budget(baseline_costs, daily_budget_seconds):
        return 0

    baseline_by_deck = [combined_simulation([result], days)[1] for result in baseline]

    def fits(candidate: int) -> bool:
        simulations = native_simulation(decks, days, candidate, data)
        worst_costs = baseline_costs.copy()
        for index, result in enumerate(simulations):
            _, candidate_costs = combined_simulation([result], days)
            for day in range(days):
                delta = max(0.0, candidate_costs[day] - baseline_by_deck[index][day])
                worst_costs[day] = max(worst_costs[day], baseline_costs[day] + delta)
        return within_daily_budget(worst_costs, daily_budget_seconds)

    low, high = 0, 100
    while high < 10_000 and fits(high):
        low = high
        high = min(high * 2, 10_000)
    if high == 10_000 and fits(high):
        return high
    while low < high:
        middle = (low + high + 1) // 2
        if fits(middle):
            low = middle
        else:
            high = middle - 1
    return low


def build_dashboard(
    deck: str = "",
    requested_daily_budget_minutes: int | None = None,
    data: "AttentionData | None" = None,
) -> dict[str, Any]:
    if data is None:
        raise RuntimeError(
            "数据通道未初始化：请从 Anki 工具菜单或工具栏启动 Attention Is All You Need。"
        )
    started = time.time()
    now_ms = int(time.time() * 1000)
    config = attention_config(data)
    managed_decks = [str(name) for name in config.get("managedDecks") or []]
    simulation_days = int(config.get("simulationDays", 90))
    configured_budget_minutes = int(
        config.get("dailyBudgetMinutes", TARGET_DAILY_MINUTES)
    )
    daily_budget_minutes = (
        requested_daily_budget_minutes
        if requested_daily_budget_minutes is not None
        else configured_budget_minutes
    )
    if not 1 <= daily_budget_minutes <= 1440:
        raise RuntimeError("每日预算须为 1-1440 分钟。")
    deck_names = sorted(data.deck_names(), key=str.casefold)
    if deck and deck not in deck_names:
        raise RuntimeError(f"牌组不存在：{deck}")

    # A selected deck defines the entire dashboard scope, including capacity.
    if deck:
        managed_decks = [deck]
    easy_days = representative_easy_days(data, deck, managed_decks, deck_names)
    active_days = sum(1 for p in easy_days if p > 0)

    # FSRS simulation is per-deck; an empty managedDecks means "all decks",
    # so expand to top-level decks (each covers its subdecks via deck:"X").
    simulation_decks = managed_decks or [
        n for n in deck_names if "::" not in n
    ]

    burden_ids = data.find_cards(view_query("-is:suspended", deck, managed_decks))
    candidate_source_ids = data.find_cards(
        view_query(
            "-is:suspended -tag:看板忽略 -tag:需优化卡片",
            deck,
            managed_decks,
        )
    )
    optimization_ids = data.find_cards(
        view_query("tag:需优化卡片", deck, managed_decks)
    )
    all_needed_ids = sorted(set(burden_ids) | set(candidate_source_ids) | set(optimization_ids))
    infos = data.card_infos(all_needed_ids)
    reviews = data.reviews_of_cards(all_needed_ids)
    burden_reviews = {cid: reviews.get(cid, []) for cid in burden_ids}
    capacity_reviews = burden_reviews
    if deck or managed_decks:
        capacity_ids = data.find_cards(managed_query("-is:suspended", managed_decks))
        capacity_reviews = data.reviews_of_cards(capacity_ids)
    summaries_by_id = {
        int(cid): summarize_reviews(int(cid), reviews.get(int(cid), []), now_ms)
        or empty_review(int(cid))
        for cid in all_needed_ids
    }
    candidate_rows = [
        make_card_row(summaries_by_id[int(cid)], infos.get(int(cid)))
        for cid in candidate_source_ids
    ]
    review_queue = sorted(
        [row for row in candidate_rows if row["reasons"]],
        key=lambda row: (
            row["total_min_30"],
            row["slow_count"],
            row["again_count"],
            row["again_day_count"],
            len(row["problems"]),
        ),
        reverse=True,
    )
    optimization_queue = [
        make_card_row(summaries_by_id[int(cid)], infos.get(int(cid)))
        for cid in optimization_ids
    ]
    summaries = [summaries_by_id[int(cid)] for cid in burden_ids]

    today_reviews = [
        r
        for revs in burden_reviews.values()
        for r in revs
        if r.get("ease", 0) > 0
        and r.get("time", 0) > 0
        and datetime.fromtimestamp(int(r["id"]) / 1000).date() == datetime.now().date()
    ]
    today_total_ms = sum(int(r["time"]) for r in today_reviews)
    today_avg_ms = round(today_total_ms / len(today_reviews)) if today_reviews else 0
    today_slow = sum(1 for r in today_reviews if int(r["time"]) >= SLOW_MS)
    today_again = sum(1 for r in today_reviews if int(r["ease"]) == 1)
    over_budget = max(0, round(today_total_ms / 60000 - daily_budget_minutes, 1))

    start_30 = now_ms - 30 * 24 * 3600 * 1000
    recent_30_reviews = [
        review
        for card_reviews in reviews.values()
        for review in card_reviews
        if review.get("ease", 0) > 0
        and review.get("time", 0) > 0
        and int(review.get("id", 0)) >= start_30
    ]
    scope_recent_avg_ms = (
        round(sum(int(r["time"]) for r in recent_30_reviews) / len(recent_30_reviews))
        if recent_30_reviews
        else max(today_avg_ms, 45_000)
    )
    capacity_recent_30 = [
        review
        for card_reviews in capacity_reviews.values()
        for review in card_reviews
        if review.get("ease", 0) > 0
        and review.get("time", 0) > 0
        and int(review.get("id", 0)) >= start_30
    ]
    capacity_avg_ms = (
        round(sum(int(r["time"]) for r in capacity_recent_30) / len(capacity_recent_30))
        if capacity_recent_30
        else 45_000
    )

    top_attention = sorted(summaries, key=lambda s: s.total_ms_90, reverse=True)[:20]
    top_slow = sorted(summaries, key=lambda s: (s.slow_count, s.total_ms_90), reverse=True)[:20]
    top_again_slow = sorted(summaries, key=lambda s: (s.again_count, s.slow_count, s.total_ms_90), reverse=True)[:20]
    needed_ids = sorted({s.card_id for s in top_attention + top_slow + top_again_slow})
    infos.update(data.card_infos([cid for cid in needed_ids if cid not in infos]))

    rows_attention = [make_card_row(s, infos.get(s.card_id)) for s in top_attention]
    rows_slow = [make_card_row(s, infos.get(s.card_id)) for s in top_slow if s.slow_count > 0]
    rows_again = [make_card_row(s, infos.get(s.card_id)) for s in top_again_slow if s.again_count > 0]
    quality_by_id = {
        row["card_id"]: row
        for row in rows_attention + rows_slow + rows_again
        if row["problems"]
    }
    quality_cards = sorted(
        quality_by_id.values(),
        key=lambda row: (len(row["problems"]), row["total_min_90"]),
        reverse=True,
    )

    due_count = query_count(view_query("is:due -is:suspended", deck, managed_decks), data)
    new_today = query_count(view_query("introduced:1", deck, managed_decks), data)
    new_7 = query_count(view_query("introduced:7", deck, managed_decks), data)
    suspended = query_count(view_query("is:suspended", deck, managed_decks), data)
    needs_optimization = query_count(view_query("tag:需优化卡片", deck, managed_decks), data)
    scope_forecast_counts = [
        query_count(view_query("is:due -is:new -is:suspended", deck, managed_decks), data),
        *[
            query_count(
                view_query(
                    f"is:review prop:due={day} -is:suspended",
                    deck,
                    managed_decks,
                ),
                data,
            )
            for day in range(1, WEEK_DAYS)
        ],
    ]
    forecast_counts = [
        query_count(managed_query("is:due -is:new -is:suspended", managed_decks), data),
        *[
            query_count(
                managed_query(
                    f"is:review prop:due={day} -is:suspended",
                    managed_decks,
                ),
                data,
            )
            for day in range(1, WEEK_DAYS)
        ],
    ]
    forecast_reviews = sum(forecast_counts)
    forecast_ms = forecast_reviews * capacity_avg_ms
    scope_forecast_reviews = sum(scope_forecast_counts)
    scope_forecast_ms = scope_forecast_reviews * scope_recent_avg_ms
    weekly_budget_ms = active_days * daily_budget_minutes * 60_000
    headroom_ms = weekly_budget_ms - forecast_ms
    recommended_new = 0 if headroom_ms < 0 else None
    global_new_7 = query_count(
        managed_query("introduced:7", managed_decks), data
    )
    capacity_source = "schedule_estimate"
    simulation_error = ""
    try:
        baseline_simulations = native_simulation(
            simulation_decks,
            simulation_days,
            0,
            data,
        )
        native_counts, native_costs = combined_simulation(
            baseline_simulations,
            simulation_days,
        )
        additional_new = recommended_new_cards(
            simulation_decks,
            baseline_simulations,
            simulation_days,
            daily_budget_minutes * 60 * active_days / WEEK_DAYS,
            data,
        )
        forecast_counts = native_counts[:WEEK_DAYS]
        forecast_reviews = sum(forecast_counts)
        forecast_seconds = sum(native_costs[:WEEK_DAYS])
        forecast_ms = round(forecast_seconds * 1000)
        headroom_ms = weekly_budget_ms - forecast_ms
        recommended_new = (
            0 if headroom_ms < 0 or additional_new == 0
            else additional_new
        )
        capacity_source = "fsrs_simulator"
    except RuntimeError as exc:
        simulation_error = str(exc)

    if not today_reviews:
        conclusion = "今天还没有有效复习记录。"
    elif today_avg_ms >= 50_000:
        conclusion = "今天主要问题是单卡偏重，不是单纯数量太多。"
    elif today_again / max(len(today_reviews), 1) >= 0.25:
        conclusion = "今天主要问题是重新学习比例偏高。"
    elif today_total_ms / 60000 > daily_budget_minutes:
        conclusion = f"今天已超过 {daily_budget_minutes} 分钟预算，需要停止扩张。"
    else:
        conclusion = "今天系统基本可控，优先处理长期注意力黑洞。"

    # Pre-computed, display-ready values so JS only renders, never re-derives
    # business rules (tone thresholds, the weekly decision, the forecast note).
    today_avg_s = round(today_avg_ms / 1000) if today_avg_ms else 0
    today_again_rate = round(today_again / max(len(today_reviews), 1) * 100, 1)
    signals = [
        {
            "label": "单卡均时",
            "value": today_avg_s,
            "unit": "秒",
            "tone": (
                "tone-red" if today_avg_s >= 60
                else "tone-amber" if today_avg_s >= 40
                else "tone-green"
            ),
            "status": "检索偏重" if today_avg_s >= 50 else "可控",
        },
        {
            "label": "40 秒以上",
            "value": today_slow,
            "unit": "次",
            "tone": "tone-amber" if today_slow else "tone-green",
            "status": "需要审计" if today_slow else "无异常",
        },
        {
            "label": "Again 率",
            "value": today_again_rate,
            "unit": "%",
            "tone": (
                "tone-red" if today_again_rate >= 25
                else "tone-amber" if today_again_rate >= 15
                else "tone-green"
            ),
            "status": "提取不稳" if today_again_rate >= 15 else "稳定",
        },
        {
            "label": "当前到期",
            "value": due_count,
            "unit": "张",
            "tone": "tone-red" if due_count > 100 else "tone-green",
            "status": "队列积压" if due_count > 100 else "可控",
        },
    ]

    if not today_reviews:
        for signal in signals[:3]:
            signal.update(value="—", tone="tone-muted", status="暂无复习数据")

    headroom_minutes = round(headroom_ms / 60000)
    simulator_ready = capacity_source == "fsrs_simulator"
    overloaded = headroom_ms < 0
    blocked = (recommended_new == 0) if simulator_ready else overloaded
    if blocked:
        decision = "未来 7 天暂停新增"
        decision_basis = f"已新增 {global_new_7} 张 · 建议新增 0 张；先处理积压。"
    elif simulator_ready:
        decision = f"未来 7 天建议新增 {recommended_new} 张"
        decision_basis = f"近 7 天已新增 {global_new_7} 张 · 未来 7 天可再新增 {recommended_new} 张。"
    else:
        decision = "新增额度待 FSRS 模拟"
        decision_basis = f"已新增 {global_new_7} 张；FSRS 模拟暂不可用，暂不建议增加额度。"
    if easy_days[datetime.now().weekday()] == 0:
        decision = "今日休息，不建议新增"
        decision_basis = (
            "按当前牌组的休息日设置，今天不安排新增。"
            + ("未来 7 天已超预算，先处理积压。" if overloaded
               else "未来新增安排请在复习日重新评估。")
        )
    day_scope = (
        f"有效 {active_days} 天" if active_days < WEEK_DAYS else "每日"
    )
    if overloaded:
        forecast_note = f"超过预算 {abs(headroom_minutes)} 分钟（{day_scope}）"
    else:
        forecast_note = f"尚余 {headroom_minutes} 分钟（{day_scope}）"
        if not simulator_ready:
            forecast_note += "，新增额度待 FSRS 模拟"

    return {
        "generated_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "runtime_s": round(time.time() - started, 1),
        "selected_deck": deck,
        "decks": deck_names,
        "title": "Attention Is All You Need",
        "subtitle": "人的注意力有限，能制造的 Anki 卡片近乎无限；两者的矛盾，就是 Anki 系统的核心风险。",
        "conclusion": conclusion,
        "signals": signals,
        "today": {
            "reviews": len(today_reviews),
            "minutes": round(today_total_ms / 60000, 1),
            "avg_s": today_avg_s,
            "slow_count": today_slow,
            "again_rate": today_again_rate,
            "over_budget_min": over_budget,
        },
        "balance": {
            "due": due_count,
            "new_today": new_today,
            "new_7": new_7,
            "suspended": suspended,
            "needs_optimization": needs_optimization,
            "budget_minutes": daily_budget_minutes,
        },
        "capacity": {
            "forecast_counts": forecast_counts,
            "forecast_reviews": forecast_reviews,
            "forecast_minutes": round(forecast_ms / 60000),
            "scope_forecast_reviews": scope_forecast_reviews,
            "scope_forecast_minutes": round(scope_forecast_ms / 60000),
            "weekly_budget_minutes": round(weekly_budget_ms / 60000),
            "headroom_minutes": headroom_minutes,
            "recent_avg_s": round(capacity_avg_ms / 1000),
            "recommended_new": recommended_new,
            "added_new": global_new_7,
            "managed_decks": managed_decks,
            "source": capacity_source,
            "simulation_days": simulation_days,
            "simulation_error": simulation_error,
            "active_days": active_days,
            "easy_days": easy_days,
            "simulator_ready": simulator_ready,
            "overloaded": overloaded,
            "blocked": blocked,
            "decision": decision,
            "decision_basis": decision_basis,
            "forecast_note": forecast_note,
        },
        "attention_holes": rows_attention,
        "quality_cards": quality_cards[:20],
        "slow_cards": rows_slow,
        "again_slow": rows_again,
        "review_queue": review_queue,
        "optimization_queue": optimization_queue,
        "candidate_counts": {
            "reviewTotal": len(review_queue),
            "optimizationTotal": len(optimization_queue),
            "displayed": len(review_queue),
            "overviewDisplayed": min(len(review_queue), 6),
        },
    }
