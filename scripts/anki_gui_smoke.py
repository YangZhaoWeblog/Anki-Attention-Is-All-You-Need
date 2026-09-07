#!/usr/bin/env python3
"""Launch the add-on in an isolated, offscreen Anki profile and probe its WebView."""

from __future__ import annotations

import json
import os
from pathlib import Path
import subprocess
import shutil
import sys
import tempfile
import time

from anki.collection import Collection
from aqt.profiles import ProfileManager


ROOT = Path(__file__).parents[1]
PROFILE = "Dashboard QA"
HELPER = r'''
from __future__ import annotations
import json
import os
from pathlib import Path
from aqt import gui_hooks, mw
from aqt.qt import QTimer

OUT = Path(os.environ["ATTENTION_QA_OUTPUT"])
attempts = 0

def finish(payload):
    OUT.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    QTimer.singleShot(0, mw.app.quit)

def probe_after_write(before):
    import attention_is_all_you_need as addon
    win = addon._window
    script = """JSON.stringify({
      candidateCountAfterWrite: state.dashboard && state.dashboard.candidate_counts.reviewTotal,
      writeFeedbackVisible: document.body.innerText.includes('已隐藏整条笔记'),
      bodyTextAfterWrite: document.body.innerText.slice(0, 500)
    })"""
    def completed(raw):
        after = json.loads(raw)
        note = mw.col.get_note(before["noteId"])
        finish({**before, **after, "tagWritten": "看板忽略" in note.tags})
    win.web.page().runJavaScript(script, completed)

def probe_detail():
    import attention_is_all_you_need as addon
    win = addon._window
    script = """JSON.stringify({
      title: document.title,
      hasDashboard: !!state.dashboard,
      selectedDeckId: state.init && state.init.selectedDeckId,
      candidateCount: state.dashboard && state.dashboard.candidate_counts.reviewTotal,
      hasDetail: !!state.detail,
      noteId: state.detail && state.detail.noteId,
      hasRenderedCard: !!document.querySelector('.rendered-card'),
      bodyText: document.body.innerText.slice(0, 600)
    })"""
    def completed(raw):
        before = json.loads(raw)
        win.web.page().runJavaScript(
            "document.querySelector('[data-hide-open]').click();"
            "document.querySelector('[data-hide-confirm]').click();true",
            lambda _: QTimer.singleShot(1800, lambda: probe_after_write(before)),
        )
    win.web.page().runJavaScript(script, completed)

def open_cards():
    import attention_is_all_you_need as addon
    win = addon._window
    win.web.page().runJavaScript(
        "document.querySelector('[data-open-cards]')?.click(); true",
        lambda _: QTimer.singleShot(1800, probe_detail),
    )

def probe_dashboard():
    global attempts
    attempts += 1
    import attention_is_all_you_need as addon
    win = addon._window
    win.web.page().runJavaScript(
        "!!state.dashboard",
        lambda ready: open_cards() if ready else (
            QTimer.singleShot(250, probe_dashboard)
            if attempts < 80 else finish({"error": "dashboard timeout"})
        ),
    )

def start():
    import attention_is_all_you_need as addon
    addon.open_dashboard()
    QTimer.singleShot(250, probe_dashboard)

gui_hooks.profile_did_open.append(lambda: QTimer.singleShot(0, start))
'''


def create_fixture(base: Path) -> None:
    manager = ProfileManager(base)
    manager.setupMeta()
    manager.meta["firstRun"] = False
    manager.meta["defaultLang"] = "en_US"
    manager.create(PROFILE)
    manager.set_last_loaded_profile_name(PROFILE)
    manager.load(PROFILE)
    manager.save()
    profile_dir = base / PROFILE
    profile_dir.mkdir(parents=True, exist_ok=True)
    col = Collection(str(profile_dir / "collection.anki2"))
    try:
        model = col.models.by_name("Basic")
        note = col.new_note(model)
        note["Front"] = "<b>隔离看板题面</b>" + "用于结构疑点验证" * 30
        note["Back"] = '<img src="missing-smoke.png">隔离看板答案'
        col.add_note(note, col.decks.id_for_name("Default"))
        card_id = int(note.card_ids()[0])
        now = int(time.time() * 1000)
        for review_index, duration in enumerate((40_000, 41_000)):
            col.db.execute(
                "insert into revlog (id,cid,usn,ease,ivl,lastIvl,factor,time,type) values (?,?,?,?,?,?,?,?,?)",
                now + review_index,
                card_id,
                -1,
                3,
                1,
                0,
                0,
                duration,
                1,
            )
    finally:
        col.close()
    addons = base / "addons21"
    addons.mkdir(parents=True, exist_ok=True)
    shutil.copytree(ROOT / "addon", addons / "attention_is_all_you_need",
                    ignore=shutil.ignore_patterns("__pycache__", "*.pyc", "meta.json"))
    helper = addons / "attention_gui_smoke"
    helper.mkdir()
    (helper / "__init__.py").write_text(HELPER, encoding="utf-8")


def main() -> int:
    with tempfile.TemporaryDirectory(prefix="attention-gui-") as folder:
        base = Path(folder)
        create_fixture(base)
        output = base / "result.json"
        code = (
            "import sys,aqt;"
            f"aqt.AnkiApp.KEY={('attention-smoke-' + base.name)!r};"
            f"sys.argv=['anki','-b',{str(base)!r},'-p',{PROFILE!r}];"
            "aqt.run()"
        )
        env = dict(os.environ)
        env["ATTENTION_QA_OUTPUT"] = str(output)
        env.setdefault("QT_QPA_PLATFORM", "offscreen")
        completed = subprocess.run(
            [sys.executable, "-c", code],
            env=env,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            timeout=45,
        )
        if not output.exists():
            print(completed.stdout)
            raise SystemExit("Anki GUI smoke did not produce a result")
        result = json.loads(output.read_text(encoding="utf-8"))
        print(json.dumps(result, ensure_ascii=False, indent=2))
        required = (
            result.get("title") == "Attention Is All You Need"
            and result.get("hasDashboard")
            and result.get("selectedDeckId") is not None
            and result.get("candidateCount") == 1
            and result.get("hasDetail")
            and result.get("hasRenderedCard")
            and "隔离看板题面" in result.get("bodyText", "")
            and result.get("candidateCountAfterWrite") == 0
            and result.get("tagWritten")
            and result.get("writeFeedbackVisible")
        )
        return 0 if required and completed.returncode == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
