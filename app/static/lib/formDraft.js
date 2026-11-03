import { clearDraft, loadDraft, saveDraft } from "./draft.js";
import { resolveDraftKey } from "./draftIdentity.js";
import { pushToast } from "./toast.js";
import { confirmBox } from "./confirm.js";

// Forms own field/receipt state; CommentEditor owns HTML/Blobs in the same immutable scope.
export function formDraft(scope, fields, editorRef = "ded") {
  return {
    data: () => ({ formDraftReady: false, formDraftError: "", formDraftRestored: false }),
    computed: { formDraftScope() { return scope.call(this); } },
    created() {
      this.captureFormBaseline();
      this._formReady = this.restoreFormDraft();
      this._formReady.catch(() => {});
      this._formExit = () => { this.flushFormDraft().catch(() => {}); };
      window.addEventListener("pagehide", this._formExit);
      this._formHidden = () => { if (document.visibilityState === "hidden") this._formExit(); };
      document.addEventListener("visibilitychange", this._formHidden);
    },
    mounted() {
      this.$watch(() => fields.map(field => this[field]), () => {
        if (this.formDraftReady && !this._formFinished) this.flushFormDraft().catch(() => {});
      }, { deep: true });
    },
    beforeUnmount() {
      this._formExit();
      this._formDead = true;
      window.removeEventListener("pagehide", this._formExit);
      document.removeEventListener("visibilitychange", this._formHidden);
    },
    methods: {
      captureFormBaseline() {
        this._formBaseline = Object.fromEntries(fields.map(field => [field, JSON.stringify(this[field])]));
      },
      draftError(error) {
        this.formDraftError = "초안 저장/복원 실패: " + (error.message || error);
        if (!this._draftWarned) {
          pushToast({ kind: "error", title: "초안 보존 확인 필요", message: this.formDraftError });
          this._draftWarned = true;
        }
      },
      async restoreFormDraft() {
        try {
          this._formKey = await resolveDraftKey("form:" + this.formDraftScope);
          const record = await loadDraft(this._formKey);
          if (this._formDead) return;
          if (record && record.fields) {
            // Local pickers stay usable while identity/storage loads. Do not overwrite an input
            // already changed by the user; an uncertain/confirmed write always restores its exact form.
            const receipt = record.fields.createdKey || record.fields.createMutationId || record.fields.transitionMutationId;
            for (const field of fields) {
              if (Object.hasOwn(record.fields, field)
                  && (receipt || JSON.stringify(this[field]) === this._formBaseline[field])) this[field] = record.fields[field];
            }
            this._userTouched = true;
            this.formDraftRestored = true;
          }
          this.formDraftReady = true;
          this.formDraftError = "";
        } catch (error) { this.draftError(error); throw error; }
      },
      async flushFormDraft() {
        if (this._formFinished) return;
        if (!this.formDraftReady) await this._formReady;
        if (!this.formDraftReady || this._formFinished) return;
        const values = {};
        for (const field of fields) values[field] = this[field];
        try {
          await saveDraft(this._formKey, { fields: JSON.parse(JSON.stringify(values)) });
          this.formDraftError = "";
        } catch (error) { this.draftError(error); throw error; }
      },
      async preserveAndClose() {
        try {
          const editor = this.$refs[editorRef];
          if (editor && editor.flushDraft && await editor.flushDraft() === false) return;
          await this.flushFormDraft();
          this.$emit("close");
        } catch (_) { /* Persistence failure keeps the visible draft. */ }
      },
      async finishFormDraft(submitted = false) {
        this._formFinished = true;
        try { if (this._formKey) await clearDraft(this._formKey); }
        catch (error) {
          this.draftError(error);
          // A completed Jira mutation must not become an upload rollback or a duplicate retry
          // just because local cleanup failed. Its persisted receipt still reconciles a reopen.
          if (!submitted) throw error;
        }
      },
      async discardFormDraft() {
        if (this.busy || this.createMutationId || this.transitionMutationId) return;
        if (!await confirmBox("저장된 초안을 삭제할까요?", { okLabel: "초안 삭제", danger: true })) return;
        const editor = this.$refs[editorRef];
        if (editor && editor.discardDraft && await editor.discardDraft() === false) return;
        try { await this.finishFormDraft(); this.$emit("close"); }
        catch (error) { this._formFinished = false; this.draftError(error); }
      },
    },
  };
}
