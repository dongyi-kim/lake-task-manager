// EpicCreateDialog.js — 최상위 Epic 만들기 창.
//
// 기본 필드(제목·Epic 이름·우선순위·컴포넌트·기한·담당자) + 설명(폴딩 에디터) + **기존 Task 선택**
// (이 Epic 에 함께 넣을 Task 를 검색해 다중선택). 제출: Epic 생성 → 선택 Task 에 Epic Link 설정 →
// (설명 있으면) 생성된 키로 이미지 업로드 + 설명 저장.
import { api } from "../../lib/api.js";
import Avatar from "./Avatar.js";
import TypeBadge from "./TypeBadge.js";
import FieldEdit from "./FieldEdit.js";
import PriIcon, { priRankOf } from "./PriIcon.js";
import CommentEditor from "./CommentEditor.js";
import { fromBackdrop } from "../../lib/backdrop.js";
import { isBusy, busyLabel } from "../../lib/uibusy.js";
import { pushToast } from "../../lib/toast.js";

export default {
  name: "EpicCreateDialog",
  components: { Avatar, TypeBadge, FieldEdit, PriIcon, CommentEditor },
  emits: ["close", "created"],
  data() {
    return {
      busy: false, err: "", priOpts: [], compOpts: [],
      descOpen: false, createdKey: "",
      ep: { summary: "", epicName: "", priority: "", components: [],
            duedate: "", assigneeId: "", assigneeName: "" },
      // 기존 Task 선택
      pickOpen: false, candQ: "", cands: [], candBusy: false,
      selected: [],           // [{key, summary, type}]
      // 이미 다른 Epic 에 속한 Task 는 기본으로 감춘다 — 새 Epic 에 담을 후보를 고르는 자리라
      // 대개 '소속 없는 것' 을 찾는다(이미 배정된 걸 빼앗아 오는 건 예외적 작업).
      excludeEpicLinked: true,
    };
  },
  computed: {
    canCreate() { return !!this.ep.summary.trim(); },
    selectedKeys() { return this.selected.map((t) => t.key); },
  },
  mounted() {
    api.options("components").then((r) => { this.compOpts = (r || []).map((x) => x.name); }).catch(() => {});
    api.options("priorities").then((r) => { this.priOpts = (r || []).map((x) => x.name); }).catch(() => {});
    this.$nextTick(() => { const el = this.$refs.sum; if (el) el.focus(); });
    document.addEventListener("keydown", this._onEsc = (e) => {
      if (e.key === "Escape") { e.stopPropagation(); this.closeGuarded(); }
    }, true);
    this.searchCands("");
  },
  unmounted() { document.removeEventListener("keydown", this._onEsc, true); clearTimeout(this._t); },
  methods: {
    fromBackdrop,
    // 생성 중에는 닫지 않는다 — 받아 놓은 글이 사라진다(실패하면 바로 풀린다).
    closeGuarded() {
      if (isBusy()) { pushToast(busyLabel() + " — 끝나면 닫을 수 있습니다.", "warn"); return; }
      this.$emit("close");
    },
    rankOf: priRankOf,
    setWho(id, u) {
      this.ep.assigneeId = id || "";
      this.ep.assigneeName = u ? (u.display || u.name || "") : "";
    },
    searchCands(q) {
      this.candQ = q;
      clearTimeout(this._t);
      this._t = setTimeout(() => {
        this.candBusy = true;
        const ex = this.excludeEpicLinked ? 1 : 0;      // 서버가 이미-Epic-속한 것을 걸러(자르기 전에)
        api.raw("/api/parent-task-candidates?limit=25&excludeLinked=" + ex + "&q=" + encodeURIComponent(q || ""))
          .then((r) => { this.cands = (r && r.items) || []; })
          .catch(() => { this.cands = []; })
          .finally(() => { this.candBusy = false; });
      }, 250);
    },
    toggleExcludeLinked() {
      this.excludeEpicLinked = !this.excludeEpicLinked;
      this.searchCands(this.candQ);                     // 조건이 바뀌었으니 다시 받는다
    },
    isSel(k) { return this.selectedKeys.includes(k); },
    toggleTask(c) {
      const i = this.selected.findIndex((t) => t.key === c.key);
      if (i >= 0) this.selected.splice(i, 1);
      else this.selected.push({ key: c.key, summary: c.summary, type: c.type, epicKey: c.epicKey });
    },
    removeSel(k) { const i = this.selected.findIndex((t) => t.key === k); if (i >= 0) this.selected.splice(i, 1); },
    async saveDesc(html) {
      const r = await api.updateFields(this.createdKey, { descriptionHtml: html });
      if (r && r.ok === false) throw new Error(r.error || "설명 저장 실패");
    },
    async submit() {
      if (!this.canCreate || this.busy) return;
      this.busy = true; this.err = "";
      try {
        const wantDesc = this.descOpen && this.$refs.ded && !this.$refs.ded.isBlank();
        const r = await api.createEpic({
          summary: this.ep.summary.trim(), epicName: this.ep.epicName.trim() || null,
          priority: this.ep.priority || null, components: this.ep.components.slice(),
          duedate: this.ep.duedate || null, assignee: this.ep.assigneeId || null,
          taskKeys: this.selectedKeys,
        });
        if (!r || r.ok === false) { this.err = (r && r.error) || "만들지 못했습니다."; return; }
        const key = r.key;
        if (wantDesc && key) {
          this.createdKey = key; await this.$nextTick();
          try { await this.$refs.ded.submit(); } catch (e) { /* 설명 실패해도 Epic 은 생성됨 */ }
        }
        // 일부 Task 연결 실패는 알리되 Epic 생성은 성공으로 넘긴다.
        if (r.link && r.link.failed && r.link.failed.length) {
          this.err = "일부 Task 연결 실패: " + r.link.failed.map((f) => f.key).join(", ");
        }
        this.$emit("created", key);
      } catch (e) {
        this.err = (e && e.message) || "만들지 못했습니다.";
      } finally { this.busy = false; }
    },
  },
  template: `
  <Teleport to="body">
  <div class="nk-ov" @click.self="fromBackdrop($event) && $emit('close')">
  <div class="nk nk-epic" @click.stop>
    <div class="nk-h">Epic 만들기
      <span class="nk-h-s">최상위 Epic 을 만들고, 기존 Task 를 담을 수 있습니다</span>
      <button class="lp-x" @click="closeGuarded()" aria-label="닫기">✕</button>
    </div>

    <input ref="sum" v-model="ep.summary" class="nk-sum" maxlength="200"
           placeholder="이 Epic 은 무엇인가요? (요약)" @keydown.enter.prevent="submit">

    <div class="nk-meta">
      <div><span class="k">Epic 이름</span><span class="val">
        <input v-model="ep.epicName" class="nk-mini" maxlength="60" placeholder="단축어 (비우면 요약)"></span></div>
      <div><span class="k">우선순위</span><span class="val">
        <FieldEdit ticket="__new__" field="priority" local :choices="priOpts"
                   :value="ep.priority" @pick="(v) => ep.priority = v">
          <template v-if="ep.priority"><PriIcon :rank="rankOf(ep.priority)" :name="ep.priority" /><span class="prio-n">{{ ep.priority }}</span></template>
          <span v-else class="muted">미지정</span>
        </FieldEdit></span></div>
      <div><span class="k">작업 기한</span><span class="val">
        <FieldEdit ticket="__new__" field="duedate" local :value="ep.duedate" @pick="(v) => ep.duedate = v">
          <span :class="{ muted: !ep.duedate }">{{ ep.duedate || '미지정' }}</span>
        </FieldEdit></span></div>
      <div><span class="k">컴포넌트</span><span class="val">
        <FieldEdit ticket="__new__" field="components" local :choices="compOpts"
                   :value="ep.components" @pick="(v) => ep.components = v">
          <span v-if="ep.components.length" class="tkt-labels">
            <span v-for="c in ep.components" :key="c" class="tkt-label comp">{{ c }}</span></span>
          <span v-else class="muted">미지정</span>
        </FieldEdit></span></div>
      <div><span class="k">담당자</span><span class="val val-user">
        <FieldEdit ticket="__new__" field="assignee" local :value="ep.assigneeId" :user-id="ep.assigneeId" @pick="setWho">
          <Avatar v-if="ep.assigneeId" :user="ep.assigneeId" :name="ep.assigneeName" :size="20" />
          <span v-else class="nk-noav" aria-hidden="true"></span>
          <span :class="{ muted: !ep.assigneeId }">{{ ep.assigneeName || '미지정' }}</span>
        </FieldEdit></span></div>
    </div>

    <!-- 기존 Task 선택 — 이 Epic 에 담는다(Epic Link 설정) -->
    <div class="nk-tasks">
      <button type="button" class="nk-desc-t" @click="pickOpen = !pickOpen">
        <span class="nk-desc-cav">{{ pickOpen ? '▾' : '▸' }}</span>기존 Task 담기
        <span v-if="selected.length" class="nk-cnt">{{ selected.length }}</span>
      </button>
      <div v-show="pickOpen" class="nk-tasks-body">
        <div v-if="selected.length" class="nk-sel">
          <span v-for="t in selected" :key="t.key" class="nk-sel-chip">
            <b>{{ t.key }}</b> {{ t.summary }}
            <button class="nk-sel-x" @click="removeSel(t.key)" aria-label="빼기">✕</button>
          </span>
        </div>
        <div class="nk-tsrow">
          <input class="nk-mini nk-tsearch" :value="candQ" @input="searchCands($event.target.value)"
                 placeholder="Task 검색 (키 또는 제목)">
          <button type="button" class="nk-voc nk-exep" :class="{ on: excludeEpicLinked }" role="switch"
                  :aria-checked="excludeEpicLinked" @click="toggleExcludeLinked"
                  title="이미 다른 Epic 에 속한 Task 를 목록에서 숨깁니다">
            <span class="nk-voc-k"></span><span class="nk-voc-t">이미 Epic 속한 것 제외</span>
          </button>
        </div>
        <div class="nk-cands">
          <div v-if="candBusy" class="muted nk-cand-empty">찾는 중…</div>
          <button v-for="c in cands" :key="c.key" type="button" class="nk-cand nk-cand-task"
                  :class="{ on: isSel(c.key) }" @click="toggleTask(c)">
            <span class="nk-cand-ck">{{ isSel(c.key) ? '☑' : '☐' }}</span>
            <TypeBadge :type="c.type" /><b>{{ c.key }}</b>
            <span class="nk-cand-s">{{ c.summary }}</span>
            <span v-if="c.epicKey" class="nk-cand-ep" :title="'현재 Epic: ' + (c.epicName || c.epicKey)">↳ {{ c.epicName || c.epicKey }}</span>
            <span v-if="c.assignee" class="nk-cand-asg" :title="c.assignee + ' 담당'">
              <Avatar :user="c.assigneeId" :name="c.assignee" :size="16" />{{ c.assignee }}</span>
          </button>
          <div v-if="!candBusy && !cands.length" class="muted nk-cand-empty">
            {{ excludeEpicLinked ? '소속 없는 Task 가 없습니다 (제외 토글을 꺼 보세요).' : '결과가 없습니다.' }}</div>
        </div>
      </div>
    </div>

    <!-- 설명(폴딩) -->
    <div class="nk-desc">
      <button type="button" class="nk-desc-t" @click="descOpen = !descOpen">
        <span class="nk-desc-cav">{{ descOpen ? '▾' : '▸' }}</span>설명 {{ descOpen ? '접기' : '추가 (선택)' }}
      </button>
      <div v-show="descOpen" class="nk-desc-body">
        <CommentEditor ref="ded" :ticket-key="createdKey || '__new__'" :submit-fn="saveDesc" hide-footer />
        <div class="nk-desc-hint">이미지·파일은 붙여넣거나 끌어다 놓으면 Epic 생성 후 함께 첨부됩니다.</div>
      </div>
    </div>

    <div v-if="err" class="tkt-cmt-err">{{ err }}</div>
    <div class="nk-f">
      <span class="nk-hint">{{ canCreate ? (selected.length ? selected.length + '개 Task 를 함께 담습니다.' : 'Task 는 나중에 담아도 됩니다.') : '제목을 입력하세요.' }}</span>
      <button class="cmt-ed-btn ghost" @click="$emit('close')">취소</button>
      <button class="cmt-ed-btn primary" :disabled="!canCreate || busy" @click="submit">
        {{ busy ? '만드는 중…' : 'Epic 만들기' }}</button>
    </div>
  </div>
  </div>
  </Teleport>`,
};
