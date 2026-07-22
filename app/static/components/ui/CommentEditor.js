// CommentEditor.js — Milkdown Crepe 기반 댓글 작성/수정 에디터.
// · 신버전 위키/노션식 WYSIWYG: '# '·'- '·'1. '·'> '·백틱3개 등 마크다운 입력이 실시간 변환.
//   슬래시(/) 블록 메뉴 · 플로팅 툴바 · 표 행/열 편집 · 코드블록(언어) 내장.
// · 이미지 붙여넣기/드래그드롭 = **제출 시 업로드**(upload-on-submit): 붙여넣는 순간엔 로컬
//   objectURL 미리보기만, 제출할 때 첨부로 올려 실제 파일명으로 !파일명! 치환. 저장 실패/취소
//   시 이번에 올린 첨부는 롤백(삭제) → 취소/실패 시 서버에 흔적이 안 남는다. 창 닫으면 draft 소멸.
// 부모는 submitFn(finalMarkdown) 만 넘긴다(작성=commentCreate / 수정=commentUpdate 를 부모가 선택).
import { loadCrepe } from "../../lib/milkdown.js";
import { api } from "../../lib/api.js";

export default {
  name: "CommentEditor",
  props: {
    ticketKey: { type: String, required: true },
    initial: { type: String, default: "" },            // 수정 시 기존 markdown
    submitLabel: { type: String, default: "등록" },
    submitFn: { type: Function, required: true },       // async (markdown) => any (실패 시 throw)
  },
  emits: ["submitted", "cancel"],
  data() { return { ready: false, loadErr: "", busy: false, err: "" }; },
  async mounted() {
    this._pending = new Map();        // objectURL -> { blob, name }
    this._seq = 0;
    let mod;
    try {
      mod = await loadCrepe(document.documentElement.getAttribute("data-theme") === "dark");
    } catch (e) {
      this.loadErr = "에디터를 불러오지 못했습니다(네트워크/CDN 차단). 잠시 후 다시 시도하세요.";
      return;
    }
    if (this._dead) return;           // 로드 도중 언마운트되면 중단
    const { Crepe, CrepeFeature } = mod;
    const featureConfigs = {};
    // 이미지: 붙여넣기/드롭 시 호출됨 — 지금은 로컬 objectURL 만 돌려주고(미리보기), 추적해뒀다
    //          제출 때 실제 업로드한다.
    featureConfigs[CrepeFeature.ImageBlock] = {
      onUpload: async (file) => {
        const ext = ((file.type || "").split("/")[1] || "png").replace("jpeg", "jpg");
        const name = "paste-" + Date.now() + "-" + (++this._seq) + "." + ext;
        const url = URL.createObjectURL(file);
        this._pending.set(url, { blob: file, name });
        return url;
      },
    };
    this.crepe = new Crepe({ root: this.$refs.ed, defaultValue: this.initial || "", featureConfigs });
    await this.crepe.create();
    if (this._dead) { try { this.crepe.destroy(); } catch (e) { /* noop */ } return; }
    this.ready = true;
  },
  beforeUnmount() {
    this._dead = true;
    try { for (const u of this._pending.keys()) URL.revokeObjectURL(u); } catch (e) { /* noop */ }
    try { if (this.crepe) this.crepe.destroy(); } catch (e) { /* noop */ }
  },
  methods: {
    async submit() {
      if (this.busy || !this.crepe) return;
      let md = (this.crepe.getMarkdown() || "").trim();
      if (!md) { this.err = "내용을 입력하세요."; return; }
      this.busy = true; this.err = "";
      const uploaded = [];             // 이번에 올린 첨부 id (실패 시 롤백)
      try {
        // 본문에 남아있는 붙여넣기 이미지만 업로드 → 실제 파일명으로 치환.
        for (const [url, info] of this._pending) {
          if (!md.includes(url)) continue;      // 중간에 지운 이미지는 업로드 안 함
          const file = new File([info.blob], info.name, { type: info.blob.type || "image/png" });
          const res = await api.attachmentUpload(this.ticketKey, file);
          uploaded.push(res.id);
          md = md.split("(" + url + ")").join("(" + res.filename + ")");   // (objectURL)→(파일명)
        }
        await this.submitFn(md);
        for (const u of this._pending.keys()) URL.revokeObjectURL(u);
        this._pending.clear();
        this.$emit("submitted");
      } catch (e) {
        // 롤백: 이번 제출에서 올린 첨부만 삭제(취소/실패 시 흔적 제거)
        for (const id of uploaded) {
          try { await api.attachmentDelete(this.ticketKey, id); } catch (_) { /* best effort */ }
        }
        this.err = "저장 실패: " + ((e && e.message) || e);
      } finally {
        this.busy = false;
      }
    },
  },
  template: `
  <div class="cmt-editor">
    <div v-if="loadErr" class="cmt-ed-err">{{ loadErr }}
      <button class="cmt-ed-btn" @click="$emit('cancel')">닫기</button>
    </div>
    <template v-else>
      <div ref="ed" class="cmt-ed-host"></div>
      <div class="cmt-ed-bar">
        <span v-if="err" class="cmt-ed-msg">{{ err }}</span>
        <button class="cmt-ed-btn ghost" :disabled="busy" @click="$emit('cancel')">취소</button>
        <button class="cmt-ed-btn primary" :disabled="busy || !ready" @click="submit">
          {{ busy ? '저장 중…' : submitLabel }}</button>
      </div>
    </template>
  </div>`,
};
