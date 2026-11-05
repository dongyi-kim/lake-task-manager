// An embedded editor can publish to another service without invoking Jira attachment APIs.
export const editorSubmissionMethods = {
  async finishSubmission() {
    this._draftFinished = true;
    clearTimeout(this._dt); this._dt = null;
    if (this._draftWrite) { try { await this._draftWrite; } catch (_) { /* final deletion follows */ } }
    const { clearDraft } = await import("../../lib/draft.js");
    if (this.draftKey()) { try { await clearDraft(this.draftKey()); } catch (error) { this.reportDraftError(error); } }
    this._draftWrite = null;
    for (const url of this._pending.keys()) URL.revokeObjectURL(url);
    this._pending.clear(); this.submitMutationId = ""; this.pendingSubmitHtml = "";
    if (((this.kind === "comment" && !this.initial) || this.draftScope) && this._ed && !this._dead) {
      this._ed.commands.clearContent({ emitUpdate: false }); this.restored = false;
    }
  },
  async submitWithAdapter(html, jiraBase) {
    this.busy = true; this.err = ""; this._ed.setEditable(false);
    try {
      if (await this.flushDraft() === false) throw new Error(this.draftError);
      const files = [];
      for (const [url, info] of this._pending) {
        if (!html.includes(url)) continue;
        const token = info.draftToken;
        if (!token) throw new Error("첨부 초안 저장을 확인하지 못했습니다.");
        html = html.split(url).join(token);
        files.push({ token, name: info.name, blob: info.blob });
      }
      await this.submitAdapter({ html, files, jiraBase });
      await this.finishSubmission();
      this.$emit("submitted");
    } catch (error) {
      this.err = error.message || String(error);
    } finally {
      this.busy = false;
      if (this._ed && !this._dead) this._ed.setEditable(!this.readOnly, false);
    }
  },
};
