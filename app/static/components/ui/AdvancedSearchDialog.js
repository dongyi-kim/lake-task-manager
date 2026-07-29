// AdvancedSearchDialog.js — 고급(복합) 검색. 여러 필드 조건을 UI 로 조합해 **JQL 을 구성**하고,
// 그 JQL 을 부모(Task 화면)에 넘겨 검색 결과를 띄운다. 저장 형식은 표준 JQL 이라 사용자가 우측
// 입력창에서 직접 손보거나, Jira 웹에서 그대로 쓸 수도 있다(우리만의 문법을 만들지 않는다).
//
// 대상 필드: Project · Assignee · Reporter · TEXT · Status · Epic · Labels · Component ·
//            Created · Updated · Resolved.  값이 채워진 조건만 AND 로 엮는다(빈 조건은 무시).
import { api } from "../../lib/api.js";
import FieldEdit from "./FieldEdit.js";

// JQL 문자열 리터럴 — 따옴표/역슬래시 제거 후 감싼다(값이 질의문을 깨지 않게).
function q(v) { return '"' + String(v == null ? "" : v).replace(/["\\]/g, "") + '"'; }
function inList(field, arr) { return field + " in (" + arr.map(q).join(", ") + ")"; }
function csv(s) { return String(s || "").split(",").map((x) => x.trim()).filter(Boolean); }

// 완료 판정은 statusCategory 로(상태명 하드코딩 금지 — 사내 커스텀 워크플로가 많다).
const STATUS_CATS = [
  { k: "To Do", label: "할당됨" },
  { k: "In Progress", label: "진행 중" },
  { k: "Done", label: "완료" },
];
const DATE_FIELDS = [
  { k: "created", label: "생성(Created)" },
  { k: "updated", label: "수정(Updated)" },
  { k: "resolved", label: "해결(Resolved)" },
];

export default {
  name: "AdvancedSearchDialog",
  components: { FieldEdit },
  props: {
    projects: { type: Array, default: () => [] },   // jira.yml search 등록 프로젝트(기본 대상)
    myId: { type: String, default: "" },
    initial: { type: String, default: "" },         // 현재 JQL(참고용 표시)
  },
  emits: ["apply", "close"],
  data() {
    return {
      f: {
        project: (this.projects || []).slice(),      // 기본 = 프로젝트 키(들)
        assignee: "any", assigneeSel: null,          // any | me | empty | user
        reporter: "any", reporterSel: null,
        text: "",
        status: {},                                  // { "To Do": true, … }
        epic: null,                                  // {key, name}
        labels: "",
        component: "",
        created: { mode: "", from: "", to: "", days: "7" },   // mode: '' | recent | range
        updated: { mode: "", from: "", to: "", days: "7" },
        resolved: { mode: "", from: "", to: "", days: "7" },
      },
    };
  },
  mounted() {
    this._esc = (e) => { if (e.key === "Escape") { e.stopPropagation(); this.$emit("close"); } };
    window.addEventListener("keydown", this._esc);
  },
  unmounted() { window.removeEventListener("keydown", this._esc); },
  computed: {
    /** 채워진 조건만 AND 로 엮은 JQL. 정렬은 부모(서버)가 붙인다. */
    jql() {
      const f = this.f, p = [];
      if (f.project.length) p.push(inList("project", f.project));
      if (f.assignee === "me") p.push("assignee = currentUser()");
      else if (f.assignee === "empty") p.push("assignee is EMPTY");
      else if (f.assignee === "user" && f.assigneeSel) p.push("assignee = " + q(f.assigneeSel.id));
      if (f.reporter === "me") p.push("reporter = currentUser()");
      else if (f.reporter === "empty") p.push("reporter is EMPTY");
      else if (f.reporter === "user" && f.reporterSel) p.push("reporter = " + q(f.reporterSel.id));
      if (f.text.trim()) p.push("text ~ " + q(f.text.trim()));
      const st = STATUS_CATS.filter((s) => f.status[s.k]).map((s) => s.k);
      if (st.length) p.push(inList("statusCategory", st));
      if (f.epic) p.push('"Epic Link" = ' + f.epic.key);
      if (csv(f.labels).length) p.push(inList("labels", csv(f.labels)));
      if (csv(f.component).length) p.push(inList("component", csv(f.component)));
      for (const d of DATE_FIELDS) {
        const v = f[d.k];
        if (v.mode === "recent" && String(v.days).trim()) {
          const n = parseInt(v.days, 10);
          if (n > 0) p.push(d.k + " >= -" + n + "d");
        } else if (v.mode === "range") {
          if (v.from) p.push(d.k + " >= " + q(v.from));
          if (v.to) p.push(d.k + " <= " + q(v.to));
        }
      }
      return p.join(" AND ");
    },
    statusCats() { return STATUS_CATS; },
    dateFields() { return DATE_FIELDS; },
  },
  methods: {
    fromBackdrop(e) { return e.target === e.currentTarget; },
    toggleProject(k) {
      const i = this.f.project.indexOf(k);
      if (i >= 0) this.f.project.splice(i, 1); else this.f.project.push(k);
    },
    toggleStatus(k) { this.f.status = Object.assign({}, this.f.status, { [k]: !this.f.status[k] }); },
    onPickAssignee(id, u) {
      if (!id) { this.f.assignee = "any"; this.f.assigneeSel = null; return; }
      this.f.assignee = "user"; this.f.assigneeSel = { id, name: (u && (u.display || u.name)) || id };
    },
    onPickReporter(id, u) {
      if (!id) { this.f.reporter = "any"; this.f.reporterSel = null; return; }
      this.f.reporter = "user"; this.f.reporterSel = { id, name: (u && (u.display || u.name)) || id };
    },
    onPickEpic(key, e) { this.f.epic = key ? { key, name: (e && (e.name || e.summary)) || key } : null; },
    reset() {
      this.f.project = (this.projects || []).slice();
      this.f.assignee = "any"; this.f.assigneeSel = null;
      this.f.reporter = "any"; this.f.reporterSel = null;
      this.f.text = ""; this.f.status = {}; this.f.epic = null;
      this.f.labels = ""; this.f.component = "";
      for (const d of DATE_FIELDS) this.f[d.k] = { mode: "", from: "", to: "", days: "7" };
    },
    apply() { this.$emit("apply", this.jql); },
  },
  template: `
  <div class="adv-ov" @mousedown.self="$emit('close')">
    <div class="adv-box" role="dialog" aria-modal="true">
      <div class="adv-head">
        <b>고급 검색</b>
        <span class="adv-sub">조건을 조합하면 JQL 이 만들어집니다</span>
        <button class="adv-x" @click="$emit('close')" aria-label="닫기">✕</button>
      </div>

      <div class="adv-body">
        <!-- Project -->
        <div class="adv-row">
          <span class="adv-l">Project</span>
          <div class="adv-v adv-chips">
            <button v-for="pk in projects" :key="pk" type="button" class="adv-chip"
                    :class="{ on: f.project.includes(pk) }" @click="toggleProject(pk)">{{ pk }}</button>
            <span v-if="!projects.length" class="adv-none">등록된 프로젝트 없음</span>
          </div>
        </div>
        <!-- Assignee -->
        <div class="adv-row">
          <span class="adv-l">Assignee</span>
          <div class="adv-v adv-seg3">
            <button type="button" :class="{ on: f.assignee==='any' }" @click="f.assignee='any'; f.assigneeSel=null">무관</button>
            <button type="button" :class="{ on: f.assignee==='me' }" @click="f.assignee='me'; f.assigneeSel=null">나</button>
            <button type="button" :class="{ on: f.assignee==='empty' }" @click="f.assignee='empty'; f.assigneeSel=null">미지정</button>
            <FieldEdit class="adv-fe" :class="{ on: f.assignee==='user' }" ticket="__adv__" field="assignee" local
                       :value="f.assigneeSel ? f.assigneeSel.id : ''" :user-id="f.assigneeSel ? f.assigneeSel.id : ''"
                       @pick="onPickAssignee">{{ f.assigneeSel ? f.assigneeSel.name : '사람 지정…' }}</FieldEdit>
          </div>
        </div>
        <!-- Reporter -->
        <div class="adv-row">
          <span class="adv-l">Reporter</span>
          <div class="adv-v adv-seg3">
            <button type="button" :class="{ on: f.reporter==='any' }" @click="f.reporter='any'; f.reporterSel=null">무관</button>
            <button type="button" :class="{ on: f.reporter==='me' }" @click="f.reporter='me'; f.reporterSel=null">나</button>
            <button type="button" :class="{ on: f.reporter==='empty' }" @click="f.reporter='empty'; f.reporterSel=null">미지정</button>
            <FieldEdit class="adv-fe" :class="{ on: f.reporter==='user' }" ticket="__adv__" field="reporter" local
                       :value="f.reporterSel ? f.reporterSel.id : ''" :user-id="f.reporterSel ? f.reporterSel.id : ''"
                       @pick="onPickReporter">{{ f.reporterSel ? f.reporterSel.name : '사람 지정…' }}</FieldEdit>
          </div>
        </div>
        <!-- TEXT -->
        <div class="adv-row">
          <span class="adv-l">TEXT</span>
          <div class="adv-v">
            <input class="adv-in" v-model="f.text" placeholder="요약·설명·댓글 본문 검색 (text ~)" spellcheck="false" />
          </div>
        </div>
        <!-- Status -->
        <div class="adv-row">
          <span class="adv-l">Status</span>
          <div class="adv-v adv-chips">
            <button v-for="s in statusCats" :key="s.k" type="button" class="adv-chip"
                    :class="{ on: f.status[s.k] }" @click="toggleStatus(s.k)">{{ s.label }}</button>
          </div>
        </div>
        <!-- Epic -->
        <div class="adv-row">
          <span class="adv-l">Epic</span>
          <div class="adv-v adv-seg3">
            <FieldEdit class="adv-fe" :class="{ on: !!f.epic }" ticket="__adv__" field="epic" local
                       :value="f.epic ? f.epic.key : ''" @pick="onPickEpic">{{ f.epic ? f.epic.name : 'Epic 지정…' }}</FieldEdit>
            <button v-if="f.epic" type="button" class="adv-clear" @click="f.epic=null">해제</button>
          </div>
        </div>
        <!-- Labels -->
        <div class="adv-row">
          <span class="adv-l">Labels</span>
          <div class="adv-v"><input class="adv-in" v-model="f.labels" placeholder="쉼표로 구분 (labels in …)" spellcheck="false" /></div>
        </div>
        <!-- Component -->
        <div class="adv-row">
          <span class="adv-l">Component</span>
          <div class="adv-v"><input class="adv-in" v-model="f.component" placeholder="쉼표로 구분 (component in …)" spellcheck="false" /></div>
        </div>
        <!-- Created / Updated / Resolved -->
        <div v-for="d in dateFields" :key="d.k" class="adv-row">
          <span class="adv-l">{{ d.label }}</span>
          <div class="adv-v adv-date">
            <div class="adv-seg3 adv-datemode">
              <button type="button" :class="{ on: f[d.k].mode==='' }" @click="f[d.k].mode=''">무관</button>
              <button type="button" :class="{ on: f[d.k].mode==='recent' }" @click="f[d.k].mode='recent'">최근</button>
              <button type="button" :class="{ on: f[d.k].mode==='range' }" @click="f[d.k].mode='range'">기간</button>
            </div>
            <template v-if="f[d.k].mode==='recent'">
              <input class="adv-in adv-days" v-model="f[d.k].days" inputmode="numeric" /> <span class="adv-unit">일 이내</span>
            </template>
            <template v-else-if="f[d.k].mode==='range'">
              <input class="adv-in adv-dt" type="date" v-model="f[d.k].from" /> <span class="adv-unit">~</span>
              <input class="adv-in adv-dt" type="date" v-model="f[d.k].to" />
            </template>
          </div>
        </div>
      </div>

      <div class="adv-foot">
        <code class="adv-jql">{{ jql || '(조건을 선택하세요)' }}</code>
        <div class="adv-btns">
          <button type="button" class="adv-btn ghost" @click="reset">초기화</button>
          <button type="button" class="adv-btn ghost" @click="$emit('close')">취소</button>
          <button type="button" class="adv-btn primary" :disabled="!jql" @click="apply">검색</button>
        </div>
      </div>
    </div>
  </div>`,
};
