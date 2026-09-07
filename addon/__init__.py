from __future__ import annotations

from aqt import gui_hooks, mw
from aqt.qt import QAction
from aqt.utils import showWarning

from .dashboard_window import DashboardWindow


_window: "DashboardWindow | None" = None


def open_dashboard() -> None:
    global _window
    try:
        # Reuse the live window; only recreate once the previous one was
        # closed (and thus had its WebView cleaned up in closeEvent).
        if _window is None or getattr(_window, "_closed", True):
            _window = DashboardWindow(mw, addon_module=__name__)
        _window.show()
        _window.raise_()
        _window.activateWindow()
    except Exception as exc:
        showWarning(f"Attention Is All You Need 启动失败：{exc}")


def close_dashboard() -> None:
    # Profile is closing: tear down the window so its WebView doesn't fire
    # late bridge events against a dead collection.
    global _window
    win = _window
    _window = None
    if win is not None and not getattr(win, "_closed", True):
        win.close()


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


action = QAction("Attention Is All You Need", mw)
action.triggered.connect(open_dashboard)
mw.form.menuTools.addAction(action)

gui_hooks.top_toolbar_did_init_links.append(add_toolbar_link)
gui_hooks.profile_will_close.append(close_dashboard)
