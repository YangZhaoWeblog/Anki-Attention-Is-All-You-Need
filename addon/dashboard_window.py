"""Qt/WebView host and the dashboard's asynchronous bridge contract."""

from __future__ import annotations

import json
import re
from datetime import date, timedelta
from pathlib import Path
from typing import Any, Callable
from urllib.parse import urlsplit

from aqt import mw
from aqt.operations import CollectionOp, QueryOp
from aqt.qt import QDialog, QVBoxLayout
from aqt.webview import AnkiWebView
from aqt.utils import openLink

from . import attention_dashboard, dashboard_actions, settings
from .data import AnkiData


HTML_PATH = Path(__file__).with_name("attention_dashboard.html")
BRIDGE_PREFIX = "attention:"


class DashboardWebView(AnkiWebView):
    def onEsc(self):
        self.window().close()

    def createWindow(self, window_type):
        # Anki's default creates another AnkiWebView and consumes the request.
        # Let newWindowRequested route the clicked URL to the system instead.
        return None


class DashboardWindow(QDialog):
    def __init__(self, parent, addon_module: str) -> None:
        super().__init__(parent)
        self._addon_module = addon_module
        self._closed = False
        self._generation = 0
        self._writing = False
        self._collection = mw.col
        self._profile = self._profile_name
        self.setWindowTitle("Attention Is All You Need")
        self.resize(1200, 820)
        self.web = DashboardWebView(title="Attention Is All You Need")
        self.web.set_bridge_command(self._on_bridge_cmd, self)
        self.web.page().newWindowRequested.connect(self._open_card_link)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(self.web)
        self.web.setHtml(HTML_PATH.read_text(encoding="utf-8"))

    @property
    def _profile_name(self) -> str:
        return str(getattr(mw.pm, "name", "") or "default")

    def closeEvent(self, event) -> None:
        if not self._closed:
            self._closed = True
            self._generation += 1
            try:
                self.web.cleanup()
            except Exception:
                pass
        super().closeEvent(event)
        self.deleteLater()

    def _on_bridge_cmd(self, raw: str) -> Any:
        if self._closed or mw.col is not self._collection or self._profile_name != self._profile:
            return None
        if not raw.startswith(BRIDGE_PREFIX):
            return None
        request_id = ""
        generation = self._generation
        try:
            message = json.loads(raw[len(BRIDGE_PREFIX):])
            command = str(message["command"])
            request_id = str(message["requestId"])
            generation = int(message.get("generation", self._generation))
            params = message.get("params") or {}
            if not isinstance(params, dict):
                raise ValueError("请求参数须为对象。")
            if generation < self._generation:
                return None
            self._dispatch(command, request_id, generation, params)
        except (KeyError, TypeError, ValueError, json.JSONDecodeError) as exc:
            self._reply_error(request_id, generation, "invalid_request", str(exc))
        return None

    def _dispatch(self, command: str, request_id: str, generation: int, params: dict[str, Any]) -> None:
        if command == "initialize":
            self._initialize(request_id, generation)
        elif command == "loadDashboard":
            self._load_dashboard(request_id, generation, params)
        elif command == "loadCard":
            self._query(request_id, generation, lambda data: data.card_detail(int(params["cardId"])))
        elif command == "prepareDelete":
            self._query(request_id, generation, lambda data: dashboard_actions.prepare_delete(data._col, int(params["cardId"])))
        elif command == "saveSettings":
            self._save_settings(request_id, generation, params)
        elif command in {
            "saveNote", "setSuspended", "setOptimization", "setHidden",
            "deleteNote",
        }:
            self._mutate(command, request_id, generation, params)
        else:
            self._reply_error(request_id, generation, "unsupported_command", f"不支持的命令：{command}")
        return None

    def _open_card_link(self, request) -> None:
        # Do not create a child WebView or expose the management bridge to cards.
        if self._closed or mw.col is not self._collection or self._profile_name != self._profile:
            return
        if not request.isUserInitiated():
            return
        url = request.requestedUrl().toString()
        scheme = urlsplit(url).scheme.lower()
        if scheme in {"http", "https", "obsidian"} or re.fullmatch(r"marginnote[\w.-]*", scheme):
            openLink(url)

    def _config(self) -> dict[str, Any]:
        return mw.addonManager.getConfig(self._addon_module) or {}

    def _initialize(self, request_id: str, generation: int) -> None:
        snapshot = self._config()
        profile = self._profile_name
        self._query(
            request_id,
            generation,
            lambda data: settings.initialize(data, snapshot, profile),
            config=snapshot,
        )

    def _load_dashboard(self, request_id: str, generation: int, params: dict[str, Any]) -> None:
        self._generation = max(self._generation, generation)
        deck_id = int(params["deckId"])
        budget = int(params["dailyBudgetMinutes"])

        def build(data: AnkiData) -> dict[str, Any]:
            deck_name = data.resolve_deck(deck_id)
            if deck_name is None:
                raise ValueError("所选牌组不存在，请重新选择。")
            result = attention_dashboard.build_dashboard(deck_name, budget, data)
            result["selectedDeckId"] = deck_id
            period_start = date.today()
            capacity = result["capacity"]
            capacity.update(
                deckId=deck_id,
                periodStart=period_start.isoformat(),
                periodEnd=(period_start + timedelta(days=6)).isoformat(),
                activeDays=capacity["active_days"],
                forecastMinutes=capacity["forecast_minutes"],
                budgetMinutes=capacity["weekly_budget_minutes"],
                headroomMinutes=capacity["headroom_minutes"],
                newAllowance=capacity["recommended_new"],
            )
            return result

        self._query(request_id, generation, build)

    def _query(
        self,
        request_id: str,
        generation: int,
        fn: Callable[[AnkiData], dict[str, Any]],
        *,
        config: dict[str, Any] | None = None,
    ) -> None:
        snapshot = self._config() if config is None else config

        def op(col) -> dict[str, Any]:
            return fn(AnkiData(col, snapshot))

        QueryOp(
            parent=self,
            op=op,
            success=lambda result: self._reply_ok(request_id, generation, result),
        ).failure(lambda exc: self._reply_error(request_id, generation, "query_failed", str(exc))).run_in_background()

    def _save_settings(self, request_id: str, generation: int, params: dict[str, Any]) -> None:
        try:
            current = self._config()
            updated, saved = settings.save(
                AnkiData(mw.col, current),
                current,
                self._profile_name,
                int(params["deckId"]),
                int(params["dailyBudgetMinutes"]),
            )
            mw.addonManager.writeConfig(self._addon_module, updated)
        except Exception as exc:
            self._reply_error(request_id, generation, "settings_failed", str(exc))
            return
        self._reply_ok(request_id, generation, saved)

    def _mutate(self, command: str, request_id: str, generation: int, params: dict[str, Any]) -> None:
        if self._writing:
            self._reply_error(request_id, generation, "write_in_progress", "另一个写操作仍在执行。")
            return
        calls = {
            "saveNote": lambda col: dashboard_actions.save_note(col, int(params["noteId"]), dict(params.get("changedFields") or {}), list(params.get("tags") or [])),
            "setSuspended": lambda col: dashboard_actions.set_suspended(col, int(params["cardId"]), bool(params["suspended"])),
            "setOptimization": lambda col: dashboard_actions.set_optimization(col, int(params["cardId"]), bool(params["enabled"])),
            "setHidden": lambda col: dashboard_actions.set_hidden(col, int(params["cardId"]), bool(params["enabled"])),
            "deleteNote": lambda col: dashboard_actions.delete_note(col, int(params["noteId"]), list(params["confirmedCardIds"])),
        }
        self._writing = True

        def done(result) -> None:
            self._writing = False
            self._reply_ok(request_id, generation, result.payload)

        def failed(exc: Exception) -> None:
            self._writing = False
            self._reply_error(request_id, generation, "write_failed", str(exc))

        CollectionOp(parent=self, op=calls[command]).success(done).failure(failed).run_in_background()

    def _reply_ok(self, request_id: str, generation: int, result: dict[str, Any]) -> None:
        self._reply({"requestId": request_id, "generation": generation, "result": result})

    def _reply_error(self, request_id: str, generation: int, code: str, message: str) -> None:
        self._reply({"requestId": request_id, "generation": generation, "error": {"code": code, "message": message}})

    def _reply(self, payload: dict[str, Any]) -> None:
        if self._closed or mw.col is not self._collection or self._profile_name != self._profile:
            return
        generation = int(payload.get("generation", self._generation))
        if generation < self._generation:
            return
        encoded = json.dumps(payload, ensure_ascii=False).replace("</", "<\\/")
        self.web.eval(f"window.__attentionResponse({encoded});")
