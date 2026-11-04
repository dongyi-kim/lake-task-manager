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


@pytest.mark.parametrize("status", [401, 503])
def test_initial_context_failure_can_close_or_retry_without_discarding_typed_json(editor_browser, status):
    page, base, errors, _ = editor_browser
    failing, posts = True, []

    def context(route):
        if failing:
            route.fulfill(status=status, content_type="application/json", body='{"error":"fixture context unavailable"}')
        else:
            route.continue_()

    page.route("**/api/bulk/create-context", context)
    page.route("**/api/bulk/create", lambda route: (posts.append(route.request.post_data), route.abort()))
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
    failing = False
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
    assert not [error for error in errors if f"HTTP {status} " not in error or "/api/bulk/create-context" not in error]
