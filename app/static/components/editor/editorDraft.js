import { saveDraft, loadDraft, clearDraft, purgeExpired } from "../../lib/draft.js";
import { resolveDraftKey } from "../../lib/draftIdentity.js";
import { newMutationId } from "../../lib/mutationId.js";
import { pushToast } from "../../lib/toast.js";
import { api } from "../../lib/api.js";

export const editorDraftMethods = {
    draftKey() { return this._resolvedDraftKey || null; },
    draftScopeName() {
      if (this.draftScope) return "scoped:" + String(this.draftScope);
      if (this.kind === "description") return "description:" + this.ticketKey;
      if (this.kind === "comment" && !this.initial) return "new:" + this.ticketKey;
      return null; // Existing comments provide their stable comment id as draftScope.
    },
    saveDraftSoon() {
      if (!this.draftScopeName() || !this._ed) return;
      this.flushDraft(); // Queue immediately: closing inside a debounce window must not lose text.
    },
    async flushDraft() {
      if (!this._ed || this._dead || this._draftFinished || this.draftConflict) return true;
      const scope = this.draftScopeName();
      if (!scope) return true;
      clearTimeout(this._dt); this._dt = null;
      let html = this._ed.getHTML();
      const text = (this._ed.getText() || "").trim();
      const imgs = [];
      for (const [url, info] of this._pending) {
        if (!html.includes(url)) continue;
        const token = info.draftToken || (info.draftToken = "draft:" + newMutationId("image"));
        html = html.split(url).join(token);
        imgs.push({ token, name: info.name, blob: info.blob, uploaded: info.uploaded || null,
                    uploadMutationId: info.uploadMutationId || "" });
      }
      const hasNode = /<img\b|<a\b|<input\b|<table\b|<pre\b|<blockquote\b|<h[1-6]\b|<hr\b/i.test(html);
      const empty = (!text && !imgs.length && !hasNode);
      const record = { html, images: imgs, baseHtml: this._baseHtml || "",
                       preserveExpiredForMutation: !!(this.submitAdapter && this.mutationPending),
                       mutationId: this.submitMutationId || "", pendingHtml: this.pendingSubmitHtml || "" };
      const write = (async () => {
        const k = this._resolvedDraftKey || await resolveDraftKey(scope);
        this._resolvedDraftKey = k;
        if ((empty && !this.initial) || (html === this._baseHtml && !this.submitMutationId)) await clearDraft(k);
        else await saveDraft(k, record);
      })();
      this._draftWrite = write;
      try { await write; this.draftError = ""; return true; }
      catch (error) { this.reportDraftError(error); return false; }
      finally { if (this._draftWrite === write) this._draftWrite = null; }
    },
    reportDraftError(error) {
      this.draftError = "초안 저장/복원 실패: " + ((error && error.message) || error);
      if (!this._draftWarned) {
        pushToast({ kind: "error", title: "초안 보존 확인 필요", message: this.draftError });
        this._draftWarned = true;
      }
    },
    async restoreDraft() {
      const scope = this.draftScopeName();
      if (!scope) return;
      try {
        const k = await resolveDraftKey(scope);
        this._resolvedDraftKey = k;
        purgeExpired().catch(error => this.reportDraftError(error));
        const rec = await loadDraft(k);
        if (!rec || !rec.html || this._dead || !this._ed) return;
        const editsServerContent = (this.kind === "description" && !this.draftScope)
          || (this.kind === "comment" && !!this.initial);
        if (editsServerContent && rec.baseHtml !== this._baseHtml && !rec.mutationId) {
          this._conflictingDraft = rec;
          this.draftConflict = true;
          return;
        }
        this.applyDraft(rec);
      } catch (error) { this.reportDraftError(error); }
    },
    applyDraft(rec) {
      this.submitMutationId = String(rec.mutationId || "");
      this.pendingSubmitHtml = String(rec.pendingHtml || "");
      let html = rec.html;
      for (const im of (rec.images || [])) {
        const url = URL.createObjectURL(im.blob);
        this._pending.set(url, { blob: im.blob, name: im.name, uploaded: im.uploaded || null,
                                 uploadMutationId: im.uploadMutationId || "", draftToken: im.token });
        html = html.split(im.token).join(url);
      }
      this._ed.commands.setContent(html, { emitUpdate: false });
      this.restored = true;
    },
    restoreConflictingDraft() {
      this.applyDraft(this._conflictingDraft);
      this._conflictingDraft = null;
      this.draftConflict = false;
      this.flushDraft();
    },
    async cancelEditing() {
      if (this.busy || this.submitting) return;
      if (await this.flushDraft() !== false && !this.busy && !this.submitting) this.$emit("cancel");
    },
    async discardDraft() {
      if (this.busy || this.submitting) return false;
      if (this.submitMutationId || this.mutationPending) {
        this.err = "Jira 반영 여부를 확인하기 전에는 이 초안을 버릴 수 없습니다. 로그인 또는 네트워크 복구 후 다시 등록해 주세요.";
        pushToast({ kind: "warn", icon: "⚠", title: "확인 중인 댓글",
                    message: "중복 작성을 막기 위해 반영 여부 확인이 끝날 때까지 초안을 유지합니다." });
        return false;
      }
      // A transition can be cancelled after one image upload succeeded but before the transition
      // request started (for example, auth failed on the next image). Do not silently orphan that
      // attachment. If cleanup itself cannot be confirmed, keep the scoped draft so it is retryable.
      if (this.draftScope) {
        for (const info of this._pending.values()) {
          const id = info.uploaded && info.uploaded.id;
          if (!id) continue;
          try { await api.attachmentDelete(this.ticketKey, id); }
          catch (e) {
            this.err = "첨부 정리를 확인하지 못해 초안을 유지합니다. 로그인 또는 네트워크 복구 후 다시 취소해 주세요.";
            pushToast({ kind: "warn", icon: "⚠", title: "전환 초안 유지",
                        message: this.err });
            return false;
          }
          info.uploaded = null;
          info.uploadMutationId = "";       // 삭제된 attachment receipt를 다음 upload에 재사용 금지
        }
      }
      const k = this.draftKey();
      clearTimeout(this._dt); this._dt = null;
      const prior = this._draftWrite;
      if (prior) { try { await prior; } catch (_) { /* 마지막 삭제가 최종 상태를 정한다 */ } }
      const write = k ? clearDraft(k) : null;
      this._draftWrite = write;
      if (write) {
        try { await write; }
        catch (error) { this.reportDraftError(error); return false; }
        finally { if (this._draftWrite === write) this._draftWrite = null; }
      }
      // Do not destroy the only visible copy until durable deletion is confirmed.
      try { for (const u of this._pending.keys()) URL.revokeObjectURL(u); } catch (e) { /* noop */ }
      this._pending.clear();
      this.submitMutationId = "";
      this.pendingSubmitHtml = "";
      if (this._ed) this._ed.commands.setContent(this._baseHtml || "", { emitUpdate: false });
      this.restored = false; this.draftConflict = false; this._conflictingDraft = null;
      this._draftFinished = true;
      return true;
    },
};
