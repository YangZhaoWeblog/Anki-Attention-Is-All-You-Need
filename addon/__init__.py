from __future__ import annotations

import importlib
import threading
from typing import Any

from anki.scheduler_pb2 import SimulateFsrsReviewRequest
from aqt import gui_hooks, mw
from aqt.qt import QAction
from aqt.utils import openLink, showWarning

from . import attention_dashboard


_server = None
_server_thread: threading.Thread | None = None


def addon_config() -> dict[str, Any]:
    return mw.addonManager.getConfig(__name__) or {}


def managed_decks() -> list[str]:
    available = {item.name for item in mw.col.decks.all_names_and_ids()} if mw.col else set()
    configured = addon_config().get("managedDecks") or []
    return [name for name in configured if name in available]


def on_main(function):
    completed = threading.Event()
    result: dict[str, Any] = {}

    def run() -> None:
        try:
            result["value"] = function()
        except Exception as exc:
            result["error"] = exc
        finally:
            completed.set()

    mw.taskman.run_on_main(run)
    if not completed.wait(120):
        raise TimeoutError("Anki 主线程响应超时。")
    if "error" in result:
        raise result["error"]
    return result.get("value")


def direct_anki(action: str, params: dict[str, Any]):
    def execute():
        collection = mw.col
        if collection is None:
            raise RuntimeError("Anki 集合尚未打开。")
        if action == "deckNames":
            return [item.name for item in collection.decks.all_names_and_ids()]
        if action == "findCards":
            return list(collection.find_cards(params.get("query", "")))
        if action == "cardsInfo":
            result = []
            for card_id in params.get("cards") or []:
                card = collection.get_card(int(card_id))
                note = card.note()
                fields = {
                    name: {"value": value, "order": index}
                    for index, (name, value) in enumerate(note.items())
                }
                result.append(
                    {
                        "cardId": card.id,
                        "fields": fields,
                        "deckName": collection.decks.name_if_exists(card.did) or "",
                        "tags": list(note.tags),
                    }
                )
            return result
        if action == "getReviewsOfCards":
            card_ids = [int(card_id) for card_id in params.get("cards") or []]
            result = {str(card_id): [] for card_id in card_ids}
            if not card_ids:
                return result
            placeholders = ",".join("?" for _ in card_ids)
            rows = collection.db.all(
                f"select id, cid, ease, time from revlog where cid in ({placeholders})",
                *card_ids,
            )
            for review_id, card_id, ease, review_time in rows:
                result[str(card_id)].append(
                    {
                        "id": review_id,
                        "cardId": card_id,
                        "ease": ease,
                        "time": review_time,
                    }
                )
            return result
        if action == "attentionConfig":
            config = addon_config()
            return {
                "managedDecks": managed_decks(),
                "simulationDays": int(config.get("simulationDays", 90)),
                "dailyBudgetMinutes": int(config.get("dailyBudgetMinutes", 30)),
            }
        if action == "attentionSimulate":
            request = simulator_request(
                params["deck"],
                params.get("additionalNew", 0),
                params.get("days", 90),
                params.get("newLimit", 0),
            )
            simulation = collection._backend.simulate_fsrs_review(request)
            return {
                "dailyReviewCount": list(simulation.daily_review_count),
                "dailyNewCount": list(simulation.daily_new_count),
                "dailyTimeCost": list(simulation.daily_time_cost),
            }
        raise RuntimeError(f"不支持的数据操作：{action}")

    return on_main(execute)


def start_dashboard() -> str:
    global _server, _server_thread
    if _server is None:
        attention_dashboard.set_anki_caller(direct_anki)
        preferred = int(addon_config().get("dashboardPort", 8766))
        for port in range(preferred, preferred + 20):
            try:
                _server = attention_dashboard.ThreadingHTTPServer(
                    ("127.0.0.1", port),
                    attention_dashboard.Handler,
                )
                break
            except OSError:
                continue
        if _server is None:
            raise RuntimeError("无法找到可用的本地端口。")
        _server_thread = threading.Thread(
            target=_server.serve_forever,
            name="attention-dashboard",
            daemon=True,
        )
        _server_thread.start()
    return f"http://127.0.0.1:{_server.server_port}"


def open_dashboard() -> None:
    try:
        openLink(start_dashboard())
    except Exception as exc:
        showWarning(f"Attention Is All You Need 启动失败：{exc}")


def stop_dashboard() -> None:
    global _server, _server_thread
    server = _server
    _server = None
    _server_thread = None
    if server is not None:
        def shutdown() -> None:
            server.shutdown()
            server.server_close()

        threading.Thread(target=shutdown, daemon=True).start()


def add_toolbar_link(links: list[str], toolbar) -> None:
    links.append(
        toolbar.create_link(
            "attention-dashboard",
            "Attention",
            open_dashboard,
            tip="Open Attention Is All You Need",
            id="attention-dashboard",
        )
    )


def simulator_request(deck: str, additional_new: int, days: int, new_limit: int):
    collection = mw.col
    deck_id = collection.decks.id_for_name(deck)
    if deck_id is None:
        raise ValueError(f"牌组不存在：{deck}")
    config = collection.decks.config_dict_for_deck_id(deck_id)
    params = (
        config.get("fsrsParams6")
        or config.get("fsrsParams5")
        or config.get("fsrsWeights")
        or []
    )
    if not params:
        raise ValueError(f"牌组没有可用的 FSRS 参数：{deck}")
    request = SimulateFsrsReviewRequest(
        params=params,
        desired_retention=float(config.get("desiredRetention", 0.9)),
        deck_size=max(0, int(additional_new)),
        days_to_simulate=max(1, int(days)),
        new_limit=max(0, int(new_limit)),
        review_limit=int(config.get("rev", {}).get("perDay", 9999)),
        max_interval=int(config.get("rev", {}).get("maxIvl", 36500)),
        search=f'deck:"{deck}" -is:suspended -is:new',
        new_cards_ignore_review_limit=False,
        easy_days_percentages=config.get("easyDaysPercentages") or [1.0] * 7,
        review_order=int(config.get("reviewOrder", 0)),
        historical_retention=float(config.get("sm2Retention", 0.9)),
        learning_step_count=len(config.get("new", {}).get("delays") or []),
        relearning_step_count=len(config.get("lapse", {}).get("delays") or []),
    )
    if int(config.get("lapse", {}).get("leechAction", 1)) == 0:
        request.suspend_after_lapse_count = int(
            config.get("lapse", {}).get("leechFails", 8)
        )
    return request


def install_anki_connect_bridge() -> None:
    try:
        anki_connect = importlib.import_module("2055492159")
    except Exception:
        return
    if hasattr(anki_connect.AnkiConnect, "attentionSimulate"):
        return

    @anki_connect.util.api()
    def attentionConfig(self):
        config = addon_config()
        return {
            "managedDecks": managed_decks(),
            "simulationDays": int(config.get("simulationDays", 90)),
            "dailyBudgetMinutes": int(config.get("dailyBudgetMinutes", 30)),
        }

    @anki_connect.util.api()
    def attentionSimulate(
        self,
        deck,
        additionalNew=0,
        days=90,
        newLimit=0,
    ):
        request = simulator_request(deck, additionalNew, days, newLimit)
        result = mw.col._backend.simulate_fsrs_review(request)
        return {
            "dailyReviewCount": list(result.daily_review_count),
            "dailyNewCount": list(result.daily_new_count),
            "dailyTimeCost": list(result.daily_time_cost),
        }

    @anki_connect.util.api()
    def attentionStartDashboard(self):
        return start_dashboard()

    setattr(anki_connect.AnkiConnect, "attentionConfig", attentionConfig)
    setattr(anki_connect.AnkiConnect, "attentionSimulate", attentionSimulate)
    setattr(
        anki_connect.AnkiConnect,
        "attentionStartDashboard",
        attentionStartDashboard,
    )


action = QAction("Attention Is All You Need", mw)
action.triggered.connect(open_dashboard)
mw.form.menuTools.addAction(action)

gui_hooks.top_toolbar_did_init_links.append(add_toolbar_link)
gui_hooks.profile_did_open.append(install_anki_connect_bridge)
gui_hooks.profile_will_close.append(stop_dashboard)

if mw.col:
    install_anki_connect_bridge()
