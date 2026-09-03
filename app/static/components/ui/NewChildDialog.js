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
import { recentItems } from "../../lib/recent.js";
import { cachedOptions, recentEpicOptions, rememberOptions } from "../../lib/optionRepository.js";
import { clearPendingMutation, loadPendingMutation, newMutationId,
         savePendingMutation } from "../../lib/mutationId.js";

// Task 상위 고르기에서 Epic 대신 고를 수 있는 특수 옵션(맨 위 고정). ('사용자 VoC' 는 상위가 아니라
// 아래 토글로 받는다 — Epic 에 속한 VoC 도 있어 상위 선택과 배타적이면 안 된다.)
const SPECIALS = [{ key: "__none__", special: "none", label: "Epic 없음", desc: "Epic 없이 Task 만들기" }];
const DEFAULT_TASK_TYPES = ["Task", "Story", "Bug", "Improvement", "New Feature"];
const DEFAULT_SUBTASK_TYPES = ["Sub-Task"];

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
      busy: false, err: "", priOpts: cachedOptions("priorities"), compOpts: cachedOptions("components"),
      descOpen: false, createdKey: "", createMutationId: "", pendingCreatePayload: null, voc: false,
      nc: { type: "", summary: "", priority: "", components: [],
            duedate: "", assigneeId: "", assigneeName: "" },
      // 해소된 상위 컨텍스트(고정이면 즉시, FAB 이면 고른 뒤 채워진다)
      d: { parent: "", isEpic: false, standalone: false, types: [], due: "", comps: [], plabel: "" },
      resolved: false,     // 상위가 정해졌나 → 입력 폼 노출
      fixed: false,        // 티켓 내부에서 열려 상위가 상수(변경 불가)
      // 상위 선택(FAB) 상태
      pq: "", plist: [], pbusy: false, parentErr: "", parentLookupSeq: 0, resolveSeq: 0,
      typeLoading: false, typeErr: "",
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
    api.options("components").then((r) => {
      const values = (r || []).map((x) => x.name);
      if (values.length) this.compOpts = rememberOptions("components", values);
    }).catch(() => {});
    api.options("priorities").then((r) => {
      const values = (r || []).map((x) => x.name);
      if (values.length) this.priOpts = rememberOptions("priorities", values);
    }).catch(() => {});
    // 상위 컨텍스트: FAB(pickKind, 상위 미지정) 이면 이 창에서 고르고, 그 외엔 props 로 즉시 해소.
    if (this.pickKind && !this.parent && !this.standalone) {
      this.searchParents("");
      this.$nextTick(() => { const el = this.$refs.psearch; if (el) el.focus(); });
    } else {
      this.fixed = !this.standalone;    // 상위 상수(변경 불가). standalone(Epic 없음)은 고정 아님
      this._resolve({ parent: this.parent, isEpic: this.isEpic, standalone: this.standalone,
                      types: this.types, due: this.parentDue, comps: this.parentComponents, plabel: "" });
    }
    this.restorePendingCreate();
    document.addEventListener("keydown", this._onEsc = (e) => {
      if (e.key === "Escape") { e.stopPropagation(); this.closeGuarded(); }
    }, true);
  },
  unmounted() { document.removeEventListener("keydown", this._onEsc, true); clearTimeout(this._t); },
  methods: {
    fromBackdrop,
    // 생성 중에는 닫지 않는다 — 받아 놓은 글이 사라진다(실패하면 바로 풀린다).
    closeGuarded() {
      if (this.busy || this.createMutationId) {
        pushToast("Jira 반영 여부를 확인 중입니다 — 입력을 보존한 채 다시 시도해 주세요.", "warn");
        return;
      }
      if (isBusy()) { pushToast(busyLabel() + " — 끝나면 닫을 수 있습니다.", "warn"); return; }
      this.$emit("close");
    },
    rankOf: priRankOf,
    epicColor(key) { return categoryColor(key); },
    setWho(id, u) {
      this.nc.assigneeId = id || "";
      this.nc.assigneeName = u ? (u.display || u.name || "") : "";
    },
    restorePendingCreate() {
      const saved = loadPendingMutation("issue-create");
      if (!saved) return;
      const p = saved.payload || {}, ctx = saved.context || {};
      this.createMutationId = saved.id;
      this.pendingCreatePayload = p;
      this.nc = {
        type: p.type || "", summary: p.summary || "", priority: p.priority || "",
        components: (p.components || []).slice(), duedate: p.duedate || "",
        assigneeId: p.assignee || "", assigneeName: p.assignee || "",
      };
      this._resolve({
        parent: ctx.parent || "", isEpic: !!ctx.isEpic, standalone: !!ctx.standalone,
        types: (ctx.isEpic || ctx.standalone) ? DEFAULT_TASK_TYPES : DEFAULT_SUBTASK_TYPES,
        due: p.duedate || "", comps: (p.components || []).slice(),
        plabel: ctx.parent || (ctx.standalone ? "Epic 없음" : ""),
      });
      this.err = "이전 생성 요청의 Jira 반영 여부를 확인할 수 있습니다. 같은 내용으로 다시 시도해 주세요.";
    },
    // ── 상위 선택(FAB) ──
    searchParents(q) {
      q = String(q || "");
      this.pq = q;
      this.parentErr = "";
      clearTimeout(this._t);
      const token = ++this.parentLookupSeq;
      const isSub = this.pickKind === "task";
      // 'Epic 없음'과 이 브라우저의 최근 티켓/Epic은 서버 검색과 무관하다. 먼저 그린다.
      const local = this._recentParents(q, isSub);
      this.plist = local;
      this.pbusy = true;
      this._t = setTimeout(() => {
        const p = isSub
          ? api.parentTaskCandidates(q).then((r) => (r && r.items) || [])
          : api.options("epics", q).then((r) => (r || []).map(
              (e) => ({ key: e.key, summary: e.summary || "", name: e.name || e.key, type: "Epic" })));
        p.then((items) => {
          if (token !== this.parentLookupSeq || this.pq !== q) return;
          this.plist = this._mergeParents(local, items || []);
        }).catch(() => {
          if (token === this.parentLookupSeq) {
            this.parentErr = "Jira 후보 목록을 불러오지 못했습니다. 없음·최근 항목은 계속 선택할 수 있습니다.";
          }
        })
          .finally(() => { if (token === this.parentLookupSeq) this.pbusy = false; });
      }, 250);
    },
    _mergeParents(...groups) {
      const out = [], seen = new Set();
      for (const group of groups) for (const item of (group || [])) {
        const id = item && (item.special ? "special:" + item.special : String(item.key || "").toUpperCase());
        if (!id || seen.has(id)) continue;
        seen.add(id); out.push(item);
      }
      return out;
    },
    _recentParents(q, isSub) {
      const needle = String(q || "").trim().toLocaleLowerCase();
      const matches = (item) => !needle || [item.key, item.summary, item.name, item.label, item.desc]
        .some((value) => String(value || "").toLocaleLowerCase().includes(needle));
      const out = [];
      if (!isSub) {
        out.push(...SPECIALS.filter(matches));
        out.push(...recentEpicOptions(60).filter(matches).map((item) => ({ ...item, type: "Epic" })));
        return this._mergeParents(out);
      }
      for (const item of recentItems(60, "jira")) {
        const type = String(item.type || item.issuetype || "");
        if (/^epic$/i.test(type) || /sub[ -]?task/i.test(type)) continue;
        const key = String(item.key || "").toUpperCase();
        if (!key) continue;
        const summary = String(item.summary || item.title || "").replace(
          new RegExp("^" + key.replace(/[.*+?^${}()|[\]\\]/g, "\\$&") + "\\s*"), "");
        const candidate = { key, summary, type: type || "Task", statusCategory: item.statusCategory || "",
                            epicKey: item.epicKey || "", epicName: item.epicName || "",
                            assignee: item.assignee || "", assigneeId: item.assigneeId || "" };
        if (matches(candidate)) out.push(candidate);
      }
      return this._mergeParents(out);
    },
    pickParent(item) {
      const token = ++this.resolveSeq;
      this.typeErr = "";
      if (item.special) {                               // 'Epic 없음' → 독립 Task
        const initial = cachedOptions("tasktypes");
        this._resolve({ parent: "", isEpic: false, standalone: true,
                        types: initial.length ? initial : DEFAULT_TASK_TYPES,
                        due: "", comps: [], plabel: "Epic 없음" });
        this._loadTypes(token, "tasktypes", api.taskTypes());
        return;
      }
      const parent = item.key;
      const typeKind = this.pickKind === "epic" ? "childtypes.epic" : "childtypes.task";
      const cached = cachedOptions(typeKind);
      const fallback = this.pickKind === "epic" ? DEFAULT_TASK_TYPES : DEFAULT_SUBTASK_TYPES;
      this._resolve({ parent, isEpic: this.pickKind === "epic", standalone: false,
                      types: cached.length ? cached : fallback,
                      due: item.due || "", comps: (item.components || []).slice(),
                      plabel: item.name || item.summary || "" });
      this._loadTypes(token, typeKind, api.childTypes(parent));
      api.ticket(parent).then((v) => {
        if (token !== this.resolveSeq || !v || this.d.parent !== parent) return;
        this.d.due = v.due || ""; this.d.comps = (v.components || []).slice();
        if (!this.nc.duedate) this.nc.duedate = this.d.due;
        if (!this.nc.components.length) this.nc.components = this.d.comps.filter((c) => c !== "사용자 VoC");
      }).catch(() => { /* 상위 부가정보가 없어도 타입·제목 입력은 계속 가능 */ });
    },
    _loadTypes(token, kind, request) {
      this.typeLoading = true;
      Promise.resolve(request).then((values) => {
        if (token !== this.resolveSeq) return;
        const list = Array.from(new Set((values || []).filter(Boolean)));
        if (!list.length) {
          this.typeErr = "Jira에서 생성 가능한 타입을 받지 못했습니다. 기존 목록으로 계속할 수 있습니다.";
          return;
        }
        rememberOptions(kind, list);
        this.d.types = list;
        if (!list.includes(this.nc.type)) this.nc.type = list.length === 1 ? list[0] : "";
      }).catch(() => {
        if (token === this.resolveSeq) this.typeErr = "타입 조회가 지연·실패해 기존 목록을 사용합니다.";
      }).finally(() => { if (token === this.resolveSeq) this.typeLoading = false; });
    },
    reopenPick() {                                      // 상위 다시 고르기(고정이 아닐 때만)
      if (this.fixed) return;
      this.resolved = false;
      this.searchParents(this.pq || "");
      this.$nextTick(() => { const el = this.$refs.psearch; if (el) el.focus(); });
    },
    _resolve(ctx) {
      if (!(ctx.types || []).length) {
        const kind = (ctx.isEpic || ctx.standalone) ? "tasktypes" : "childtypes.task";
        const cached = cachedOptions(kind);
        ctx.types = cached.length ? cached
          : ((ctx.isEpic || ctx.standalone) ? DEFAULT_TASK_TYPES : DEFAULT_SUBTASK_TYPES);
      }
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
        let payload = {
          type: this.nc.type, summary: this.nc.summary.trim(), priority: this.nc.priority,
          duedate: this.nc.duedate || null, assignee: this.nc.assigneeId || null,
          components: comps, descriptionHtml: createDesc,
          parentIsEpic: this.d.parent ? !!this.d.isEpic : null,
        };
        if (this.pendingCreatePayload && this.createMutationId) {
          payload = { ...this.pendingCreatePayload };
        }
        // standalone 은 부모가 없어 /api/task 로, 그 외엔 부모 밑으로(/api/ticket/{parent}/child).
        // 생성 뒤 설명 저장이 실패해도 재시도할 때 티켓을 중복 생성하지 않는다.
        let key = this.createdKey;
        if (!key) {
          if (!this.createMutationId) this.createMutationId = newMutationId("issue");
          payload.clientMutationId = this.createMutationId;
          this.pendingCreatePayload = { ...payload };
          savePendingMutation("issue-create", this.createMutationId, payload, {
            parent: this.d.parent, isEpic: this.d.isEpic, standalone: this.d.standalone,
          });
          const r = this.d.standalone ? await api.createTask(payload)
                                      : await api.createChild(this.d.parent, payload);
          if (!r || r.ok === false) {
            this.createMutationId = "";
            this.pendingCreatePayload = null;
            clearPendingMutation("issue-create");
            this.err = (r && r.error) || "만들지 못했습니다."; return;
          }
          key = r.key;
          this.createdKey = key;
          this.createMutationId = "";       // later description retries use the confirmed key
          this.pendingCreatePayload = null;
          clearPendingMutation("issue-create");
        }
        if (wantDesc && key) {
          await this.$nextTick();
          await this.$refs.ded.submit();
          if (this.$refs.ded.err) throw new Error(this.$refs.ded.err);
        }
        this.$emit("created", key);
      } catch (e) {
        // 인증이 끊긴 경우에도 Jira가 쓰기를 받았는지 확정할 수 없는 transport 경로가 섞일 수
        // 있다. 로그인 복구 뒤 같은 논리 요청 id로 재조사해야 중복 Task가 생기지 않는다.
        if (!(e && (e.uncertain || e.needLogin))) {
          this.createMutationId = "";
          this.pendingCreatePayload = null;
          clearPendingMutation("issue-create");
        }
        this.err = (e && e.message) || "만들지 못했습니다.";
      } finally { this.busy = false; }
    },
  },
  template: `
  <Teleport to="body">
  <div class="nk-ov" @click.self="fromBackdrop($event) && closeGuarded()">
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
      <div v-if="parentErr" class="nk-cand-error">{{ parentErr }}</div>
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
      <div v-if="typeLoading || typeErr" class="nk-type-note" :class="{ warn: typeErr }">
        {{ typeErr || 'Jira 타입 목록 확인 중…' }}
      </div>

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
      <button class="cmt-ed-btn ghost" @click="closeGuarded()">취소</button>
      <button class="cmt-ed-btn primary" :disabled="!canCreate || busy" @click="submit">
        {{ busy ? '만드는 중…' : '만들기' }}</button>
    </div>
    </template>
  </div>
  </div>
  </Teleport>`,
};
