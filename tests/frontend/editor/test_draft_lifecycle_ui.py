"""Durable drafts use the real browser IndexedDB and editor/form components against mock Jira."""
import pytest

from frontend.editor.test_editor_roundtrip_ui import editor_browser, _open_ticket, PNG_1X1


def _mount(page, name, props):
    page.evaluate("""async ({name, props}) => {
      if (window.draftApp) window.draftApp.unmount();
      document.querySelector('#draft-harness')?.remove();
      const host = document.createElement('div'); host.id = 'draft-harness'; document.body.append(host);
      const {createApp} = await import('/vendor/vue.esm-browser.prod.js');
      const {default: component} = await import('/components/ui/' + name + '.js');
      const app = createApp(component, {...props,
        onClose: () => { app.unmount(); window.draftClosed = true; },
        onCreated: key => { window.draftCreated = key; app.unmount(); }});
      window.draftClosed = false; window.draftCreated = '';
      window.draftApp = app; window.draftVm = app.mount(host);
    }""", {"name": name, "props": props})
    page.wait_for_function("() => window.draftVm.formDraftReady")


@pytest.mark.parametrize("name,props", [
    ("NewChildDialog", {"standalone": True}),
    ("NewChildDialog", {"parent": "DL-9007", "isEpic": False}),
    ("EpicCreateDialog", {}),
])
def test_create_form_cancel_and_reload_restore_fields_and_editor(editor_browser, name, props):
    page, base, errors, _ = editor_browser
    page.goto(base)
    page.evaluate("""async () => {
      const {api} = await import('/lib/api.js');
      api.me = async () => ({manager:false}); // Non-admin / identity unavailable must not gate local drafts.
    }""")
    _mount(page, name, props)
    page.locator(".nk-sum").fill("보존할 생성 초안")
    page.locator(".nk-desc-t").filter(has_text="설명").click()
    editor = page.locator(".nk-desc .ProseMirror")
    editor.fill("설명도 보존합니다")
    page.locator(".nk-f").get_by_role("button", name="취소", exact=True).click()
    page.wait_for_function("() => window.draftClosed")
    page.reload()
    _mount(page, name, props)
    assert page.locator(".nk-sum").input_value() == "보존할 생성 초안"
    editor = page.locator(".nk-desc .ProseMirror")
    page.wait_for_function("() => document.querySelector('.nk-desc .ProseMirror')?.textContent.includes('설명도 보존합니다')")
    assert editor.inner_text() == "설명도 보존합니다"
    assert not errors


@pytest.mark.parametrize("editor_browser", [0, 800], indirect=True)
def test_comment_cancel_navigation_and_duplicate_image_names_restore(editor_browser):
    page, base, errors, _ = editor_browser
    _open_ticket(page, base, "DL-9007")
    page.get_by_role("button", name="＋ 댓글 달기").click()
    editor = page.locator(".tkt-compose-editor .ProseMirror")
    page.locator(".tkt-compose-editor .ProseMirror[contenteditable=true]").fill("초안 이미지 두 개")
    # Exercise the real file chooser handler with equal filenames but different file payloads.
    picker = page.locator(".tkt-compose-editor input[type=file]")
    picker.set_input_files([
        {"name": "same.png", "mimeType": "image/png", "buffer": PNG_1X1},
        {"name": "same.png", "mimeType": "image/png", "buffer": PNG_1X1 + b"second"},
    ])
    try:
        page.wait_for_function("() => document.querySelectorAll('.tkt-compose-editor .ProseMirror .img-wrap img').length === 2")
    except Exception:
        raise AssertionError(page.locator(".tkt-compose-editor .ProseMirror").inner_html())
    page.locator(".tkt-compose-editor").get_by_role("button", name="취소", exact=True).click()
    page.locator(".tkt-compose-editor").wait_for(state="detached")
    page.reload()
    page.get_by_role("button", name="＋ 댓글 달기").click()
    try:
        page.wait_for_function("() => document.querySelectorAll('.tkt-compose-editor .ProseMirror .img-wrap img').length === 2")
    except Exception:
        raise AssertionError(page.locator(".tkt-compose-editor .ProseMirror").inner_html())
    assert "초안 이미지 두 개" in page.locator(".tkt-compose-editor .ProseMirror").inner_text()
    info = page.locator(".tkt-compose-editor .ProseMirror .img-wrap img").evaluate_all("nodes => nodes.map(n => ({src:n.src, good:n.complete && n.naturalWidth > 0}))")
    assert len({item["src"] for item in info}) == 2
    assert all(item["good"] for item in info)
    assert not errors


def test_description_cancel_restores_and_remote_change_requires_choice(editor_browser):
    page, base, errors, _ = editor_browser
    _open_ticket(page, base, "DL-9001")
    page.locator("button.sec-edit").first.click()
    page.locator(".tkt-desc-edit .ProseMirror[contenteditable=true]").fill("보존할 본문 수정")
    page.locator(".tkt-desc-edit-f").get_by_role("button", name="취소", exact=True).click()
    page.locator("button.sec-edit").first.click()
    page.wait_for_function("() => document.querySelector('.tkt-desc-edit .ProseMirror')?.textContent === '보존할 본문 수정'")
    page.locator(".tkt-desc-edit-f").get_by_role("button", name="취소", exact=True).click()
    response = page.request.put(base + "/api/ticket/DL-9001/fields", data={"descriptionHtml": "<p>다른 사용자가 변경한 본문</p>"})
    assert response.ok
    page.reload()
    page.locator("button.sec-edit").first.click()
    page.get_by_text("서버 내용이 변경되었습니다. 저장된 초안을 복원할까요?").wait_for()
    assert "다른 사용자가 변경한 본문" in page.locator(".tkt-desc-edit .ProseMirror").inner_text()
    page.get_by_role("button", name="초안 복원", exact=True).click()
    assert page.locator(".tkt-desc-edit .ProseMirror").inner_text().strip() == "보존할 본문 수정"
    assert not errors


def test_transition_cancel_restores_fields_and_comment(editor_browser):
    page, base, errors, _ = editor_browser
    page.goto(base)
    props = {"ticket": "DL-9007", "transition": {"id": "31", "name": "완료", "toCategory": "done",
        "fields": [{"id": "worklog"}, {"id": "resolution", "allowedValues": [{"id": "1", "name": "Done"}]}]}}
    _mount(page, "TransitionDialog", props)
    page.locator(".trx-time input").first.fill("2")
    page.locator(".trx .ProseMirror").fill("전환 코멘트 보존")
    page.locator(".trx-f2").get_by_role("button", name="취소", exact=True).click()
    page.wait_for_function("() => window.draftClosed")
    page.reload()
    _mount(page, "TransitionDialog", props)
    assert page.locator(".trx-time input").first.input_value() == "2"
    page.wait_for_function("() => document.querySelector('.trx .ProseMirror')?.textContent === '전환 코멘트 보존'")
    assert not errors


def test_draft_storage_commit_order_expiry_and_scope(editor_browser):
    page, base, errors, _ = editor_browser
    page.goto(base)
    result = page.evaluate("""async () => {
      const d = await import('/lib/draft.js');
      const identity = await import('/lib/draftIdentity.js');
      const key = await identity.resolveDraftKey('test:ordering');
      await Promise.all([d.saveDraft(key, {html:'old'}), d.clearDraft(key), d.saveDraft(key, {html:'new'})]);
      const ordered = (await d.loadDraft(key)).html;
      const realNow = Date.now;
      Date.now = () => realNow() + d.DRAFT_TTL_MS + 1;
      const expired = await d.loadDraft(key);
      Date.now = realNow;
      const original = IDBDatabase.prototype.transaction;
      IDBDatabase.prototype.transaction = function(...args) {
        const transaction = original.apply(this, args);
        if (args[1] === 'readwrite') queueMicrotask(() => transaction.abort());
        return transaction;
      };
      let failed = false;
      try { await d.saveDraft(key, {html:'must not succeed'}); } catch (_) { failed = true; }
      finally { IDBDatabase.prototype.transaction = original; }
      const fresh = await import('/lib/draft.js?open-retry-test');
      const open = indexedDB.open;
      indexedDB.open = () => { throw new DOMException('temporarily blocked', 'SecurityError'); };
      let openFailed = false;
      try { await fresh.saveDraft(key, {html:'blocked'}); } catch (_) { openFailed = true; }
      finally { indexedDB.open = open; }
      await fresh.saveDraft(key, {html:'open recovered'});
      const openRecovery = openFailed && (await fresh.loadDraft(key)).html === 'open recovered';
      const {api} = await import('/lib/api.js');
      const oldMe = api.me, oldHealth = api.health;
      let calls = 0;
      api.health = async () => { calls++; throw new Error('offline'); };
      identity.rememberDraftContext({env:'prod', jiraBase:'https://jira-a', projectKey:'DL'});
      const keys = [];
      for (const failure of ['missing', 'forbidden', 'expired', 'offline', 'pending']) {
        api.me = async () => {
          calls++;
          if (failure === 'missing') return {};
          if (failure === 'pending') return new Promise(() => {});
          throw Object.assign(new Error(failure), {status: failure === 'forbidden' ? 403 : 401});
        };
        window.dispatchEvent(new Event('auth-ok'));
        keys.push(await identity.resolveDraftKey('test:ordering'));
      }
      identity.rememberDraftContext({env:'prod', jiraBase:'https://jira-a', projectKey:'OTHER'});
      const otherProject = await identity.resolveDraftKey('test:ordering');
      identity.rememberDraftContext({env:'prod', jiraBase:'https://jira-b', projectKey:'DL'});
      const otherServer = await identity.resolveDraftKey('test:ordering');
      api.me = oldMe; api.health = oldHealth;
      return {ordered, expired, failed, openRecovery, calls,
        scoped: new Set(keys).size === 1 && new Set([keys[0], otherProject, otherServer]).size === 3};
    }""")
    assert result == {"ordered": "new", "expired": None, "failed": True, "openRecovery": True, "calls": 0, "scoped": True}
    assert not errors


def test_local_draft_migration_keeps_images_receipts_and_does_not_resurrect(editor_browser):
    page, base, errors, _ = editor_browser
    page.goto(base)
    result = page.evaluate("""async () => {
      const d = await import('/lib/draft.js');
      const identity = await import('/lib/draftIdentity.js?migration-test');
      const scope = 'scoped:comment:DL-9007:42';
      identity.rememberDraftContext({env:'prod', jiraBase:'https://jira-a', projectKey:'DL'});
      const prefix = 'v2:prod:' + encodeURIComponent('https://jira-a') + ':';
      const old = prefix + 'old-user:' + scope, latest = prefix + 'new-user:' + scope;
      const other = 'v2:prod:' + encodeURIComponent('https://jira-b') + ':user:' + scope;
      const now = Date.now, stamp = now();
      Date.now = () => stamp - 2000;
      await d.saveDraft(old, {html:'older'});
      Date.now = () => stamp - 1000;
      await d.saveDraft(latest, {html:'latest', mutationId:'receipt-1', pendingHtml:'pending',
        images:[{token:'draft:image',name:'same.png',blob:new Blob(['image-data'])}]});
      Date.now = () => stamp;
      await d.saveDraft(other, {html:'other server'});
      Date.now = now;
      const key = await identity.resolveDraftKey(scope), record = await d.loadDraft(key);
      const oldRecord = await d.loadDraft(old), latestRecord = await d.loadDraft(latest);
      await d.clearDraft(key);
      await identity.resolveDraftKey(scope);
      // A first offline session has no cached health metadata; still no HTTP is needed.
      localStorage.removeItem('ltm-draft-context.v3');
      const offline = await import('/lib/draftIdentity.js?first-offline');
      const fallback = await offline.resolveDraftKey('new:DL-9007');
      await d.saveDraft(fallback, {html:'offline draft'});
      offline.rememberDraftContext({env:'prod', jiraBase:'https://jira-a', projectKey:'DL'});
      const recovered = await offline.resolveDraftKey('new:DL-9007');
      const bulkScope = 'form:bulk-create:task';
      const bulkOld = 'v3:bulk:prod:' + encodeURIComponent('https://jira-a') + ':DL:old-user:' + bulkScope;
      const run = {id:'bulk:old:001', scope:{environment:'prod',jiraBase:'https://jira-a',projectKey:'DL',userId:'old-user'},
        chunks:[{attempted:true, body:{clientMutationId:'bulk:old:001:0'}, states:{0:{ok:null}}}]};
      await d.saveDraft(bulkOld, {fields:{creationRun:run}, preserveExpiredForMutation:true});
      const newerBulk = bulkOld.replace(':old-user:', ':new-user:');
      const anotherPending = bulkOld.replace(':old-user:', ':pending-user:');
      Date.now = () => stamp + 1000;
      await d.saveDraft(newerBulk, {fields:{src:'newer normal draft'}});
      Date.now = () => stamp - 5000;
      await d.saveDraft(anotherPending, {fields:{creationRun:{...run,id:'bulk:older:002'}}, preserveExpiredForMutation:true});
      Date.now = now;
      const bulkKey = await identity.resolveDraftKey(bulkScope);
      const bulk = await d.loadDraft(bulkKey);
      const newestPending = bulkOld.replace(':old-user:', ':third-pending-user:');
      Date.now = () => stamp + 5000;
      await d.saveDraft(newestPending, {fields:{creationRun:{...run,id:'bulk:newer:003'}}, preserveExpiredForMutation:true});
      Date.now = now;
      await identity.resolveDraftKey(bulkScope);
      const activePendingKept = (await d.loadDraft(bulkKey)).fields.creationRun.id === run.id;
      await d.clearDraft(bulkKey);
      await identity.resolveDraftKey(bulkScope);
      const nextPendingRecovered = (await d.loadDraft(bulkKey)).fields.creationRun.id === 'bulk:newer:003';
      await d.clearDraft(bulkKey);
      await identity.resolveDraftKey(bulkScope);
      const remainingBulk = await d.loadDraft(bulkKey);
      identity.rememberDraftContext({env:'mock',jiraBase:'http://jira.local',projectKey:'DL',
        executionScope:{environment:'mock@2026-11-05',jiraBase:'http://jira.local',projectKey:'DL'}});
      const execution = identity.localDraftContext();
      const {bulkScopeKey} = await import('/lib/bulkCreateRun.js');
      const mockScopeMatches = bulkScopeKey(execution) === bulkScopeKey({environment:'mock@2026-11-05',
        jiraBase:'http://jira.local',projectKey:'DL'});
      return {html:record.html, image:await record.images[0].blob.text(),
        receipt:record.mutationId, pending:record.pendingHtml, timestamp:record.savedAt === stamp - 1000,
        originals:oldRecord.html === 'older' && latestRecord.html === 'latest'
          && oldRecord.migratedTo === key && latestRecord.migratedTo === key,
        other:(await d.loadDraft(other)).html,
        deleted:await d.loadDraft(key), offline:(await d.loadDraft(recovered)).html,
        fallback:fallback === 'v3:local:new:DL-9007' && recovered !== fallback,
        pendingPreserved:remainingBulk.fields.creationRun.id === 'bulk:older:002' && activePendingKept && nextPendingRecovered,
        mockScopeMatches, bulk:JSON.stringify(bulk.fields.creationRun) === JSON.stringify(run) && bulk.preserveExpiredForMutation
          && (await d.loadDraft(bulkOld)).migratedTo === bulkKey};
    }""")
    assert result == {"html": "latest", "image": "image-data", "receipt": "receipt-1", "pending": "pending",
                      "timestamp": True, "originals": True, "other": "other server", "deleted": None,
                      "offline": "offline draft", "fallback": True, "bulk": True, "mockScopeMatches": True, "pendingPreserved": True}
    assert not errors


def test_storage_failure_keeps_visible_editor_and_cancel_can_retry(editor_browser):
    page, base, errors, _ = editor_browser
    _open_ticket(page, base, "DL-9007")
    page.get_by_role("button", name="＋ 댓글 달기").click()
    editor = page.locator(".tkt-compose-editor .ProseMirror[contenteditable=true]")
    editor.wait_for(state="visible")
    page.evaluate("""() => {
      window.originalDraftTransaction = IDBDatabase.prototype.transaction;
      IDBDatabase.prototype.transaction = function(...args) {
        const tx = window.originalDraftTransaction.apply(this, args);
        if (args[1] === 'readwrite') queueMicrotask(() => tx.abort());
        return tx;
      };
    }""")
    editor.fill("저장 실패해도 작성 내용 유지")
    page.locator(".tkt-compose-editor [role=alert]").wait_for()
    page.locator(".tkt-compose-editor").get_by_role("button", name="취소", exact=True).click()
    assert editor.is_visible()
    assert "저장 실패해도 작성 내용 유지" in editor.inner_text()
    page.locator(".tkt-compose-editor").get_by_role("button", name="초안 삭제", exact=True).click()
    assert "저장 실패해도 작성 내용 유지" in editor.inner_text()
    page.evaluate("() => { IDBDatabase.prototype.transaction = window.originalDraftTransaction; }")
    page.locator(".tkt-compose-editor").get_by_role("button", name="취소", exact=True).click()
    page.locator(".tkt-compose-editor").wait_for(state="detached")
    page.reload()
    page.get_by_role("button", name="＋ 댓글 달기").click()
    page.wait_for_function("() => document.querySelector('.tkt-compose-editor .ProseMirror')?.textContent.includes('저장 실패해도 작성 내용 유지')")
    assert not errors


def test_confirmed_create_key_survives_followup_failure_without_duplicate(editor_browser):
    page, base, errors, upload = editor_browser
    page.goto(base)
    props = {"standalone": True, "types": ["Task"]}
    _mount(page, "NewChildDialog", props)
    created = []
    page.on("request", lambda request: created.append(request.url)
            if request.method == "POST" and request.url == base + "/api/task" else None)
    page.locator(".nk-sum").fill("생성 후 설명 실패 복구")
    page.evaluate("""async () => {
      const {api} = await import('/lib/api.js');
      window.draftVm.nc.priority = (await api.options('priorities'))[0].name;
      api.updateFields = async () => { throw new Error('follow-up failed'); };
    }""")
    page.locator(".nk-desc-t").filter(has_text="설명").click()
    page.locator(".nk-desc .ProseMirror[contenteditable=true]").fill("첨부와 본문 복구")
    page.locator(".nk-desc input[type=file]").set_input_files(upload)
    page.locator(".nk-f button.primary").click()
    page.wait_for_function("() => window.draftVm.createdKey && !window.draftVm.busy && window.draftVm.err")
    key = page.evaluate("window.draftVm.createdKey")
    page.locator(".nk-f").get_by_role("button", name="취소", exact=True).click()
    page.wait_for_function("() => window.draftClosed")
    page.reload()
    _mount(page, "NewChildDialog", props)
    assert page.evaluate("window.draftVm.createdKey") == key
    page.wait_for_function("() => window.draftVm.$refs.ded?.ready")
    page.locator(".nk-f button.primary").click()
    page.wait_for_function("() => !!window.draftCreated")
    assert page.evaluate("window.draftCreated") == key
    assert len(created) == 1
    ticket = page.request.get(base + "/api/ticket/" + key).json()
    assert "첨부와 본문 복구" in ticket["descriptionHtml"]
    assert not errors


def test_uncertain_transition_can_close_and_restores_exact_receipt(editor_browser):
    page, base, errors, _ = editor_browser
    page.goto(base)
    props = {"ticket": "DL-9007", "transition": {"id": "31", "name": "완료", "toCategory": "done", "fields": []}}
    _mount(page, "TransitionDialog", props)
    page.evaluate("""async () => {
      const {api} = await import('/lib/api.js');
      api.doTransition = async (_key, payload) => {
        window.sentTransition = JSON.parse(JSON.stringify(payload));
        throw Object.assign(new Error('response lost'), {uncertain:true});
      };
    }""")
    page.locator(".trx .ProseMirror[contenteditable=true]").fill("응답 유실 시도")
    page.locator(".trx-ok").click()
    page.wait_for_function("() => window.sentTransition && !window.draftVm.busy")
    sent = page.evaluate("window.sentTransition")
    page.locator(".trx-f2").get_by_role("button", name="취소", exact=True).click()
    page.wait_for_function("() => window.draftClosed")
    page.reload()
    _mount(page, "TransitionDialog", props)
    assert page.evaluate("window.draftVm.pendingTransitionPayload") == sent
    assert page.evaluate("window.draftVm.transitionMutationId") == sent["clientMutationId"]
    page.wait_for_function("() => window.draftVm.$refs.ed?.ready")
    assert page.evaluate("window.draftVm.$refs.ed.discardDraft()") is False
    assert page.evaluate("window.draftVm.$refs.ed.draftConflict") is False
    assert not errors


def test_bulk_form_uses_local_health_execution_scope_without_open_time_identity(editor_browser):
    page, base, errors, _ = editor_browser
    page.goto(base)
    page.evaluate("""async () => {
      const {api} = await import('/lib/api.js');
      const {rememberDraftContext} = await import('/lib/draftIdentity.js');
      await api.health();
      rememberDraftContext({env:'mock',jiraBase:'http://jira.local',projectKey:'DL',
        executionScope:{environment:'mock@2026-11-05',jiraBase:'http://jira.local',projectKey:'DL'}});
      window.bulkContextCalls = 0; window.bulkWrites = [];
      api.bulkCreateContext = async () => { window.bulkContextCalls++; return {
        environment:'mock@2026-11-05',jiraBase:'http://jira.local',projectKey:'DL'}; };
      api.bulkCreate = async body => { window.bulkWrites.push(body); return {
        ok:true, created:body.items.map((_,index)=>({index,key:'DL-'+(9900+index)})), failed:[]}; };
    }""")
    _mount(page, "BulkCreateDialog", {"mode": "task"})
    assert page.evaluate("window.bulkContextCalls") == 0
    result = page.evaluate("""async () => {
      const vm=window.draftVm;
      vm.items=[{summary:'local scope smoke',type:'Task',epic:null}];
      await vm.create();
      return {result:vm.result, writes:window.bulkWrites};
    }""")
    assert result["result"]["ok"] and len(result["writes"]) == 1
    assert result["writes"][0]["expectedScope"]["environment"] == "mock@2026-11-05"
    assert "userId" not in result["writes"][0]["expectedScope"]
    assert not errors
