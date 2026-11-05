from pathlib import Path


STATIC = Path(__file__).resolve().parents[3] / "app" / "static"


def _read(path):
    return (STATIC / path).read_text(encoding="utf-8")



def test_comment_retry_reuses_id_and_does_not_rollback_images_when_outcome_unknown():
    editor = _read("components/ui/CommentEditor.js") + _read("components/editor/editorDraft.js")
    dialog = _read("components/ui/TicketDialog.js")
    api = _read("lib/api.js")
    assert 'newMutationId("comment")' in editor
    assert "await this.submitFn(html, this.submitMutationId || null)" in editor
    assert "e.uncertain || e.needLogin" in editor
    assert "info.uploaded || null" in editor
    assert "mutationId: this.submitMutationId" in editor
    assert "this.submitMutationId = String(rec.mutationId" in editor
    assert "uploaded: im.uploaded || null" in editor
    assert "await this.flushDraft() === false" in editor
    assert "pendingHtml: this.pendingSubmitHtml" in editor
    assert "let html = this.pendingSubmitHtml || this._ed.getHTML()" in editor
    assert "await ed.flushDraft() === false" in dialog
    assert "this.submitMutationId || this.mutationPending" in editor
    assert "submitNew(md, mutationId)" in dialog
    assert "commentCreate(this.tk, md, mutationId)" in dialog
    assert "{ html, clientMutationId }, { mutation: true }" in api
    assert 'fd.append("clientMutationId", mutationId)' in api
    assert "pending-upload.v1:" in api
    assert "info.uploadMutationId" in editor
    assert "api.attachmentUpload(this.ticketKey, file, info.uploadMutationId)" in editor



def test_pending_create_storage_outlives_renderer_restart_and_matches_receipt_ttl():
    ids = _read("lib/mutationId.js")
    assert 'PENDING_PREFIX = "pending-mutation.v1:"' in ids
    assert "PENDING_TTL_MS = 8 * 24 * 60 * 60 * 1000" in ids
    assert "export function savePendingMutation" in ids
    assert "export function loadPendingMutation" in ids
    assert "export function clearPendingMutation" in ids


def test_auth_error_keeps_server_reason_and_triggers_login_recovery():
    api = _read("lib/api.js")
    assert "body.detail || body.error" in api
    assert "error.needLogin" in api
    assert 'window.dispatchEvent(new CustomEvent("need-login", { detail: { silentFailed: true } }))' in api



def test_no_screen_transition_calls_share_the_same_durable_receipt_contract():
    api = _read("lib/api.js")
    tasks = _read("components/views/MyTasksView.js")
    root = _read("components/app-root.js")

    assert 'const scope = "transition-api:"' in api
    assert "payload = saved && saved.payload ? saved.payload" in api
    assert 'clientMutationId: newMutationId("transition")' in api
    assert "if (managed && !(error && (error.uncertain || error.needLogin || error.authRecovered || error.authRecoveryPending)))" in api
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
    assert '<CommentEditor v-if="formDraftReady"' in dialog
    assert "await this._formReady" in dialog
    assert "if (field.system && !mapped[field.system]) mapped[field.system] = field" in dialog
    assert "Object.entries(source)" in dialog



def test_draft_keeps_structural_content_that_has_no_plain_text():
    editor = _read("components/ui/CommentEditor.js") + _read("components/editor/editorDraft.js")
    flush = editor[editor.index("async flushDraft() {"):editor.index("async restoreDraft() {")]
    assert "<table\\b" in flush and "<input\\b" in flush and "<hr\\b" in flush
    assert "(!text && !imgs.length && !hasNode)" in flush
