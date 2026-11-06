import CommentEditor from "./CommentEditor.js";
import { api } from "../../lib/api.js";
import { saveDraft, loadDraft, clearDraft } from "../../lib/draft.js";
import { newMutationId } from "../../lib/mutationId.js";
import { resolveDraftKey } from "../../lib/draftIdentity.js";
import { confluenceStorage, mentionIds } from "../../lib/confluenceStorage.js";

export default {
  name: "ConfluenceComposer",
  components: { CommentEditor },
  props: { base: String, spaces: Array, space: String, parent: Object },
  emits: ["close", "created"],
  data() { return { ready: false, busy: false, error: "", progress: "", restored: false,
    formKey: "", draftId: "new", runHistory: [], knownParents: [],
    form: { title: "", space: this.parent?.space || this.space, parent: this.parent || null, numbered: true }, run: null }; },
  computed: {
    draftScope() { return "confluence:" + this.base + ":" + this.draftId; },
    locked() { return this.busy || !!this.run; },
    parentAvailable() { return this.form.parent && this.form.parent.space === this.form.space; },
    parentChoices() { return [...new Map([...this.knownParents, this.parent].filter(item => item && item.space === this.form.space).map(item => [item.id, item])).values()]; },
  },
  watch: { form: { deep: true, handler() { if (this.ready) this.persist().catch(error => { this.error = "초안 저장 실패: " + error.message; }); } } },
  async mounted() {
    try {
      this.formKey = await resolveDraftKey("scoped:confluence:" + this.base + ":form");
      const draft = await loadDraft(this.formKey);
      if (draft) { this.form = draft.form; this.run = draft.run; this.draftId = draft.draftId || "new"; this.runHistory = draft.runHistory || []; this.restored = true; }
      const history = await loadDraft(this.formKey + ":history");
      if (history) this.runHistory = history.items || [];
      this.knownParents = this.form.parent ? [this.form.parent] : [];
      this.ready = true;
    } catch (error) { this.error = "초안 복원 실패: " + error.message; }
  },
  methods: {
    persist() { return saveDraft(this.formKey, JSON.parse(JSON.stringify({ form: this.form, run: this.run,
      draftId: this.draftId, runHistory: this.runHistory, preserveExpiredForMutation: !!this.run }))); },
    chooseParent(id) { this.form.parent = this.parentChoices.find(item => item.id === id) || null; },
    async finishRun() {
      if (this.busy || !this.run) return;
      if (!window.confirm("Confluence에서 문서와 첨부의 반영 결과를 확인했습니까? 발행 기록을 남기고 새 초안을 시작합니다. 이미 발행된 문서는 삭제하지 않습니다.")) return;
      const history = [{ title: this.run.payload.title, pageId: this.run.page?.id || "", mutationId: this.run.payload.mutation_id,
        endedAt: Date.now() }, ...this.runHistory.filter(item => item.mutationId !== this.run.payload.mutation_id)].slice(0, 20);
      const draftId = newMutationId("conf-draft");
      const form = { title: "", space: this.form.space, parent: this.form.parent, numbered: this.form.numbered };
      try {
        // Commit the new body scope first. An interrupted cleanup cannot restore old content
        // as a fresh document; the closed mutation ID stays visible in publication history.
        await saveDraft(this.formKey + ":history", JSON.parse(JSON.stringify({ items: history })));
        await saveDraft(this.formKey, JSON.parse(JSON.stringify({ form, run: null, draftId, runHistory: history })));
        await this.$refs.editor.finishSubmission();
        this.draftId = draftId; this.runHistory = history; this.run = null; this.form = form; this.error = ""; this.progress = "";
      } catch (error) { this.error = "발행 기록 정리 실패: " + error.message; }
    },
    noop() {},
    async close() {
      if (this.busy) return;
      try {
        if (this.$refs.editor && await this.$refs.editor.flushDraft() === false) return;
        if (this.ready) await this.persist();
        this.$emit("close");
      } catch (error) { this.error = "초안 저장 실패: " + error.message; }
    },
    async discard() {
      if (this.locked) return;
      try {
        if (this.$refs.editor && await this.$refs.editor.discardDraft() === false) return;
        await clearDraft(this.formKey); this.$emit("close");
      } catch (error) { this.error = "초안 삭제 실패: " + error.message; }
    },
    async publish() {
      if (this.busy || !this.ready) return;
      if (!this.form.title.trim() || !this.form.space) { this.error = "Space와 문서 제목을 입력하세요."; return; }
      this.error = "";
      await this.$refs.editor.submit();
    },
    async publishDocument({ html, files, jiraBase }) {
      this.busy = true;
      try {
        if (!this.run) {
          const inlineImages = new Set([...new DOMParser().parseFromString(html, "text/html").querySelectorAll("img")]
            .map(node => node.getAttribute("src")));
          for (const file of files) if (!file.blob?.size || file.blob.size > 10 * 1024 * 1024)
            throw new Error(file.name + " — 파일은 0바이트 초과, 10MiB 이하만 첨부할 수 있습니다.");
          for (const file of files) if (inlineImages.has(file.token)
              && !["image/png", "image/jpeg", "image/gif", "image/webp", "image/avif"].includes(file.blob.type))
            throw new Error(file.name + " — 본문 이미지는 PNG, JPEG, GIF, WebP, AVIF 형식을 사용해 주세요.");
          const assets = files.map(file => ({ token: file.token, originalName: file.name,
            filename: "ltm-" + file.token.replace(/[^a-zA-Z0-9]/g, "").slice(-16) + "-" + file.name.replace(/[\\/\x00-\x1f]/g, "_").slice(-160),
            mutationId: newMutationId("conf-file"), uploaded: false }));
          this.progress = "발행 내용 확인 중…";
          const ids = mentionIds(html), users = ids.length ? await api.confluenceMentionUsers(ids) : {};
          const body = confluenceStorage(html, { assets, numbered: this.form.numbered, users, jiraBase });
          this.run = { startedAt: Date.now(), page: null, completed: false, assets,
            payload: { space: this.form.space, title: this.form.title.trim(), body,
              parent_id: this.parentAvailable ? this.form.parent.id : null, mutation_id: newMutationId("conf-page") } };
        }
        // A previous IDB failure can leave the in-memory run assigned. Every retry must
        // successfully persist its exact payload/ID before making any external write.
        await this.persist();
        if (this.run.completed) return;
        if (Date.now() - this.run.startedAt > 7 * 86400000)
          throw new Error("재시도 보존 기간이 지났습니다. 중복 생성을 막기 위해 자동 재발행하지 않습니다. Confluence에서 문서와 첨부를 확인해 주세요.");
        if (!this.run.page) {
          this.progress = "문서 생성 중…";
          this.run.page = await api.confluenceCreate(this.run.payload);
          await this.persist();
        }
        for (let index = 0; index < this.run.assets.length; index++) {
          const asset = this.run.assets[index];
          if (asset.uploaded) continue;
          const file = files.find(item => item.token === asset.token);
          if (!file) throw new Error("초안의 첨부 원본을 찾을 수 없습니다: " + asset.originalName);
          this.progress = `첨부 업로드 ${index + 1} / ${this.run.assets.length} · ${asset.originalName}`;
          await api.confluenceUpload(this.run.page.id, new File([file.blob], asset.filename,
            { type: file.blob.type || "application/octet-stream" }), asset.mutationId);
          asset.uploaded = true; await this.persist();
        }
        this.run.completed = true; await this.persist();
        this.progress = "발행 완료";
      } catch (error) {
        const prefix = this.run?.page ? "문서는 생성되었습니다. 완료한 첨부는 유지되며 남은 첨부만 재시도합니다. " : "";
        // A later preflight 4xx cannot disprove an earlier uncertain creation. Keep its ID.
        throw new Error(prefix + error.message);
      } finally { this.busy = false; }
    },
    async submitted() {
      const id = this.run?.page?.id;
      try { await clearDraft(this.formKey); }
      catch (error) { this.error = "발행은 완료했지만 초안 정리를 확인하지 못했습니다: " + error.message; return; }
      this.$emit("created", id);
    },
  },
  template: `
    <section class="conf-composer" aria-label="새 Confluence 문서" @keydown.esc.stop="close">
      <header class="conf-compose-header"><div><span class="conf-compose-eyebrow">CONFLUENCE</span><h2>새 문서</h2></div>
        <div class="conf-compose-actions"><button class="btn" :disabled="busy" @click="close">닫기 · 초안 보관</button>
          <button class="btn primary" :disabled="busy || !ready" @click="publish">{{ busy ? '발행 중…' : run?.completed ? '발행한 문서 보기' : run ? '발행 이어서 하기' : '발행' }}</button></div></header>
      <p v-if="error" class="conf-error" role="alert">{{ error }}</p>
      <p v-if="!ready && !error" class="conf-muted" role="status">초안 확인 중…</p>
      <div v-if="ready" class="conf-compose-scroll">
        <fieldset class="conf-compose-fields" :disabled="locked">
          <div class="conf-compose-location"><label>Space<select v-model="form.space" aria-label="새 문서 Space"><option v-for="item in spaces" :key="item.key" :value="item.key">{{ item.name }} ({{ item.key }})</option></select></label>
            <label>위치<select :value="parentAvailable ? form.parent.id : ''" @change="chooseParent($event.target.value)" aria-label="새 문서 위치">
              <option value="">Space 최상위</option><option v-for="item in parentChoices" :key="item.id" :value="item.id">{{ item.title }} 아래</option></select></label></div>
          <input class="conf-compose-title" v-model="form.title" maxlength="255" aria-label="문서 제목" placeholder="문서 제목">
          <label class="conf-number-toggle"><input type="checkbox" v-model="form.numbered">헤딩 자동 번호 <span>1 → 1.1 → 1.1.1</span></label>
          <CommentEditor :key="draftId" ref="editor" ticket-key="" kind="confluence" :submit-fn="noop" :submit-adapter="publishDocument"
            :draft-scope="draftScope" :read-only="locked" :mutation-pending="!!run" :heading-numbering="true" :auto-number-headings="form.numbered"
            :hide-footer="true" :allow-empty="true" :initial-height="400" height-key="lake.confluence.editor.height"
            placeholder="문서를 작성하세요. / 로 표·코드·링크 넣기, @ 로 멘션, 파일·이미지 붙여넣기와 드래그" @submitted="submitted" />
        </fieldset>
        <div class="conf-compose-note"><button class="btn" :disabled="locked" @click="$refs.editor.tbImage()">파일·이미지 첨부</button><span>초안에 보관되며 발행할 때 첨부됩니다. 파일당 최대 10MiB.</span>
          <button v-if="!locked" class="conf-more" @click="discard">초안 삭제</button></div>
        <p v-if="progress || run?.page" role="status" class="conf-compose-progress">{{ progress || ('문서 #' + run.page.id + ' 생성됨 · 남은 첨부를 이어서 발행하세요.') }}</p>
        <button v-if="run && !busy" class="conf-more" @click="finishRun">결과 확인 완료 · 새 초안 시작</button>
        <details v-if="runHistory.length" class="conf-compose-note"><summary>종료한 발행 기록 ({{ runHistory.length }})</summary><ul><li v-for="item in runHistory" :key="item.mutationId">{{ item.title }} · {{ item.pageId ? '#' + item.pageId : '결과 직접 확인' }} <small>{{ item.mutationId }}</small></li></ul></details>
      </div>
    </section>`,
};
