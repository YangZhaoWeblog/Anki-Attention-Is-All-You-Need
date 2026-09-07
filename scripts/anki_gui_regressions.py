#!/usr/bin/env python3
"""Deterministic WebView regressions for dashboard editing and interaction state."""

from __future__ import annotations

import json
import base64
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
PROFILE = "Dashboard Regression QA"
HELPER = r'''
from __future__ import annotations
import json
import os
from pathlib import Path
from aqt import gui_hooks, mw
from aqt.qt import QTimer

OUT = Path(os.environ["ATTENTION_QA_OUTPUT"])
IDS = json.loads(Path(os.environ["ATTENTION_QA_FIXTURE"]).read_text())
result = {}
attempts = {}
finished = False
original_suspend = None

def finish():
    global finished
    if finished:
        return
    finished = True
    result["crossNoteSafe"] = mw.col.get_note(IDS["noteB"])["Front"] != "DRAFT_FROM_A"
    result["savedContinue"] = mw.col.get_note(IDS["noteC"])["Front"] == "SAVED_C"
    OUT.write_text(json.dumps(result, ensure_ascii=False), encoding="utf-8")
    QTimer.singleShot(0, mw.app.quit)

def page():
    import attention_is_all_you_need as addon
    return addon._window.web.page()

def capture(name):
    folder = os.environ.get('ATTENTION_QA_SCREENSHOTS')
    if folder:
        import attention_is_all_you_need as addon
        addon._window.grab().save(str(Path(folder) / (name + '.png')))

def poll(name, expression, callback, limit=80):
    attempts[name] = attempts.get(name, 0) + 1
    def checked(value):
        if value:
            callback()
        elif attempts[name] < limit:
            QTimer.singleShot(100, lambda: poll(name, expression, callback, limit))
        else:
            result[name + "Timeout"] = True
            finish()
    page().runJavaScript(expression, checked)

def check_stale_detail():
    page().runJavaScript(
        """JSON.stringify({
          staleDetailIgnored: state.detail && state.detail.noteId===""" + str(IDS["noteB"]) + """,
          staleFailureDidNotReplacePage: !document.querySelector('.error')
        })""",
        lambda raw: (result.update(json.loads(raw)), check_settings()),
    )

def check_settings():
    page().runJavaScript(r"""
      (async()=>{
        const wait=ms=>new Promise(resolve=>setTimeout(resolve,ms));
        const until=async fn=>{for(let i=0;i<100;i++){if(fn())return;await wait(50);}throw Error('settings timeout');};
        const report={}, originalSend=send, oldBudget=state.init.dailyBudgetMinutes;
        try {
          send=(command,params,gen)=>command==='saveSettings' ? originalSend(command,params,gen).then(value=>new Promise(resolve=>setTimeout(()=>resolve(value),350))) : originalSend(command,params,gen);
          saveSettings(state.init.selectedDeckId,oldBudget+1);
          startEditing(false);
          state.draft.fields.Front='UNSAVED_DURING_SETTINGS';render();
          report.editingAllowedDuringSettings=state.editing && document.querySelector('#scope').disabled;
          await until(()=>!!document.querySelector('#unsaved')?.open);
          document.querySelector('[data-keep]').click();
          report.settingsPreserveDraft=state.draft.fields.Front==='UNSAVED_DURING_SETTINGS' && state.init.dailyBudgetMinutes===oldBudget;
          document.querySelector('[data-cancel]').click();document.querySelector('[data-discard]').click();
          await until(()=>!state.busy && !state.settingsBusy && !!state.detail);
          report.settingsAppliedAfterDiscard=state.init.dailyBudgetMinutes===oldBudget+1;
          saveSettings(state.init.selectedDeckId,oldBudget+2);
          document.querySelector('[data-hide-open]').click();
          await until(()=>!!pendingSettings);
          document.querySelector('[data-hide-cancel]').click();
          await until(()=>!state.busy && !state.settingsBusy && !!state.detail);
          report.settingsAppliedAfterDialogCancel=state.init.dailyBudgetMinutes===oldBudget+2;
          send=(command,params,gen)=>command==='saveSettings' ? Promise.reject(new Error('injected config failure')) : originalSend(command,params,gen);
          await saveSettings(state.init.selectedDeckId,oldBudget+3);
          report.settingsFailureRestoresControls=state.init.dailyBudgetMinutes===oldBudget+2 && Number(document.querySelector('#budget').value)===oldBudget+2 && !state.settingsBusy;
          await until(()=>document.querySelector('iframe')?.contentDocument?.querySelector('[data-card-content]'));
          const frame=document.querySelector('iframe');
          const content=frame.contentDocument.querySelector('[data-card-content]');
          frame.contentDocument.body.style.padding='10px';
          content.innerHTML='<div style="height:100vh">viewport-sized card</div>';
          const samples=[];
          for(let i=0;i<8;i++){await wait(50);samples.push(frame.clientHeight);}
          report.viewportHeightSamples=samples;
          report.viewportHeightStable=Math.max(...samples)-Math.min(...samples)<=1;
        } catch(error){report.settingsTestError=String(error)}
        finally{send=originalSend;window.__settingsReport=JSON.stringify(report)}
      })();true
    """, lambda _: poll('settingsCheck', '!!window.__settingsReport', collect_settings))

def collect_settings():
    page().runJavaScript('window.__settingsReport', lambda raw: (result.update(json.loads(raw)), check_narrow()))

def check_narrow():
    import attention_is_all_you_need as addon
    addon._window.resize(680, 820)
    def checked(raw):
        result.update(json.loads(raw))
        check_window_boundaries()
    QTimer.singleShot(500, lambda: page().runJavaScript("""JSON.stringify({
      narrowArrowsVisible: [...document.querySelectorAll('[data-step]')].every(b=>!b.hidden && b.getBoundingClientRect().width>0),
      narrowToggleAvailable: document.querySelector('#collapse').getBoundingClientRect().width>0
    })""", checked))

def check_window_boundaries():
    import attention_is_all_you_need as addon
    from unittest.mock import patch
    win = addon._window
    replies = []
    with patch.object(win, '_reply', side_effect=replies.append):
        win._on_bridge_cmd('attention:' + json.dumps({'command':'loadDashboard','requestId':'bad-params','generation':win._generation,'params':{}}))
    result['invalidParamsReplied'] = bool(replies and replies[0]['requestId']=='bad-params' and replies[0]['error']['code']=='invalid_request')
    with patch.object(win, '_dispatch') as dispatch, patch.object(win.web, 'eval') as output:
        win._closed = True
        win._on_bridge_cmd('attention:' + json.dumps({'command':'setHidden','requestId':'closed','generation':win._generation,'params':{'cardId':IDS['cardB'],'enabled':True}}))
        win._reply_ok('late', win._generation, {})
        result['closedGate'] = not dispatch.called and not output.called
        win._closed = False
        with patch.object(mw.pm, 'name', 'Different Profile'):
            win._on_bridge_cmd('attention:' + json.dumps({'command':'initialize','requestId':'profile','generation':win._generation}))
            win._reply_ok('late-profile', win._generation, {})
            result['profileGate'] = not dispatch.called and not output.called
    win.close()
    win._reply_ok('after-real-close', win._generation, {})
    result['actualCloseGate'] = win._closed
    finish()

def trigger_stale_detail():
    script = """
      window.__qaOriginalSend=send;
      send=function(command,params,gen){
        if(command==='loadCard' && params.cardId===""" + str(IDS["cardA"]) + """){
          return new Promise((resolve,reject)=>setTimeout(()=>reject(new Error('stale detail failure')),350));
        }
        return window.__qaOriginalSend(command,params,gen);
      };
      state.view='cards';
      loadCard(""" + str(IDS["cardA"]) + """,{force:true});
      setTimeout(()=>loadCard(""" + str(IDS["cardB"]) + """,{force:true}),20);
      setTimeout(()=>{send=window.__qaOriginalSend;},700);
      true
    """
    page().runJavaScript(
        script,
        lambda _: poll(
            "staleTarget",
            "!!state.detail && state.detail.noteId===" + str(IDS["noteB"]),
            lambda: QTimer.singleShot(500, check_stale_detail),
        ),
    )

def check_partial_failure():
    global original_suspend
    if original_suspend is not None:
        type(mw.col.sched).suspend_cards = original_suspend
        original_suspend = None
    note = mw.col.get_note(IDS["noteD"])
    card = mw.col.get_card(IDS["cardD"])
    script = f"""JSON.stringify({{
      partialFailureMessage: document.body.innerText.includes('已按 Anki 实际状态刷新'),
      partialStateReloaded: state.dashboard.optimization_queue.some(row=>row.cardId==={IDS["cardD"]})
    }})"""
    def checked(raw):
        result.update(json.loads(raw))
        result["partialTagVisible"] = "需优化卡片" in note.tags
        result["partialCardUnsuspended"] = int(card.queue) != -1
        page().runJavaScript(
            "(()=>{const queue=state.queue,filter=state.filter;state.queue='optimization';state.filter='slow';const visible=currentRows().some(row=>row.cardId===" +
            str(IDS['cardD']) + ");state.queue=queue;state.filter=filter;return visible;})()",
            lambda visible: (result.update({'optimizationIgnoresSignals':bool(visible)}), trigger_stale_detail()),
        )
    page().runJavaScript(script, checked)

def inject_partial_failure():
    global original_suspend
    scheduler_type = type(mw.col.sched)
    original_suspend = scheduler_type.suspend_cards
    def fail_suspend(scheduler, card_ids):
        raise RuntimeError("injected suspend failure")
    scheduler_type.suspend_cards = fail_suspend
    page().runJavaScript(
        "document.querySelector('[data-optimize]').click();true",
        lambda _: QTimer.singleShot(2200, check_partial_failure),
    )

def open_partial_failure_card():
    page().runJavaScript(
        "state.view='cards';state.filter='all';state.queue='review';state.selected=" + str(IDS["cardD"]) +
        ";state.detail=null;render();loadCard(state.selected,{force:true});true",
        lambda _: poll("detailD", "!!state.detail && state.detail.noteId===" + str(IDS["noteD"]), inject_partial_failure),
    )

def check_save_continue():
    result["step"] = "check_save_continue"
    page().runJavaScript(
        """JSON.stringify({
          unlockedAfterSaveContinue: !state.busy && !!document.querySelector('#refresh') && !document.querySelector('#refresh').disabled,
          continuedToOverview: state.view === 'overview'
        })""",
        lambda raw: (result.update(json.loads(raw)), open_partial_failure_card()),
    )

def save_and_continue():
    result["step"] = "save_and_continue"
    script = """(()=>{
      document.querySelector('[data-edit]')?.click();
      const field=document.querySelector('[data-field]');
      if(field){field.value='SAVED_C';field.dispatchEvent(new Event('input',{bubbles:true}));}
      document.querySelector('[data-view="overview"]')?.click();
      setTimeout(()=>document.querySelector('[data-save-next]')?.click(),50);
      return JSON.stringify({
        editFieldFound: !!field,
        editingStarted: state.editing,
        draftDirty: !!state.draft && editValues().dirty,
        unsavedOpened: !!document.querySelector('#unsaved')?.open
      });
    })()
    """
    def started(raw):
        if raw:
            result.update(json.loads(raw))
        QTimer.singleShot(2200, check_save_continue)
    page().runJavaScript(script, started)

def check_cross_note():
    result["step"] = "check_cross_note"
    result["crossNoteSafe"] = mw.col.get_note(IDS["noteB"])["Front"] != "DRAFT_FROM_A"
    page().runJavaScript(
        "state.view='cards';state.filter='all';state.selected=" + str(IDS["cardC"]) + ";state.detail=null;render();loadCard(state.selected);true",
        lambda _: poll("detailC", "!!state.detail && state.detail.noteId===" + str(IDS["noteC"]), save_and_continue),
    )

def after_refresh():
    result["step"] = "after_refresh"
    def checked(dialog_open):
        result["refreshGuardedDraft"] = bool(dialog_open)
        if dialog_open:
            page().runJavaScript(
                "document.querySelector('[data-discard]')?.click(); true",
                lambda _: QTimer.singleShot(1800, check_cross_note),
            )
        else:
            page().runJavaScript(
                "document.querySelector('#editor')?.requestSubmit(); true",
                lambda _: QTimer.singleShot(1800, check_cross_note),
            )
    page().runJavaScript("!!document.querySelector('#unsaved')?.open", checked)

def draft_a():
    result["step"] = "draft_a"
    def edited(note_id):
        note = mw.col.get_note(int(note_id))
        note.add_tag("看板忽略")
        mw.col.update_note(note)
        page().runJavaScript(
            "document.querySelector('#refresh')?.click(); true",
            lambda _: QTimer.singleShot(1500, after_refresh),
        )
    script = """
      document.querySelector('[data-edit]')?.click();
      const field=document.querySelector('[data-field]');
      field.value='DRAFT_FROM_A';
      field.dispatchEvent(new Event('input',{bubbles:true}));
      state.detail.noteId
    """
    page().runJavaScript(script, edited)

def restore_all_filter():
    result["step"] = "restore_all_filter"
    page().runJavaScript(
        "state.filter='all';state.selected=currentRows()[0]?.cardId??null;render();if(state.selected)loadCard(state.selected);true",
        lambda _: poll("detailRestored", "!!state.detail", draft_a),
    )

def check_empty_filter():
    result["step"] = "check_empty_filter"
    script = """JSON.stringify({
      filterControlsRemain: document.querySelectorAll('[data-filter]').length === 4,
      emptyStateVisible: document.body.innerText.includes('当前筛选没有卡片')
    })"""
    page().runJavaScript(script, lambda raw: (result.update(json.loads(raw)), restore_all_filter()))

def detail_checks():
    result["step"] = "detail_checks"
    capture('inspection')
    script = """(()=>{
      try {
      const frame=document.querySelector('.rendered-card');
      const processMenu=document.querySelector('[data-process-menu]');
      if(processMenu)processMenu.open=true;
      let cardCssApplied=false;
      try {
        const card=frame.contentDocument.querySelector('.card');
        cardCssApplied=getComputedStyle(card).fontSize==='39px';
      } catch (_) {}
      const detail=document.querySelector('.detail').getBoundingClientRect();
      const prev=document.querySelector('.nav-prev').getBoundingClientRect();
      const next=document.querySelector('.nav-next').getBoundingClientRect();
      const stickyControls=getComputedStyle(document.querySelector('.inspection-heading')).position === 'sticky';
      const listScrollIndependent=getComputedStyle(document.querySelector('.inspection-list')).overflowY === 'auto';
      const eventDate=new Date(state.dashboard.review_queue[0].recent_reviews[0].reviewedAt*1000);
      const expectedDate=eventDate.getFullYear()+'-'+String(eventDate.getMonth()+1).padStart(2,'0')+'-'+String(eventDate.getDate()).padStart(2,'0');
      const reviewDatesVisible=document.querySelector('.review-events').innerText.includes(expectedDate);
      const sortPresent=!!document.querySelector('#sort');
      const handlePresent=document.querySelector('#collapse')?.classList.contains('is-handle');
      const menuItemsHaveRoles=document.querySelectorAll('[role="menuitem"]').length>=5;
      const frameBody=frame.contentDocument.body;
      const frameFitsContent=frame.clientHeight>=frameBody.scrollHeight;
      const image=frame.contentDocument.querySelector('img');
      const realImageLoaded=!!image && image.complete && image.naturalWidth>0;
      const templateScriptsBlocked=frame.hasAttribute('sandbox') && !frame.sandbox.contains('allow-scripts') && !window.__qaTemplateRan && !window.__qaImageHandlerRan;
      const originalSelected=state.selected;
      document.querySelector('[data-process-menu] summary').dispatchEvent(new KeyboardEvent('keydown',{key:'ArrowDown',bubbles:true}));
      const menuKeyboardFocus=document.activeElement.getAttribute('role')==='menuitem';
      document.activeElement.dispatchEvent(new KeyboardEvent('keydown',{key:'ArrowRight',bubbles:true}));
      const menuBlocksNavigation=state.selected===originalSelected;
      document.activeElement.dispatchEvent(new KeyboardEvent('keydown',{key:'Escape',bubbles:true}));
      const menuEscClosed=!document.querySelector('[data-process-menu]').open;
      const answer=document.querySelector('.answer');
      answer.open=true;
      document.querySelector('#collapse').click();
      const answerStillOpen=!!document.querySelector('.answer')?.open;
      return JSON.stringify({
        cardCssApplied,
        processMenuPresent: !!document.querySelector('[data-process-menu]'),
        hideScopeVisible: document.querySelector('[data-hide-open]')?.textContent.includes('影响 1 张卡片'),
        stickyControls,
        listScrollIndependent,
        reviewDatesVisible,
        sortPresent, handlePresent, menuItemsHaveRoles, frameFitsContent,
        realImageLoaded, templateScriptsBlocked, menuKeyboardFocus, menuBlocksNavigation, menuEscClosed,
        arrowsOutsideCard: prev.right <= detail.left + 4 && next.left >= detail.right - 4,
        answerStillOpen
      });
      } catch (error) {
        return JSON.stringify({detailCheckError:String(error)});
      }
    })()
    """
    def completed(raw):
        if raw:
            result.update(json.loads(raw))
        else:
            result["detailChecksReturned"] = False
        check_links()
    page().runJavaScript(script, completed)

def check_links():
    import attention_is_all_you_need.dashboard_window as window_module
    opened = []
    original = window_module.openLink
    window_module.openLink = opened.append
    import attention_is_all_you_need as addon
    page().newWindowRequested.connect(lambda request: result.update({'linkRequest': {'url': request.requestedUrl().toString(), 'userInitiated': request.isUserInitiated()}}))
    def checked():
        window_module.openLink = original
        result['sourceLinkForwarded'] = opened == ['obsidian://open?vault=QA&file=source']
        result['openedLinks'] = opened
        page().runJavaScript("document.querySelector('[data-filter=again]')?.click();true", lambda _: QTimer.singleShot(300, check_empty_filter))
    def click(raw):
        from PyQt6.QtTest import QTest
        from aqt.qt import Qt, QPoint
        point = json.loads(raw)
        result['linkPoint'] = point
        capture('focused-card')
        QTest.mouseClick(addon._window.web.focusProxy(), Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier, QPoint(round(point['x']), round(point['y'])))
        QTimer.singleShot(350, checked)
    page().runJavaScript("""(()=>{const f=document.querySelector('iframe'), a=f.contentDocument.querySelector('a');f.scrollIntoView({block:'start'});window.scrollBy(0,-100);
      const r=f.getBoundingClientRect(), l=a.getBoundingClientRect();return JSON.stringify({x:r.x+l.x+l.width/2,y:r.y+l.y+l.height/2});})()""", click)

def force_cards():
    result["step"] = "force_cards"
    page().runJavaScript(
        "if(state.view!=='cards'){document.querySelector('[data-view=cards]').click();}true",
        lambda _: poll("detailA", "!!state.detail", detail_checks),
    )

def check_overview_candidate():
    result["step"] = "check_overview_candidate"
    page().runJavaScript(
        "state.view==='cards'",
        lambda opened: (result.update({"overviewCandidateOpened": bool(opened)}), force_cards()),
    )

def collect_cover_report():
    page().runJavaScript(
        "window.__coverReport",
        lambda raw: (
            result.update(json.loads(raw)),
            page().runJavaScript(
                "document.querySelector('.candidate')?.click();true",
                lambda _: QTimer.singleShot(500, check_overview_candidate),
            ),
        ),
    )

def dashboard_ready():
    result["step"] = "dashboard_ready"
    print("attention regression: dashboard ready", flush=True)
    capture('overview')
    page().runJavaScript(
        r"""
        (async()=>{
          const wait=ms=>new Promise(resolve=>setTimeout(resolve,ms));
          const until=async fn=>{for(let i=0;i<100;i++){if(fn())return true;await wait(50);}return false;};
          const report={};
          try {
            const expectedIds=""" + json.dumps(sorted(IDS['card'+key] for key in 'ABCDEF')) + r""";
            report.imageOnlyExcluded=JSON.stringify(state.dashboard.review_queue.map(r=>r.cardId).sort())===JSON.stringify(expectedIds.sort());
            const cards=[...document.querySelectorAll('.candidate')];
            const visible=state.dashboard.review_queue.slice(0,cards.length);
            const tileFor=id=>cards[visible.findIndex(row=>row.cardId===id)];
            const imageTile=tileFor(""" + str(IDS["cardA"]) + r""");
            const textTile=tileFor(""" + str(IDS["cardB"]) + r""");
            const fallbackTile=tileFor(""" + str(IDS["cardF"]) + r""");
            const image=imageTile?.querySelector('.tile-art img');
            report.overviewImageCoverContain=!!image && getComputedStyle(image).objectFit==='contain';
            report.overviewTextCover=!!textTile?.querySelector('.tile-art.text-preview .text-cover strong')?.innerText.includes('Candidate B');
            report.overviewFallbackCover=!!fallbackTile?.querySelector('.tile-art.fallback-cover .stack-symbol') &&
              !fallbackTile.innerText.includes('Aa') && !fallbackTile.innerText.includes('预览不可用');
            state.view='cards';state.filter='all';state.queue='review';state.selected=""" + str(IDS["cardF"]) + r""";state.detail=null;render();
            loadCard(state.selected,{force:true});
            const detailReady=await until(()=>!!state.detail && state.detail.cardId===""" + str(IDS["cardF"]) + r""" && !!document.querySelector('.detail-fallback-title'));
            report.detailFallbackTitleVisible=detailReady && document.querySelector('.detail-fallback-title').innerText.includes('Basic') &&
              document.querySelector('.detail-fallback-title').innerText.includes('卡片 """ + str(IDS["cardF"]) + r"""');
            report.leftListHasNoCovers=!document.querySelector('.inspection-list .fallback-cover,.inspection-list .text-cover,.inspection-list img');
            state.view='overview';render();
          } catch(error) {
            report.coverReportError=String(error);
          }
          window.__coverReport=JSON.stringify(report);
        })();true
        """,
        lambda _: poll("coverReport", "!!window.__coverReport", collect_cover_report),
    )

def start():
    result["step"] = "start"
    print("attention regression: start", flush=True)
    import attention_is_all_you_need as addon
    addon.open_dashboard()
    poll("dashboard", "!!state.dashboard", dashboard_ready)

gui_hooks.profile_did_open.append(lambda: QTimer.singleShot(0, start))
QTimer.singleShot(35000, lambda: (result.update({"watchdogTimeout": True}), finish()))
'''


def create_fixture(base: Path) -> dict[str, int]:
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
        model["css"] = ".card { font-size: 39px; color: rgb(1, 2, 3); }"
        col.models.save(model)
        ids = {}
        for key in ("A", "B", "C", "D", "E", "F"):
            note = col.new_note(model)
            note["Front"] = f"Candidate {key} " + "long structural text " * 20
            if key == 'A':
                note['Front'] = '<a href="obsidian://open?vault=QA&file=source">source</a><img src="valid.png" onload="parent.__qaImageHandlerRan=true"><script>parent.__qaTemplateRan=true</script>' + note['Front']
            if key == 'F':
                note["Front"] = ""
            note["Back"] = f"Answer {key}"
            if key == 'F':
                note["Back"] = ""
            col.add_note(note, col.decks.id_for_name("Default"))
            ids["note" + key] = int(note.id)
            ids["card" + key] = int(note.card_ids()[0])
        image = col.new_note(model)
        image["Front"] = ""
        image["Back"] = '<img src="normal-image.png">'
        col.add_note(image, col.decks.id_for_name("Default"))
        ids["imageNote"] = int(image.id)
        col.media.write_data('valid.png', base64.b64decode('iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+aJ1sAAAAASUVORK5CYII='))
        now = int(time.time() * 1000)
        for offset, key in enumerate(("A", "B", "C", "D", "E", "F")):
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
        return ids
    finally:
        col.close()


def main() -> int:
    with tempfile.TemporaryDirectory(prefix="attention-regression-") as folder:
        base = Path(folder)
        fixture = create_fixture(base)
        output = base / "result.json"
        fixture_path = base / "fixture.json"
        fixture_path.write_text(json.dumps(fixture), encoding="utf-8")
        addons = base / "addons21"
        addons.mkdir(parents=True, exist_ok=True)
        shutil.copytree(ROOT / "addon", addons / "attention_is_all_you_need",
                        ignore=shutil.ignore_patterns("__pycache__", "*.pyc", "meta.json"))
        helper = addons / "attention_gui_regressions"
        helper.mkdir()
        (helper / "__init__.py").write_text(HELPER, encoding="utf-8")
        code = (
            "import sys,aqt;"
            f"aqt.AnkiApp.KEY={('attention-regression-' + base.name)!r};"
            f"sys.argv=['anki','-b',{str(base)!r},'-p',{PROFILE!r}];"
            "aqt.run()"
        )
        env = dict(os.environ)
        env["ATTENTION_QA_OUTPUT"] = str(output)
        env["ATTENTION_QA_FIXTURE"] = str(fixture_path)
        env["PYTHONUNBUFFERED"] = "1"
        env.setdefault("QT_QPA_PLATFORM", "offscreen")
        env.setdefault("QTWEBENGINE_CHROMIUM_FLAGS", "--disable-gpu")
        try:
            completed = subprocess.run(
                [sys.executable, "-c", code],
                env=env,
                text=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                timeout=45,
            )
        except subprocess.TimeoutExpired as exc:
            print((exc.stdout or b"").decode(errors="replace") if isinstance(exc.stdout, bytes) else (exc.stdout or ""))
            if output.exists():
                print(output.read_text(encoding="utf-8"))
            raise
        if not output.exists():
            print(completed.stdout)
            raise SystemExit("Anki GUI regressions did not produce a result")
        result = json.loads(output.read_text(encoding="utf-8"))
        print(json.dumps(result, ensure_ascii=False, indent=2))
        required = [
            "imageOnlyExcluded", "overviewCandidateOpened", "cardCssApplied",
            "processMenuPresent", "hideScopeVisible", "arrowsOutsideCard",
            "answerStillOpen", "filterControlsRemain", "emptyStateVisible",
            "refreshGuardedDraft", "crossNoteSafe", "savedContinue",
            "unlockedAfterSaveContinue", "continuedToOverview", "stickyControls",
            "listScrollIndependent", "reviewDatesVisible",
            "sortPresent", "handlePresent", "menuItemsHaveRoles", "frameFitsContent",
            "realImageLoaded", "templateScriptsBlocked", "sourceLinkForwarded",
            "menuKeyboardFocus", "menuBlocksNavigation", "menuEscClosed",
            "narrowArrowsVisible", "narrowToggleAvailable", "closedGate", "profileGate", "invalidParamsReplied",
            "editingAllowedDuringSettings", "settingsPreserveDraft", "settingsAppliedAfterDiscard", "settingsFailureRestoresControls", "optimizationIgnoresSignals",
            "settingsAppliedAfterDialogCancel", "viewportHeightStable", "actualCloseGate",
            "partialFailureMessage", "partialStateReloaded", "partialTagVisible",
            "partialCardUnsuspended",
            "staleDetailIgnored", "staleFailureDidNotReplacePage",
            "overviewImageCoverContain", "overviewTextCover", "overviewFallbackCover",
            "detailFallbackTitleVisible", "leftListHasNoCovers",
        ]
        passed = completed.returncode == 0 and all(result.get(key) for key in required)
        if not passed:
            print('\n'.join(line for line in completed.stdout.splitlines() if ':ERROR:' not in line)[-9000:])
        return 0 if passed else 1


if __name__ == "__main__":
    raise SystemExit(main())
