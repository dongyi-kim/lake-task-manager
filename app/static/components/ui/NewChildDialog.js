// NewChildDialog.js — 하위/독립 티켓 만들기 창.
//
// **상위 선택과 내용 입력을 한 창에서** 한다. 예전엔 상위 검색 오버레이가 뜬 뒤 별도 입력 창이
// 2중으로 떠(대기·상위 확인 불가) 답답했다 — 여기서 상위를 고르면 바로 아래 폼이 펼쳐지고,
// 고른 상위는 머리줄에 칩으로 계속 보인다.
//   · 티켓 내부의 '＋' 로 열면(parent 지정) 상위가 **상수로 고정**된다(변경 불가).
//   · 좌하단 FAB 로 열면(pickKind 지정) 이 창에서 **상위를 검색해 고른다**('Epic 없음' 포함).
//
// 입력 방식은 티켓 정보와 같다 — 값이 그대로 보이고 누르면 같은 팝업(FieldEdit)이 열린다.
// 필수(제목·우선순위·타입)는 비워 둔 채 연다. 기한/컴포넌트는 상위 것을 물려받는다.
import { api } from "../../lib/api.js";
import Avatar from "./Avatar.js";
import TypeBadge from "./TypeBadge.js";
import FieldEdit from "./FieldEdit.js";
import PriIcon, { priRankOf } from "./PriIcon.js";
import CommentEditor from "./CommentEditor.js";
import { fromBackdrop } from "../../lib/backdrop.js";
import { isBusy, busyLabel } from "../../lib/uibusy.js";
import { pushToast } from "../../lib/toast.js";
import { categoryColor } from "../../lib/colors.js";

// Task 상위 고르기에서 Epic 대신 고를 수 있는 특수 옵션(맨 위 고정). ('사용자 VoC' 는 상위가 아니라
// 아래 토글로 받는다 — Epic 에 속한 VoC 도 있어 상위 선택과 배타적이면 안 된다.)
const SPECIALS = [{ key: "__none__", special: "none", label: "Epic 없음", desc: "Epic 없이 Task 만들기" }];

export default {
  name: "NewChildDialog",
  components: { Avatar, TypeBadge, FieldEdit, PriIcon, CommentEditor },
  props: {
    // ── 티켓 내부('＋')에서 열 때: 상위 상수 고정 ──
    parent: { type: String, default: "" },         // 부모 티켓 키(고정)
    isEpic: { type: Boolean, default: false },     // Epic 밑 Task 인가(아니면 Sub-Task)
    standalone: { type: Boolean, default: false }, // Epic 없이 최상위 Task
    types: { type: Array, default: () => [] },     // 만들 수 있는 타입(서버가 부모 보고 정한 목록)
    parentDue: { type: String, default: "" },
    parentComponents: { type: Array, default: () => [] },
    // ── FAB 에서 열 때: 이 창에서 상위 선택 ── '' | 'epic'(Task 만들기) | 'task'(Sub 만들기)
    pickKind: { type: String, default: "" },
  },
  emits: ["close", "created"],
  data() {
    return {
      busy: false, err: "", priOpts: [], compOpts: [],
      descOpen: false, createdKey: "", voc: false,
      nc: { type: "", summary: "", priority: "", components: [],
            duedate: "", assigneeId: "", assigneeName: "" },
      // 해소된 상위 컨텍스트(고정이면 즉시, FAB 이면 고른 뒤 채워진다)
      d: { parent: "", isEpic: false, standalone: false, types: [], due: "", comps: [], plabel: "" },
      resolved: false,     // 상위가 정해졌나 → 입력 폼 노출
      fixed: false,        // 티켓 내부에서 열려 상위가 상수(변경 불가)
      // 상위 선택(FAB) 상태
      pq: "", plist: [], pbusy: false,
    };
  },
  computed: {
    // Sub-Task 를 만드는 창인가 — 제목/타입 판단에 쓴다.
    creatingSub() { return this.pickKind === "task" || (this.fixed && !this.d.isEpic && !this.d.standalone); },
    title() { return this.creatingSub ? "Sub Task 만들기" : "Task 만들기"; },
    canCreate() { return this.resolved && !!(this.nc.type && this.nc.priority && this.nc.summary.trim()); },
    pickType() { return (this.d.isEpic || this.d.standalone) && this.d.types.length > 1; },
    isTask() { return this.d.isEpic || this.d.standalone; },
    needPick() { return !!this.pickKind && !this.resolved; },
  },
  mounted() {
    api.options("components").then((r) => { this.compOpts = (r || []).map((x) => x.name); }).catch(() => {});
    api.options("priorities").then((r) => { this.priOpts = (r || []).map((x) => x.name); }).catch(() => {});
    // 상위 컨텍스트: FAB(pickKind, 상위 미지정) 이면 이 창에서 고르고, 그 외엔 props 로 즉시 해소.
    if (this.pickKind && !this.parent && !this.standalone) {
      this.searchParents("");
      this.$nextTick(() => { const el = this.$refs.psearch; if (el) el.focus(); });
    } else {
      this.fixed = !this.standalone;    // 상위 상수(변경 불가). standalone(Epic 없음)은 고정 아님
      this._resolve({ parent: this.parent, isEpic: this.isEpic, standalone: this.standalone,
                      types: this.types, due: this.parentDue, comps: this.parentComponents, plabel: "" });
    }
    document.addEventListener("keydown", this._onEsc = (e) => {
      if (e.key === "Escape") { e.stopPropagation(); this.closeGuarded(); }
    }, true);
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
    epicColor(key) { return categoryColor(key); },
    setWho(id, u) {
      this.nc.assigneeId = id || "";
      this.nc.assigneeName = u ? (u.display || u.name || "") : "";
    },
    // ── 상위 선택(FAB) ──
    searchParents(q) {
      this.pq = q;
      clearTimeout(this._t);
      this._t = setTimeout(() => {
        this.pbusy = true;
        const isSub = this.pickKind === "task";
        const p = isSub
          ? api.parentTaskCandidates(q).then((r) => (r && r.items) || [])
          : api.options("epics", q).then((r) => (r || []).map(
              (e) => ({ key: e.key, summary: e.summary || "", name: e.name || e.key, type: "Epic" })));
        p.then((items) => {
          let list = items || [];
          if (!isSub) {                                   // Task 상위 = Epic → 'Epic 없음' 을 맨 위에
            const q2 = (q || "").trim().toLowerCase();
            list = SPECIALS.filter((s) => !q2
              || s.label.toLowerCase().includes(q2) || s.desc.toLowerCase().includes(q2)).concat(list);
          }
          this.plist = list;
        }).catch(() => { this.plist = []; }).finally(() => { this.pbusy = false; });
      }, 250);
    },
    async pickParent(item) {
      if (item.special) {                               // 'Epic 없음' → 독립 Task
        const types = await api.taskTypes().catch(() => []);
        this._resolve({ parent: "", isEpic: false, standalone: true, types: types || [],
                        due: "", comps: [], plabel: "Epic 없음" });
        return;
      }
      const parent = item.key;
      let types = [], due = "", comps = [];
      try {
        const [t, v] = await Promise.all([
          api.childTypes(parent).catch(() => []),
          api.ticket(parent).catch(() => null),
        ]);
        types = t || [];
        if (v) { due = v.due || ""; comps = (v.components || []).slice(); }
      } catch (e) { /* 최소값으로 연다 */ }
      this._resolve({ parent, isEpic: this.pickKind === "epic", standalone: false,
                      types, due, comps, plabel: item.name || item.summary || "" });
    },
    reopenPick() {                                      // 상위 다시 고르기(고정이 아닐 때만)
      if (this.fixed) return;
      this.resolved = false;
      this.searchParents(this.pq || "");
      this.$nextTick(() => { const el = this.$refs.psearch; if (el) el.focus(); });
    },
    _resolve(ctx) {
      this.d = ctx;
      this.resolved = true;
      // 타입: 고를 게 여럿이면 비우고, 하나뿐이면 박는다.
      this.nc.type = this.pickType ? "" : (ctx.types[0] || "");
      // 기한·컴포넌트는 상위 것을 물려받는다. '사용자 VoC' 는 토글로 분리.
      this.nc.duedate = this.nc.duedate || ctx.due || "";
      const pc = (ctx.comps || []).slice();
      if (pc.includes("사용자 VoC")) this.voc = true;
      this.nc.components = pc.filter((c) => c !== "사용자 VoC");
      this.$nextTick(() => { const el = this.$refs.sum; if (el) el.focus(); });
    },
    // ── 설명·제출 ──
    async saveDesc(html) {
      const r = await api.updateFields(this.createdKey, { descriptionHtml: html });
      if (r && r.ok === false) throw new Error(r.error || "설명 저장 실패");
    },
    async submit() {
      if (!this.canCreate || this.busy) return;
      this.busy = true; this.err = "";
      try {
        const wantDesc = this.descOpen && this.$refs.ded && !this.$refs.ded.isBlank();
        const createDesc = wantDesc && !this.$refs.ded.hasPendingUploads()
          ? this.$refs.ded.htmlValue() : null;
        const comps = this.nc.components.filter((c) => c !== "사용자 VoC");
        if (this.voc && this.isTask) comps.push("사용자 VoC");
        const payload = {
          type: this.nc.type, summary: this.nc.summary.trim(), priority: this.nc.priority,
          duedate: this.nc.duedate || null, assignee: this.nc.assigneeId || null,
          components: comps, descriptionHtml: createDesc,
        };
        // standalone 은 부모가 없어 /api/task 로, 그 외엔 부모 밑으로(/api/ticket/{parent}/child).
        // 생성 뒤 설명 저장이 실패해도 재시도할 때 티켓을 중복 생성하지 않는다.
        let key = this.createdKey;
        if (!key) {
          const r = this.d.standalone ? await api.createTask(payload)
                                      : await api.createChild(this.d.parent, payload);
          if (!r || r.ok === false) { this.err = (r && r.error) || "만들지 못했습니다."; return; }
          key = r.key;
          this.createdKey = key;
        }
        if (wantDesc && key) {
          await this.$nextTick();
          await this.$refs.ded.submit();
          if (this.$refs.ded.err) throw new Error(this.$refs.ded.err);
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
  <div class="nk" @click.stop>
    <div class="nk-h">{{ title }}
      <button class="lp-x" @click="closeGuarded()" aria-label="닫기">✕</button>
    </div>

    <!-- 상위 — 고정(상수) / 고른 결과(칩+변경) / 고르는 중(검색) -->
    <div class="nk-parent">
      <span class="k">상위</span>
      <template v-if="resolved">
        <span class="nk-parent-chip" :class="{ fixed }">
          <template v-if="d.standalone"><span class="nk-sp-ic">⊘</span>Epic 없음</template>
          <template v-else><b>{{ d.parent }}</b><span v-if="d.plabel" class="nk-parent-s">{{ d.plabel }}</span></template>
        </span>
        <button v-if="!fixed" type="button" class="nk-parent-chg" @click="reopenPick">변경</button>
      </template>
      <input v-else ref="psearch" class="nk-mini nk-tsearch" :value="pq" @input="searchParents($event.target.value)"
             :placeholder="(pickKind === 'task' ? '상위 Task' : '상위 Epic') + ' 검색 (키 또는 제목)'">
    </div>

    <!-- 상위 후보(고르는 중) -->
    <div v-if="needPick" class="nk-cands nk-cands-tall">
      <div v-if="pbusy" class="muted nk-cand-empty">찾는 중…</div>
      <template v-for="c in plist" :key="c.key">
        <button v-if="c.special" type="button" class="nk-cand nk-cand-sp" @click="pickParent(c)">
          <span class="nk-sp-ic">⊘</span><b class="nk-sp-t">{{ c.label }}</b><span class="nk-cand-s">{{ c.desc }}</span>
        </button>
        <button v-else-if="pickKind === 'epic'" type="button" class="nk-cand nk-cand-epic" @click="pickParent(c)">
          <b>{{ c.key }}</b><span class="nk-cand-s">{{ c.summary || c.name }}</span>
          <span class="nk-epic-badge" :style="{ '--ec': epicColor(c.key) }">{{ c.name }}</span>
        </button>
        <button v-else type="button" class="nk-cand nk-cand-task" @click="pickParent(c)">
          <TypeBadge :type="c.type" /><b>{{ c.key }}</b><span class="nk-cand-s">{{ c.summary }}</span>
          <span v-if="c.epicKey" class="nk-epic-badge sm" :style="{ '--ec': epicColor(c.epicKey) }"
                :title="'소속 Epic: ' + (c.epicName || c.epicKey)">{{ c.epicName || c.epicKey }}</span>
          <span v-if="c.assignee" class="nk-cand-asg" :title="c.assignee + ' 담당'">
            <Avatar :user="c.assigneeId" :name="c.assignee" :size="16" />{{ c.assignee }}</span>
        </button>
      </template>
      <div v-if="!pbusy && !plist.length" class="muted nk-cand-empty">결과가 없습니다.</div>
    </div>

    <!-- 입력 폼 — 상위가 정해지면 펼친다(같은 창) -->
    <template v-if="resolved">
    <input ref="sum" v-model="nc.summary" class="nk-sum" maxlength="200"
           :placeholder="creatingSub ? '무엇을 할 작업인가요?' : '무엇을 할 Task 인가요?'"
           @keydown.enter.prevent="submit">

    <div class="nk-meta">
      <div><span class="k">우선순위</span><span class="val">
        <FieldEdit :ticket="d.parent" field="priority" local :choices="priOpts"
                   :value="nc.priority" @pick="(v) => nc.priority = v">
          <template v-if="nc.priority">
            <PriIcon :rank="rankOf(nc.priority)" :name="nc.priority" /><span class="prio-n">{{ nc.priority }}</span>
          </template>
          <span v-else class="nk-need">필수입력</span>
        </FieldEdit></span></div>

      <div v-if="pickType"><span class="k">티켓 타입</span><span class="val">
        <FieldEdit :ticket="d.parent" field="issuetype" local :choices="d.types"
                   :value="nc.type" @pick="(v) => nc.type = v">
          <TypeBadge v-if="nc.type" :type="nc.type" />
          <span v-else class="nk-need">필수입력</span>
        </FieldEdit></span></div>
      <div v-else><span class="k">티켓 타입</span><span class="val">
        <TypeBadge :type="nc.type" /></span></div>

      <div><span class="k">작업 기한</span><span class="val">
        <FieldEdit :ticket="d.parent" field="duedate" local :value="nc.duedate"
                   @pick="(v) => nc.duedate = v">
          <span :class="{ muted: !nc.duedate }">{{ nc.duedate || '미지정' }}</span>
        </FieldEdit></span></div>

      <div><span class="k">컴포넌트</span><span class="val">
        <FieldEdit :ticket="d.parent" field="components" local :choices="compOpts"
                   :value="nc.components" @pick="(v) => nc.components = v">
          <span v-if="nc.components.length" class="tkt-labels">
            <span v-for="c in nc.components" :key="c" class="tkt-label comp">{{ c }}</span>
          </span>
          <span v-else class="muted">미지정</span>
        </FieldEdit></span></div>

      <div v-if="isTask"><span class="k">사용자 VoC</span><span class="val">
        <button type="button" class="nk-voc" :class="{ on: voc }" role="switch" :aria-checked="voc"
                @click="voc = !voc" :title="voc ? '사용자 VoC 컴포넌트를 붙여 만듭니다' : '켜면 사용자 VoC 로 분류'">
          <span class="nk-voc-k"></span>
          <span class="nk-voc-t">{{ voc ? '사용자 VoC 로 분류' : '일반 (VoC 아님)' }}</span>
        </button></span></div>

      <div><span class="k">담당자</span><span class="val val-user">
        <FieldEdit :ticket="d.parent" field="assignee" local :value="nc.assigneeId"
                   :user-id="nc.assigneeId" @pick="setWho">
          <Avatar v-if="nc.assigneeId" :user="nc.assigneeId" :name="nc.assigneeName" :size="20" />
          <span v-else class="nk-noav" aria-hidden="true"></span>
          <span :class="{ muted: !nc.assigneeId }">{{ nc.assigneeName || '미지정' }}</span>
        </FieldEdit></span></div>
    </div>

    <div class="nk-desc">
      <button type="button" class="nk-desc-t" @click="descOpen = !descOpen">
        <span class="nk-desc-cav">{{ descOpen ? '▾' : '▸' }}</span>설명 {{ descOpen ? '접기' : '추가 (선택)' }}
      </button>
      <div v-show="descOpen" class="nk-desc-body">
        <CommentEditor ref="ded" :ticket-key="createdKey || d.parent || '__new__'" kind="description"
                       :submit-fn="saveDesc" hide-footer />
        <div class="nk-desc-hint">이미지·파일은 여기에 붙여넣거나 끌어다 놓으면 티켓 생성 후 함께 첨부됩니다.</div>
      </div>
    </div>

    <div v-if="err" class="tkt-cmt-err">{{ err }}</div>
    <div class="nk-f">
      <span class="nk-hint">{{ canCreate ? '상태는 워크플로의 첫 상태로 시작합니다.'
                                          : '제목 · 우선순위 · 타입을 정해야 만들 수 있습니다.' }}</span>
      <button class="cmt-ed-btn ghost" @click="$emit('close')">취소</button>
      <button class="cmt-ed-btn primary" :disabled="!canCreate || busy" @click="submit">
        {{ busy ? '만드는 중…' : '만들기' }}</button>
    </div>
    </template>
  </div>
  </div>
  </Teleport>`,
};
