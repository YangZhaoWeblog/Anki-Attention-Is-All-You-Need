#!/usr/bin/env python3
"""Local attention dashboard served by the Anki add-on."""

from __future__ import annotations

import html
import json
import math
import re
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from datetime import datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, Callable


ANKI_CONNECT = "http://127.0.0.1:8765"
HOST = "127.0.0.1"
PORT = 8766
SLOW_MS = 60_000
TARGET_DAILY_MINUTES = 30
WEEK_DAYS = 7
_ANKI_CALLER: Callable[[str, dict[str, Any]], Any] | None = None


def set_anki_caller(caller: Callable[[str, dict[str, Any]], Any]) -> None:
    global _ANKI_CALLER
    _ANKI_CALLER = caller


def anki(action: str, params: dict[str, Any] | None = None) -> Any:
    if _ANKI_CALLER is not None:
        return _ANKI_CALLER(action, params or {})
    payload = json.dumps({"action": action, "version": 6, "params": params or {}}).encode()
    request = urllib.request.Request(
        ANKI_CONNECT,
        data=payload,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=20) as response:
            result = json.loads(response.read().decode())
    except (urllib.error.URLError, TimeoutError) as exc:
        raise RuntimeError("无法连接 AnkiConnect。请先启动 Anki，并确认 AnkiConnect 已启用。") from exc
    if result.get("error"):
        raise RuntimeError(result["error"])
    return result.get("result")


def chunks(items: list[int], size: int) -> list[list[int]]:
    return [items[i : i + size] for i in range(0, len(items), size)]


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
    if image_card and not front:
        problems.append("图片卡：不可按空题干判断")
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


def summarize_reviews(card_id: int, reviews: list[dict[str, Any]], now_ms: int) -> CardReview | None:
    start_90 = now_ms - 90 * 24 * 3600 * 1000
    start_30 = now_ms - 30 * 24 * 3600 * 1000
    clean = [
        r
        for r in reviews
        if r.get("ease", 0) > 0 and r.get("time", 0) > 0 and r.get("id", 0) >= start_90
    ]
    if not clean:
        return None
    total_90 = sum(int(r["time"]) for r in clean)
    total_30 = sum(int(r["time"]) for r in clean if int(r["id"]) >= start_30)
    slow = sum(1 for r in clean if int(r["time"]) >= SLOW_MS)
    again = sum(1 for r in clean if int(r["ease"]) == 1)
    last = [round(int(r["time"]) / 1000) for r in sorted(clean, key=lambda r: r["id"], reverse=True)[:5]]
    return CardReview(
        card_id=card_id,
        total_ms_90=total_90,
        total_ms_30=total_30,
        reviews_90=len(clean),
        slow_count=slow,
        again_count=again,
        avg_ms=round(total_90 / len(clean)),
        last_times=last,
    )


def card_infos(card_ids: list[int]) -> dict[int, dict[str, Any]]:
    infos: dict[int, dict[str, Any]] = {}
    for part in chunks(card_ids, 100):
        for item in anki("cardsInfo", {"cards": part}):
            infos[int(item["cardId"])] = item
    return infos


def get_reviews(card_ids: list[int]) -> dict[int, list[dict[str, Any]]]:
    all_reviews: dict[int, list[dict[str, Any]]] = {}
    for part in chunks(card_ids, 80):
        result = anki("getReviewsOfCards", {"cards": part})
        for key, value in result.items():
            all_reviews[int(key)] = value or []
    return all_reviews


def make_card_row(review: CardReview, info: dict[str, Any] | None) -> dict[str, Any]:
    fields = (info or {}).get("fields") or {}
    front = field_text(fields, ["Remarks", "TextFront", "ClozeFront", "Front"])
    back = field_text(fields, ["TextBack", "ClozeBack", "Back"])
    image_card = has_image(fields)
    deck = (info or {}).get("deckName", "")
    tags = (info or {}).get("tags", [])
    problems = detect_quality(front, back, image_card)
    action = "审计"
    if "面试" in front + back or "考试" in front + back:
        action = "阶段卡：考虑暂停并打标签"
    elif any(p in problems for p in ["题干过长", "答案过长", "疑似列表卡", "多问合一"]):
        action = "疑似卡差：考虑拆小"
    elif review.avg_ms >= SLOW_MS and review.again_count == 0:
        action = "有价值但难：先观察"
    return {
        "card_id": review.card_id,
        "deck": deck,
        "front": front[:180] or ("[图片卡]" if image_card else "[无可读文本]"),
        "total_min_90": round(review.total_ms_90 / 60000, 1),
        "total_min_30": round(review.total_ms_30 / 60000, 1),
        "reviews_90": review.reviews_90,
        "avg_s": round(review.avg_ms / 1000),
        "slow_count": review.slow_count,
        "again_count": review.again_count,
        "last_times": review.last_times,
        "problems": problems,
        "tags": tags,
        "action": action,
    }


def query_count(query: str) -> int:
    return len(anki("findCards", {"query": query}))


def scoped_query(query: str, deck: str) -> str:
    if not deck:
        return query
    escaped = deck.replace("\\", "\\\\").replace('"', '\\"')
    return f'{query} deck:"{escaped}"'


def managed_query(query: str, decks: list[str]) -> str:
    if not decks:
        return query
    clauses = []
    for deck in decks:
        escaped = deck.replace("\\", "\\\\").replace('"', '\\"')
        clauses.append(f'deck:"{escaped}"')
    return f'{query} ({" or ".join(clauses)})'


def view_query(query: str, deck: str, managed_decks: list[str]) -> str:
    return scoped_query(query, deck) if deck else managed_query(query, managed_decks)


def attention_config() -> dict[str, Any]:
    try:
        return anki("attentionConfig") or {}
    except RuntimeError as exc:
        if "unsupported action" not in str(exc):
            raise
        return {}


def native_simulation(
    decks: list[str],
    days: int,
    additional_new: int = 0,
) -> list[dict[str, Any]]:
    if not decks:
        raise RuntimeError("尚未设置管理范围。")
    new_limit = math.ceil(additional_new / WEEK_DAYS) if additional_new else 0
    return [
        anki(
            "attentionSimulate",
            {
                "deck": deck,
                "additionalNew": additional_new,
                "days": days,
                "newLimit": new_limit,
            },
        )
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
) -> int:
    _, baseline_costs = combined_simulation(baseline, days)
    if not within_daily_budget(baseline_costs, daily_budget_seconds):
        return 0

    baseline_by_deck = [combined_simulation([result], days)[1] for result in baseline]

    def fits(candidate: int) -> bool:
        simulations = native_simulation(decks, days, candidate)
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
) -> dict[str, Any]:
    started = time.time()
    now_ms = int(time.time() * 1000)
    config = attention_config()
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
    deck_names = sorted(anki("deckNames"), key=str.casefold)
    if deck and deck not in deck_names:
        raise RuntimeError(f"牌组不存在：{deck}")

    active_90 = anki(
        "findCards",
        {"query": view_query("rated:90 -is:suspended", deck, managed_decks)},
    )
    infos = card_infos(active_90)
    reviews = get_reviews(active_90)
    capacity_reviews = reviews
    if deck or managed_decks:
        capacity_ids = anki(
            "findCards",
            {"query": managed_query("rated:90 -is:suspended", managed_decks)},
        )
        capacity_reviews = get_reviews(capacity_ids)
    summaries = [s for cid, revs in reviews.items() if (s := summarize_reviews(int(cid), revs, now_ms))]

    candidate_ids = sorted(
        {s.card_id for s in summaries if s.total_ms_90 >= 120_000 or s.slow_count >= 2 or s.total_ms_30 >= 60_000}
    )
    infos.update(card_infos([cid for cid in candidate_ids if cid not in infos]))

    today_reviews = [
        r
        for revs in reviews.values()
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
    infos.update(card_infos([cid for cid in needed_ids if cid not in infos]))

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

    due_count = query_count(view_query("is:due -is:suspended", deck, managed_decks))
    new_today = query_count(view_query("added:1 -is:suspended", deck, managed_decks))
    new_7 = query_count(view_query("added:7 -is:suspended", deck, managed_decks))
    suspended = query_count(view_query("is:suspended", deck, managed_decks))
    needs_optimization = query_count(view_query("tag:需优化卡片", deck, managed_decks))
    scope_forecast_counts = [
        query_count(view_query("is:due -is:new -is:suspended", deck, managed_decks)),
        *[
            query_count(
                view_query(
                    f"is:review prop:due={day} -is:suspended",
                    deck,
                    managed_decks,
                )
            )
            for day in range(1, WEEK_DAYS)
        ],
    ]
    forecast_counts = [
        query_count(managed_query("is:due -is:new -is:suspended", managed_decks)),
        *[
            query_count(
                managed_query(
                    f"is:review prop:due={day} -is:suspended",
                    managed_decks,
                )
            )
            for day in range(1, WEEK_DAYS)
        ],
    ]
    forecast_reviews = sum(forecast_counts)
    forecast_ms = forecast_reviews * capacity_avg_ms
    scope_forecast_reviews = sum(scope_forecast_counts)
    scope_forecast_ms = scope_forecast_reviews * scope_recent_avg_ms
    weekly_budget_ms = WEEK_DAYS * daily_budget_minutes * 60_000
    headroom_ms = weekly_budget_ms - forecast_ms
    recommended_new = 0 if headroom_ms < 0 else None
    global_new_7 = query_count(
        managed_query("added:7 -is:suspended", managed_decks)
    )
    capacity_source = "schedule_estimate"
    simulation_error = ""
    try:
        baseline_simulations = native_simulation(
            managed_decks,
            simulation_days,
        )
        native_counts, native_costs = combined_simulation(
            baseline_simulations,
            simulation_days,
        )
        forecast_counts = native_counts[:WEEK_DAYS]
        forecast_reviews = sum(forecast_counts)
        forecast_seconds = sum(native_costs[:WEEK_DAYS])
        forecast_ms = round(forecast_seconds * 1000)
        headroom_ms = weekly_budget_ms - forecast_ms
        additional_new = recommended_new_cards(
            managed_decks,
            baseline_simulations,
            simulation_days,
            daily_budget_minutes * 60,
        )
        recommended_new = (
            0 if additional_new == 0 and headroom_ms < 0
            else global_new_7 + additional_new
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

    return {
        "generated_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "runtime_s": round(time.time() - started, 1),
        "selected_deck": deck,
        "decks": deck_names,
        "title": "Attention Is All You Need",
        "subtitle": "人的注意力有限，能制造的 Anki 卡片近乎无限；两者的矛盾，就是 Anki 系统的核心风险。",
        "conclusion": conclusion,
        "today": {
            "reviews": len(today_reviews),
            "minutes": round(today_total_ms / 60000, 1),
            "avg_s": round(today_avg_ms / 1000) if today_avg_ms else 0,
            "slow_count": today_slow,
            "again_rate": round(today_again / max(len(today_reviews), 1) * 100, 1),
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
            "headroom_minutes": round(headroom_ms / 60000),
            "recent_avg_s": round(capacity_avg_ms / 1000),
            "recommended_new": recommended_new,
            "added_new": global_new_7,
            "managed_decks": managed_decks,
            "source": capacity_source,
            "simulation_days": simulation_days,
            "simulation_error": simulation_error,
        },
        "attention_holes": rows_attention,
        "quality_cards": quality_cards[:20],
        "slow_cards": rows_slow,
        "again_slow": rows_again,
    }


HTML_PATH = Path(__file__).with_name("attention_dashboard.html")
HTML = HTML_PATH.read_text(encoding="utf-8")


class Handler(BaseHTTPRequestHandler):
    def log_message(self, fmt: str, *args: Any) -> None:
        return

    def send_json(self, data: Any, status: int = 200) -> None:
        body = json.dumps(data, ensure_ascii=False).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_HEAD(self) -> None:
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.end_headers()

    def do_GET(self) -> None:
        path = urllib.parse.urlsplit(self.path).path
        if path == "/" or path.startswith("/index"):
            body = HTML.encode()
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            return
        if self.path.startswith("/api/dashboard"):
            try:
                params = urllib.parse.parse_qs(urllib.parse.urlsplit(self.path).query)
                deck = (params.get("deck") or [""])[0]
                budget_value = (params.get("budget") or [""])[0]
                budget = int(budget_value) if budget_value else None
                self.send_json(build_dashboard(deck, budget))
            except Exception as exc:
                self.send_json({"error": str(exc)}, 500)
            return
        if path == "/api/open":
            self.send_json(
                {"error": "已停用：唤起 Anki Browser 会触发原生崩溃。"},
                409,
            )
            return
        self.send_response(404)
        self.end_headers()


def main() -> None:
    server = ThreadingHTTPServer((HOST, PORT), Handler)
    print(f"attention dashboard: http://{HOST}:{PORT}")
    server.serve_forever()


if __name__ == "__main__":
    main()
