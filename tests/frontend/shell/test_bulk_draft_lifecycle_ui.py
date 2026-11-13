"""Real IndexedDB/Vue draft expiry must preserve uncertain bulk-write evidence."""

from frontend.editor.test_editor_roundtrip_ui import editor_browser
import pytest


def test_expired_bulk_evidence_survives_load_purge_and_form_restore_without_replay(editor_browser):
    page, base, errors, _ = editor_browser
    posts = []
    page.route("**/api/bulk/create", lambda route: (posts.append(route.request.post_data), route.abort()))
    page.goto(base)
    page.evaluate("""async () => {
      const {createApp} = await import('/vendor/vue.esm-browser.prod.js');
      const {default:component} = await import('/components/ui/BulkCreateDialog.js');
      window.mountBulkDraft = () => {
        const host=document.createElement('div'); document.body.append(host);
        window.bulkDraftApp=createApp(component,{mode:'task',onClose:()=>window.bulkDraftApp.unmount()});
        window.bulkDraftVm=window.bulkDraftApp.mount(host);
      };
      window.mountBulkDraft();
    }""")
    page.wait_for_function("() => window.bulkDraftVm.formDraftReady")
    retained = page.evaluate("""async () => {
      const d=await import('/lib/draft.js');
      const {newBulkRun,bulkRunResult}=await import('/lib/bulkCreateRun.js');
      const vm=window.bulkDraftVm, old=Date.now()-8*86400000;
      const items=[{summary:'immutable original draft',type:'Task',epic:null}];
      const run=newBulkRun('task',items,'bulk:expired:evidence',vm.executionScope);
      run.firstAttemptAt=old; run.chunks[0].attempted=true;
      run.chunks[0].states[0]={ok:null,error:'response lost'};
      vm.creationRun=run; vm.createMutationId=''; // run alone must restore the whole form atomically
      vm.src=JSON.stringify({mode:'task',items}); vm.items=items; vm.step='preview';
      vm.showRunResult(bulkRunResult(run));
      await vm.flushFormDraft(); window.bulkDraftKey=vm._formKey;
      window.bulkDraftApp.unmount();
      const record=await d.loadDraft(window.bulkDraftKey);
      if (!record.preserveExpiredForMutation) throw new Error('formDraft did not retain mutation evidence');
      // Model eight days of inactivity: no close/watch event refreshed savedAt in that interval.
      const db=await new Promise((resolve,reject)=>{const q=indexedDB.open('ltm-drafts',1);
        q.onsuccess=()=>resolve(q.result); q.onerror=()=>reject(q.error);});
      await new Promise((resolve,reject)=>{
        const tx=db.transaction('drafts','readwrite'), store=tx.objectStore('drafts');
        store.put({...record,savedAt:old},window.bulkDraftKey);
        store.put({...record,savedAt:old},'bulk-evidence-direct-load');
        store.put({html:'ordinary editor draft',savedAt:old},'ordinary-expired-load');
        store.put({html:'ordinary editor draft',savedAt:old},'ordinary-expired-purge');
        tx.oncomplete=resolve; tx.onerror=()=>reject(tx.error);
      }); db.close();
      const direct=await d.loadDraft('bulk-evidence-direct-load');
      const ordinaryLoad=await d.loadDraft('ordinary-expired-load');
      // Fresh module instance gives this page's one-shot startup sweep its first call.
      const sweep=await import('/lib/draft.js?bulk-expiry-sweep'); await sweep.purgeExpired();
      const afterSweep=await d.loadDraft(window.bulkDraftKey);
      const ordinaryPurge=await d.loadDraft('ordinary-expired-purge');
      window.mountBulkDraft();
      window.bulkDraftVm.src='edited while async identity/storage restoration is pending';
      return {direct:direct.fields.creationRun.id,afterSweep:afterSweep.fields.creationRun.id,
        savedAt:afterSweep.savedAt,old,ordinaryLoad,ordinaryPurge};
    }""")
    assert retained["direct"] == retained["afterSweep"] == "bulk:expired:evidence"
    assert retained["savedAt"] == retained["old"]
    assert retained["ordinaryLoad"] is None and retained["ordinaryPurge"] is None
    page.wait_for_function("() => window.bulkDraftVm.formDraftReady && window.bulkDraftVm.result?.blocked")
    page.locator(".blk").get_by_text("최초 생성 요청 후 7일", exact=False).wait_for()
    assert page.locator(".blk").get_by_role("button", name="같은 요청으로 결과 확인·계속").is_disabled()
    restored = page.evaluate("""async () => {
      const vm=window.bulkDraftVm;
      await vm.create(); vm.retryFailed();
      const blocked={src:vm.src,id:vm.creationRun.id,firstAttemptAt:vm.creationRun.firstAttemptAt,
        pending:vm.result.pending.length,blocked:vm.result.blocked};
      // Confirming/clearing a run removes the retention marker and the stored record.
      vm.creationRun.chunks[0].states[0]={ok:true,key:'DL-confirmed-fixture'};
      await vm.flushFormDraft();
      const d=await import('/lib/draft.js');
      const completed=await d.loadDraft(vm._formKey);
      await vm.finishFormDraft(true);
      return {...blocked,retainedAfterSuccess:completed.preserveExpiredForMutation,
        cleared:await d.loadDraft(vm._formKey)};
    }""")
    assert "immutable original draft" in restored["src"]
    assert "async identity" not in restored["src"]
    assert restored["id"] == "bulk:expired:evidence" and restored["firstAttemptAt"] == retained["old"]
    assert restored["pending"] == 1 and "7일" in restored["blocked"]
    assert restored["retainedAfterSuccess"] is False and restored["cleared"] is None
    assert posts == []
    assert not errors


def test_local_draft_initialization_failure_preserves_typed_json_until_retry(editor_browser):
    page, base, errors, _ = editor_browser
    posts = []
    page.route("**/api/bulk/create", lambda route: (posts.append(route.request.post_data), route.abort()))
    page.goto(base)
    page.evaluate("""async () => {
      // Draft initialization is local; an unavailable Jira context must not gate typing.
      // Fail the durable boundary that actually controls whether this form can close.
      window.failBulkDraftStorage = true;
      const original = IDBDatabase.prototype.transaction;
      IDBDatabase.prototype.transaction = function(...args) {
        const tx = original.apply(this, args);
        if (window.failBulkDraftStorage && args[1] === 'readwrite') queueMicrotask(() => tx.abort());
        return tx;
      };
      const {createApp} = await import('/vendor/vue.esm-browser.prod.js');
      const {default:component} = await import('/components/ui/BulkCreateDialog.js');
      window.mountBulkDraft=()=>{
        const host=document.createElement('div'); document.body.append(host);
        window.bulkDraftApp=createApp(component,{mode:'task',onClose:()=>window.bulkDraftApp.unmount()});
        window.bulkDraftVm=window.bulkDraftApp.mount(host);
      }; window.mountBulkDraft();
    }""")
    page.locator(".blk").get_by_role("button", name="연결 확인 다시 시도").wait_for()
    page.locator(".blk button[aria-label='닫기']").click()
    page.locator(".blk").wait_for(state="detached")
    page.evaluate("() => window.mountBulkDraft()")
    page.locator(".blk").get_by_role("button", name="연결 확인 다시 시도").wait_for()
    source = '{"mode":"task","items":[{"type":"Task","summary":"retain unbound JSON","epic":null}]}'
    page.evaluate("source => {window.bulkDraftVm.src=source;}", source)
    page.locator(".blk button[aria-label='닫기']").click()
    assert page.locator(".blk").is_visible()
    assert page.locator(".blk").get_by_role("button", name="입력 버리고 닫기").is_visible()
    assert page.locator(".blk").get_by_role("button", name="입력 JSON 복사").is_visible()
    assert page.evaluate("() => window.bulkDraftVm.src") == source
    page.evaluate("window.failBulkDraftStorage = false")
    page.locator(".blk").get_by_role("button", name="연결 확인 다시 시도").click()
    page.wait_for_function("() => window.bulkDraftVm.formDraftReady")
    assert page.locator(".blk [role=alert]").count() == 0
    assert page.evaluate("() => window.bulkDraftVm.src") == source
    page.locator(".blk button[aria-label='닫기']").click()
    page.locator(".blk").wait_for(state="detached")
    page.evaluate("() => window.mountBulkDraft()")
    page.wait_for_function("() => window.bulkDraftVm.formDraftReady")
    assert page.evaluate("() => window.bulkDraftVm.src") == source
    assert posts == []
    assert not errors


@pytest.mark.parametrize("age_days", [0, 8], ids=["pending", "expired"])
def test_explicit_bulk_record_delete_cancels_safely_and_reopens_new_input(editor_browser, age_days):
    page, base, errors, _ = editor_browser
    writes = []
    page.on("request", lambda request: writes.append(request.url)
            if request.method == "DELETE" or (request.method == "POST" and "/api/bulk/create" in request.url) else None)
    page.context.grant_permissions(["clipboard-read", "clipboard-write"], origin=base)
    page.goto(base)
    page.evaluate("""async () => {
      const {createApp} = await import('/vendor/vue.esm-browser.prod.js');
      const {default:component} = await import('/components/ui/BulkCreateDialog.js');
      window.mountBulkDraft=()=>{
        const host=document.createElement('div'); document.body.append(host);
        window.bulkDraftApp=createApp(component,{mode:'task',onClose:()=>window.bulkDraftApp.unmount()});
        window.bulkDraftVm=window.bulkDraftApp.mount(host);
      }; window.mountBulkDraft();
    }""")
    page.wait_for_function("() => window.bulkDraftVm.formDraftReady")
    source = page.evaluate("""async days => {
      const {newBulkRun,bulkRunResult}=await import('/lib/bulkCreateRun.js');
      const vm=window.bulkDraftVm;
      const items=[{summary:'confirmed record',type:'Task',epic:null},{summary:'uncertain record',type:'Task',epic:null}];
      vm.creationRun=newBulkRun('task',items,'bulk:record:discard',vm.executionScope);
      vm.creationRun.firstAttemptAt=Date.now()-days*86400000;
      vm.creationRun.chunks[0].attempted=true;
      vm.creationRun.chunks[0].states={0:{ok:true,key:'DL-9007'},1:{ok:null,error:'response lost'}};
      vm.createMutationId=vm.creationRun.id; vm.items=items; vm.src=JSON.stringify({mode:'task',items});
      vm.step='preview'; vm.showRunResult(bulkRunResult(vm.creationRun));
      await vm.flushFormDraft(); window.bulkDraftKey=vm._formKey;
      vm.busy=true; await vm.discardExecutionRecord(); vm.busy=false;
      return vm.src;
    }""", age_days)
    page.set_viewport_size({"width": 390, "height": 844})
    footer_fits = page.locator(".blk-f").evaluate("node => node.scrollWidth <= node.clientWidth")
    assert footer_fits
    assert page.get_by_role("alertdialog").count() == 0
    page.locator(".blk").get_by_role("button", name="입력 JSON 복사").click()
    assert page.evaluate("() => navigator.clipboard.readText()") == source
    page.locator(".blk").get_by_role("button", name="실행 기록 삭제").click()
    confirm = page.get_by_role("alertdialog")
    assert "실제 생성된 Jira 티켓은 삭제되지 않습니다" in confirm.inner_text()
    assert "Jira에서 생성 여부를 확인" in confirm.inner_text()
    confirm.get_by_role("button", name="취소", exact=True).click()
    saved = page.evaluate("async () => (await (await import('/lib/draft.js')).loadDraft(window.bulkDraftKey)).fields.creationRun.id")
    assert saved == "bulk:record:discard" and page.locator(".blk").is_visible()
    page.locator(".blk").get_by_role("button", name="실행 기록 삭제").click()
    page.get_by_role("alertdialog").get_by_role("button", name="실행 기록 삭제", exact=True).click()
    page.locator(".blk").wait_for(state="detached")
    assert page.evaluate("async () => await (await import('/lib/draft.js')).loadDraft(window.bulkDraftKey)") is None
    page.evaluate("() => window.mountBulkDraft()")
    page.wait_for_function("() => window.bulkDraftVm.formDraftReady")
    assert page.evaluate("() => ({step:window.bulkDraftVm.step,run:window.bulkDraftVm.creationRun})") == {"step": "input", "run": None}
    assert "uncertain record" not in page.evaluate("() => window.bulkDraftVm.src")
    assert writes == [] and not errors
