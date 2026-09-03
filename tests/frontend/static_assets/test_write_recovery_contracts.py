from pathlib import Path


STATIC = Path(__file__).resolve().parents[3] / "app" / "static"


def _read(path):
    return (STATIC / path).read_text(encoding="utf-8")


def test_create_dialogs_keep_stable_client_mutation_id_until_confirmed_success():
    child = _read("components/ui/NewChildDialog.js")
    epic = _read("components/ui/EpicCreateDialog.js")
    assert 'newMutationId("issue")' in child
    assert "payload.clientMutationId = this.createMutationId" in child
    assert 'newMutationId("epic")' in epic
    assert "payload.clientMutationId = this.createMutationId" in epic
    assert "e.uncertain || e.needLogin" in child
    assert "e.uncertain || e.needLogin" in epic
    for source, scope in ((child, "issue-create"), (epic, "epic-create")):
        assert f'savePendingMutation("{scope}"' in source
        assert f'loadPendingMutation("{scope}")' in source
        assert f'clearPendingMutation("{scope}")' in source
        assert "pendingCreatePayload" in source


def test_comment_retry_reuses_id_and_does_not_rollback_images_when_outcome_unknown():
    editor = _read("components/ui/CommentEditor.js")
    dialog = _read("components/ui/TicketDialog.js")
    api = _read("lib/api.js")
    assert 'newMutationId("comment")' in editor
    assert "await this.submitFn(html, this.submitMutationId || null)" in editor
    assert "e.uncertain || e.needLogin" in editor
    assert "info.uploaded || null" in editor
    assert "mutationId: this.submitMutationId" in editor
    assert "this.submitMutationId = String(rec.mutationId" in editor
    assert "uploaded: im.uploaded || null" in editor
    assert "if (this.draftKey()) await this.flushDraft()" in editor
    assert "pendingHtml: this.pendingSubmitHtml" in editor
    assert "let html = this.pendingSubmitHtml || this._ed.getHTML()" in editor
    assert "await ed.discardDraft() === false" in dialog
    assert "if (this.submitMutationId)" in editor
    assert "submitNew(md, mutationId)" in dialog
    assert "commentCreate(this.tk, md, mutationId)" in dialog
    assert "{ html, clientMutationId }, { mutation: true }" in api
    assert 'fd.append("clientMutationId", mutationId)' in api
    assert "pending-upload.v1:" in api
    assert "info.uploadMutationId" in editor
    assert "api.attachmentUpload(this.ticketKey, file, info.uploadMutationId)" in editor


def test_create_dialogs_cannot_close_while_mutation_outcome_is_unknown():
    child = _read("components/ui/NewChildDialog.js")
    epic = _read("components/ui/EpicCreateDialog.js")
    for source in (child, epic):
        assert "if (this.busy || this.createMutationId)" in source
        assert '@click.self="fromBackdrop($event) && closeGuarded()"' in source
        assert '@click="closeGuarded()">취소</button>' in source
    assert "parentIsEpic: this.d.parent ? !!this.d.isEpic : null" in child


def test_pending_create_storage_outlives_renderer_restart_and_matches_receipt_ttl():
    ids = _read("lib/mutationId.js")
    assert 'PENDING_PREFIX = "pending-mutation.v1:"' in ids
    assert "PENDING_TTL_MS = 8 * 24 * 60 * 60 * 1000" in ids
    assert "export function savePendingMutation" in ids
    assert "export function loadPendingMutation" in ids
    assert "export function clearPendingMutation" in ids


def test_auth_error_keeps_server_reason_and_triggers_login_recovery():
    api = _read("lib/api.js")
    assert "b.detail || b.error" in api
    assert "error.needLogin" in api
    assert 'window.dispatchEvent(new CustomEvent("need-login"))' in api


def test_transition_retry_keeps_exact_payload_and_id_until_outcome_is_known():
    dialog = _read("components/ui/TransitionDialog.js")
    api = _read("lib/api.js")

    assert 'newMutationId("transition")' in dialog
    assert 'return "transition:" + String(this.ticket || "").toUpperCase()' in dialog
    assert "savePendingMutation(this.pendingScope(), this.transitionMutationId, payload" in dialog
    assert "loadPendingMutation(this.pendingScope())" in dialog
    assert "this.pendingTransitionPayload = payload" in dialog
    assert "r = await api.doTransition(this.ticket, payload)" in dialog
    assert "if (!(e && (e.uncertain || e.needLogin)))" in dialog
    assert "if (this.busy || this.transitionMutationId)" in dialog
    assert 'window.addEventListener("keydown", this._guardEscape, true)' in dialog
    assert 'window.removeEventListener("keydown", this._guardEscape, true)' in dialog
    assert '@click.self="fromBackdrop($event) && closeGuarded()"' in dialog
    assert '@click="closeGuarded">취소</button>' in dialog
    assert '"POST", payload, { mutation: true }' in api


def test_no_screen_transition_calls_share_the_same_durable_receipt_contract():
    api = _read("lib/api.js")
    tasks = _read("components/views/MyTasksView.js")
    root = _read("components/app-root.js")

    assert 'const scope = "transition-api:"' in api
    assert "payload = saved && saved.payload ? saved.payload" in api
    assert 'clientMutationId: newMutationId("transition")' in api
    assert "if (managed && !(error && (error.uncertain || error.needLogin)))" in api
    for source in (tasks, root):
        assert 'targetStatusId: t.toId || ""' in source
        assert 'targetStatusName: t.to || ""' in source
        assert 'targetStatusCategory: t.toCategory || ""' in source
    assert 't.toCategory === "done" || (fld.fields || []).length' in tasks
    assert 't.needsScreen || t.toCategory === "done"' in root


def test_done_transition_always_renders_shared_rich_comment_editor():
    dialog = _read("components/ui/TransitionDialog.js")

    assert 'needsComment() { return this.transition.toCategory === "done" || !!this.has.comment; }' in dialog
    assert 'if (this.needsComment && this.$refs.ed)' in dialog
    assert '<div v-if="needsComment" class="trx-f">' in dialog
    assert ':initial="pendingCommentHtml" :submit-fn="sendTransition"' in dialog
    assert 'targetStatusId: this.transition.toId || ""' in dialog
    assert 'targetStatusCategory: this.transition.toCategory || ""' in dialog
    assert dialog.index("created() {") < dialog.index("mounted() {")
    created = dialog[dialog.index("created() {"):dialog.index("mounted() {")]
    assert "loadPendingMutation(this.pendingScope())" in created
    assert "this.pendingTransitionPayload = payload" in created
    assert "if (field.system && !mapped[field.system]) mapped[field.system] = field" in dialog
    assert "Object.entries(source)" in dialog


def test_transition_editor_uses_isolated_durable_draft_and_explicit_discard():
    editor = _read("components/ui/CommentEditor.js")
    dialog = _read("components/ui/TransitionDialog.js")

    # Transition content/images use the shared IndexedDB machinery without colliding with the
    # ordinary `new:TICKET` comment draft. A saved exact transition payload remains parent-owned.
    assert 'draftScope: { type: String, default: "" }' in editor
    assert 'if (this.draftScope) return "scoped:" + String(this.draftScope)' in editor
    assert ':draft-scope="pendingScope()"' in dialog
    assert 'kind="transition"' in dialog
    assert 'this.kind === "comment" && !this.initial && !this.submitMutationId' in editor
    assert 'newMutationId("transition")' in dialog

    # Fast renderer/app reload and route unmount flush before the debounce can lose text/Blob.
    assert 'window.addEventListener("pagehide", this._flushDraftOnExit)' in editor
    assert 'document.addEventListener("visibilitychange", this._flushDraftWhenHidden)' in editor
    before_unmount = editor[editor.index("beforeUnmount() {"):editor.index("computed: {")]
    assert before_unmount.index("this.flushDraft()") < before_unmount.index("this._dead = true")
    assert 'window.removeEventListener("pagehide", this._flushDraftOnExit)' in before_unmount

    # Native close/backdrop/cancel asks before deleting; successful submit retains the existing
    # await-write-then-clear ordering so an unmount flush cannot resurrect the completed draft.
    close = dialog[dialog.index("async closeGuarded() {"):dialog.index("async initAssignee() {")]
    assert 'confirmBox("작성 중인 전환 코멘트를 버리고 닫을까요?", {' in close
    assert 'okLabel: "버리고 닫기", cancelLabel: "계속 작성", danger: true' in close
    assert "await editor.discardDraft() === false" in close
    assert close.index("confirmBox") < close.index("await editor.discardDraft()")
    assert 'if (event.key !== "Escape" || this._confirmingClose) return' in dialog
    submit = editor[editor.index("async submit() {"):editor.index("template: COMMENT_EDITOR_TEMPLATE")]
    assert "if (this._draftWrite)" in submit
    assert submit.index("if (this._draftWrite)") < submit.index("await clearDraft(dk)")
    assert '((this.kind === "comment" && !this.initial) || this.draftScope)' in submit
    assert submit.index("await clearDraft(dk)") < submit.index("this._ed.commands.clearContent(false)")
    discard = editor[editor.index("async discardDraft() {"):editor.index("isBlank() {")]
    assert "if (this.draftScope)" in discard
    assert "await api.attachmentDelete(this.ticketKey, id)" in discard
    assert "초안을 유지합니다" in discard and "return false" in discard
    assert 'info.uploadMutationId = ""' in discard

    rollback = submit[submit.index("if (!preserveUpload) {"):submit.index("this.err = (e && e.uncertain")]
    assert "if (removed)" in rollback
    assert 'info.uploadMutationId = ""' in rollback


def test_draft_keeps_structural_content_that_has_no_plain_text():
    editor = _read("components/ui/CommentEditor.js")
    flush = editor[editor.index("async flushDraft() {"):editor.index("async restoreDraft() {")]
    assert "<table\\b" in flush and "<input\\b" in flush and "<hr\\b" in flush
    assert "(!text && !imgs.length && !hasNode)" in flush
