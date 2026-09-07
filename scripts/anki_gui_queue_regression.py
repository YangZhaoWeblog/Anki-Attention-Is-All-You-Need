#!/usr/bin/env python3
"""Verify that processing a middle candidate continues with its successor."""

from __future__ import annotations

import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile

from anki.collection import Collection
from aqt.profiles import ProfileManager


ROOT = Path(__file__).parents[1]
PROFILE = "Dashboard Queue QA"
HELPER = r'''
from __future__ import annotations
import json
import os
from pathlib import Path
from aqt import gui_hooks, mw
from aqt.qt import QTimer

OUT = Path(os.environ["ATTENTION_QA_OUTPUT"])
IDS = json.loads(Path(os.environ["ATTENTION_QA_FIXTURE"]).read_text())
attempts = 0

def finish(payload):
    note = mw.col.get_note(IDS["noteB"])
    payload["hiddenTagWritten"] = "看板忽略" in note.tags
    OUT.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    QTimer.singleShot(0, mw.app.quit)

def page():
    import attention_is_all_you_need as addon
    return addon._window.web.page()

def poll():
    global attempts
    attempts += 1
    expression = """JSON.stringify({
      ready: !!state.dashboard && !state.busy,
      selected: state.selected,
      remaining: state.dashboard ? state.dashboard.review_queue.map(row=>row.cardId) : []
    })"""
    def checked(raw):
        data = json.loads(raw)
        if data["ready"] and IDS["cardB"] not in data["remaining"]:
            finish({
                "middleCardContinuesNext": data["selected"] == IDS["cardC"],
                "selectedCardId": data["selected"],
                "expectedCardId": IDS["cardC"],
            })
        elif attempts < 100:
            QTimer.singleShot(100, poll)
        else:
            finish({"timeout": True})
    page().runJavaScript(expression, checked)

def hide_middle():
    page().runJavaScript(
        "mutate('setHidden',{cardId:" + str(IDS["cardB"]) + ",enabled:true},'middle hidden');true",
        lambda _: QTimer.singleShot(100, poll),
    )

def select_middle():
    expression = (
        "state.view='cards';state.queue='review';state.filter='all';"
        "state.selected=" + str(IDS["cardB"]) + ";state.detail=null;"
        "render();loadCard(state.selected,{force:true});true"
    )
    page().runJavaScript(expression, lambda _: QTimer.singleShot(500, hide_middle))

def wait_dashboard():
    page().runJavaScript(
        "!!state.dashboard",
        lambda ready: select_middle() if ready else QTimer.singleShot(100, wait_dashboard),
    )

def start():
    import attention_is_all_you_need as addon
    addon.open_dashboard()
    QTimer.singleShot(100, wait_dashboard)

gui_hooks.profile_did_open.append(lambda: QTimer.singleShot(0, start))
'''


def fixture(base: Path) -> dict[str, int]:
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
    ids = {}
    try:
        model = col.models.by_name("Basic")
        for key in ("A", "B", "C", "D"):
            note = col.new_note(model)
            note["Front"] = "Candidate " + key + " " + "long structural text " * 20
            note["Back"] = "Answer " + key
            col.add_note(note, col.decks.id_for_name("Default"))
            ids["note" + key] = int(note.id)
            ids["card" + key] = int(note.card_ids()[0])
        now = int(__import__("time").time() * 1000)
        for offset, key in enumerate(("A", "B", "C", "D")):
            for review_index, duration in enumerate((40_000, 41_000)):
                col.db.execute(
                    "insert into revlog (id,cid,usn,ease,ivl,lastIvl,factor,time,type) values (?,?,?,?,?,?,?,?,?)",
                    now + offset * 10 + review_index,
                    ids["card" + key],
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
    return ids


def main() -> int:
    with tempfile.TemporaryDirectory(prefix="attention-queue-") as folder:
        base = Path(folder)
        ids = fixture(base)
        output = base / "result.json"
        fixture_path = base / "fixture.json"
        fixture_path.write_text(json.dumps(ids), encoding="utf-8")
        addons = base / "addons21"
        addons.mkdir(parents=True, exist_ok=True)
        shutil.copytree(
            ROOT / "addon",
            addons / "attention_is_all_you_need",
            ignore=shutil.ignore_patterns("__pycache__", "*.pyc", "meta.json"),
        )
        helper = addons / "attention_gui_queue_regression"
        helper.mkdir()
        (helper / "__init__.py").write_text(HELPER, encoding="utf-8")
        env = dict(os.environ)
        env["ATTENTION_QA_OUTPUT"] = str(output)
        env["ATTENTION_QA_FIXTURE"] = str(fixture_path)
        env.setdefault("QT_QPA_PLATFORM", "offscreen")
        env.setdefault("QTWEBENGINE_CHROMIUM_FLAGS", "--disable-gpu")
        code = (
            "import sys,aqt;"
            f"aqt.AnkiApp.KEY={('attention-queue-' + base.name)!r};"
            f"sys.argv=['anki','-b',{str(base)!r},'-p',{PROFILE!r}];"
            "aqt.run()"
        )
        completed = subprocess.run(
            [sys.executable, "-c", code],
            env=env,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            timeout=30,
        )
        if not output.exists():
            print(completed.stdout)
            return 1
        result = json.loads(output.read_text(encoding="utf-8"))
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0 if (
            completed.returncode == 0
            and result.get("middleCardContinuesNext")
            and result.get("hiddenTagWritten")
        ) else 1


if __name__ == "__main__":
    raise SystemExit(main())
