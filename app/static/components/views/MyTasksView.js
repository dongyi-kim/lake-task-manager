// MyTasksView.js — '내 Task'. 백엔드가 주는 **하나의 모델**(내 원자 + 부모/동료/Epic 맥락)을
// 세 가지로 다르게 뿌린다. 뷰별 데이터 요청은 없다 — 배열이 뭐냐가 아니라 어떻게 배치하느냐의 문제다.
//
//   시간 우선   계층을 부수고 내 실행 원자만 마감 버킷으로. 아침에 여는 기본 뷰.
//   부모 클러스터  원자를 부모 Task 로 묶되 **묶음 순서는 그 안에서 가장 급한 것**이 정한다.
//                컨텍스트 스위칭 비용과 긴급도를 동시에 존중.
//   계층 우선   Epic → Task → 내 하위. "이 프로젝트에서 내 일이 뭐지"에 최적.
//
// 공통 규칙(세 뷰 모두):
//  · 동료 하위는 처음부터 펼치지 않는다 — '동료 N개 · M 완료' 집계 칩만. 눌러야 흐린 read-only 로 펼침.
//  · 남의 부모 Task 는 흐린 헤더 + 롤업 바 + 담당자 이름으로 '내 게 아님'을 명시.
//  · Epic 없음은 숨기지 않고 **일급 상태**로 — 계층 뷰의 마지막 섹션, 다른 뷰의 점선 태그.
//  · 정렬은 1차 키 하나만 고르고 나머지는 2차 키. 둘을 동시에 1차로 만들면 목록이 못 읽힌다.
import { api } from "../../lib/api.js";

const NO_DUE = 1e6;

// 마감 D-day 표기 — 지남은 D+n, 오늘은 '오늘'
function dueLabel(d) {
  if (d === null || d === undefined) return "";
  return d < 0 ? "D+" + -d : d === 0 ? "오늘" : "D-" + d;
}
function dueBand(d) {
  if (d === null || d === undefined) return "none";
  return d < 0 ? "over" : d === 0 ? "today" : d <= 7 ? "soon" : "later";
}

const SORTS = [
  { k: "due", label: "마감 우선", hint: "마감 → 우선순위 → 키" },
  { k: "pri", label: "우선순위 우선", hint: "우선순위 → 마감 → 키" },
  { k: "status", label: "상태순", hint: "할 일 → 진행 → 완료, 그다음 마감" },
];
const CAT_ORDER = { todo: 0, inprogress: 1, done: 2 };

export default {
  name: "MyTasksView",
  data() {
    return {
      model: null, loading: true, err: "",
      view: "time",                       // time | cluster | hier
      sort: "due",
      // 보기 방식 — 기본값은 "내 것만, 완료는 접고, 동료는 집계만"
      showDone: false,
      showOthers: false,                  // 동료 하위를 처음부터 펼칠지
      showNoDue: true,
      openOthers: {},                     // 그룹별 동료 하위 펼침
      openEpic: {},
    };
  },
  mounted() { this.load(); },
  computed: {
    groups() { return (this.model && this.model.groups) || []; },
    epicMap() {
      const m = {};
      for (const e of (this.model && this.model.epics) || []) m[e.key] = e;
      return m;
    },
    // 내 실행 원자(평면) — 부모/Epic 맥락을 붙여 둔다. 시간 우선 뷰와 카운트가 이걸 쓴다.
    atoms() {
      const out = [];
      for (const g of this.groups) {
        for (const a of g.atoms) {
          out.push(Object.assign({}, a, {
            group: g,
            epicKey: a.epic || g.epic || null,
            // 부모 헤더가 필요한 원자인가(= 하위가 있는 Task 아래의 내 Sub)
            hasParent: !g.standalone && a.key !== g.key,
          }));
        }
      }
      return out;
    },
    visibleAtoms() {
      return this.atoms.filter((a) => this.keep(a)).sort(this.cmp);
    },
    // 부모 클러스터 — 묶음 순서는 '그 안에서 가장 급한 원자'가 정한다.
    visibleGroups() {
      const gs = [];
      for (const g of this.groups) {
        const atoms = g.atoms.filter((a) => this.keep(a)).sort(this.cmp);
        if (!atoms.length) continue;
        gs.push(Object.assign({}, g, { visAtoms: atoms }));
      }
      gs.sort((x, y) => this.cmpGroup(x, y));
      return gs;
    },
    // 계층 — Epic → 그룹. Epic 없음은 마지막 섹션으로(숨기지 않는다).
    epicSections() {
      const by = new Map();
      for (const g of this.visibleGroups) {
        const k = g.epic || "__none__";
        if (!by.has(k)) by.set(k, []);
        by.get(k).push(g);
      }
      const keys = [...by.keys()].filter((k) => k !== "__none__");
      keys.sort((a, b) => {
        const ua = Math.min(...by.get(a).map((g) => this.gKey(g)));
        const ub = Math.min(...by.get(b).map((g) => this.gKey(g)));
        return ua - ub;
      });
      if (by.has("__none__")) keys.push("__none__");
      return keys.map((k) => ({
        key: k,
        title: k === "__none__" ? "Epic 없음" : ((this.epicMap[k] || {}).title || k),
        groups: by.get(k),
      }));
    },
    counts() {
      const c = { over: 0, today: 0, week: 0, later: 0, none: 0, done: 0 };
      for (const a of this.atoms) {
        if (a.statusCategory === "done") { c.done++; continue; }
        const b = dueBand(a.dueDays);
        if (b === "over") c.over++;
        else if (b === "today") c.today++;
        else if (b === "soon") c.week++;
        else if (b === "later") c.later++;
        else c.none++;
      }
      return c;
    },
    // 시간 우선 — 섹션은 **1차 정렬 키가 정한다**. 마감으로 정렬하면 마감 버킷,
    // 우선순위로 정렬하면 우선순위 버킷. (버킷을 늘 마감으로 두면 다른 정렬을 골라도
    // 마감이 1차 키로 남아 컨트롤이 거짓말을 한다.)
    buckets() {
      const rows = this.visibleAtoms;
      let defs;
      if (this.sort === "pri") {
        defs = [
          { k: "over", label: "높음", test: (a) => a.priBand === "high" },
          { k: "soon", label: "보통", test: (a) => a.priBand === "mid" },
          { k: "later", label: "낮음", test: (a) => a.priBand === "low" },
        ];
      } else if (this.sort === "status") {
        defs = [
          { k: "today", label: "할 일", test: (a) => a.statusCategory === "todo" },
          { k: "soon", label: "진행 중", test: (a) => a.statusCategory === "inprogress" },
          { k: "later", label: "완료", test: (a) => a.statusCategory === "done" },
        ];
      } else {
        defs = [
          { k: "over", label: "지남", test: (a) => a.dueDays !== null && a.dueDays < 0 },
          { k: "today", label: "오늘", test: (a) => a.dueDays === 0 },
          { k: "soon", label: "이번 주", test: (a) => a.dueDays > 0 && a.dueDays <= 7 },
          { k: "later", label: "다음", test: (a) => a.dueDays > 7 },
          { k: "none", label: "마감 없음", test: (a) => a.dueDays === null || a.dueDays === undefined },
        ];
      }
      return defs.map((b) => Object.assign({}, b, { rows: rows.filter(b.test) }))
                 .filter((b) => b.rows.length);
    },
    sortHint() { return (SORTS.find((s) => s.k === this.sort) || {}).hint || ""; },
    sorts() { return SORTS; },
  },
  methods: {
    async load() {
      this.loading = true; this.err = "";
      try { this.model = await api.myTasks(this.showDone); }
      catch (e) { this.err = (e && e.message) || "불러오기 실패"; }
      finally { this.loading = false; }
    },
    toggleDone() {
      this.showDone = !this.showDone;
      this.load();                  // 완료 포함 여부는 서버 질의 조건이라 다시 받는다
    },
    keep(a) {
      if (!this.showDone && a.statusCategory === "done") return false;
      if (!this.showNoDue && (a.dueDays === null || a.dueDays === undefined)) return false;
      return true;
    },
    dueOf(a) { return (a.dueDays === null || a.dueDays === undefined) ? NO_DUE : a.dueDays; },
    cmp(a, b) {
      if (this.sort === "pri") {
        return a.priRank - b.priRank || this.dueOf(a) - this.dueOf(b) || (a.key < b.key ? -1 : 1);
      }
      if (this.sort === "status") {
        return (CAT_ORDER[a.statusCategory] - CAT_ORDER[b.statusCategory])
          || this.dueOf(a) - this.dueOf(b) || a.priRank - b.priRank;
      }
      return this.dueOf(a) - this.dueOf(b) || a.priRank - b.priRank || (a.key < b.key ? -1 : 1);
    },
    // 그룹 정렬 키 — 묶음은 그 안 원자의 대표값으로 줄 세운다(가장 급한/가장 높은/가장 덜 끝난).
    gKey(g) {
      const at = g.visAtoms || g.atoms;
      if (this.sort === "pri") return Math.min(...at.map((a) => a.priRank));
      if (this.sort === "status") return Math.min(...at.map((a) => CAT_ORDER[a.statusCategory]));
      return Math.min(...at.map((a) => this.dueOf(a)));
    },
    cmpGroup(x, y) {
      const d = this.gKey(x) - this.gKey(y);
      if (d) return d;
      const dx = Math.min(...(x.visAtoms || x.atoms).map((a) => this.dueOf(a)));
      const dy = Math.min(...(y.visAtoms || y.atoms).map((a) => this.dueOf(a)));
      return dx - dy || (x.key < y.key ? -1 : 1);
    },
    othersOpen(g) { return this.showOthers || !!this.openOthers[g.key]; },
    toggleOthers(g) { this.openOthers = Object.assign({}, this.openOthers, { [g.key]: !this.othersOpen(g) }); },
    epicOpen(k) { return this.openEpic[k] !== false; },
    toggleEpic(k) { this.openEpic = Object.assign({}, this.openEpic, { [k]: !this.epicOpen(k) }); },
    epicTitle(k) { return k ? ((this.epicMap[k] || {}).title || k) : null; },
    dueLabel, dueBand,
    urgent(g) { return this.sort === "due" && this.gKey(g) < 0; },   // 🔥 = 마감이 지난 것만
  },
  template: `
  <div class="mytasks">
    <div class="mt-head">
      <div>
        <h2 class="mt-h">내 Task<span v-if="model && model.user" class="mt-who">{{ model.user.name || model.user.id }}</span></h2>
        <div class="mt-sub">담당한 일감과 그 부모·동료·Epic 맥락을 한 화면에서. 같은 데이터를 세 가지로 봅니다.</div>
      </div>
      <div v-if="model" class="mt-counts">
        <span class="mt-c over" :class="{ zero: !counts.over }">지남 <b>{{ counts.over }}</b></span>
        <span class="mt-c today" :class="{ zero: !counts.today }">오늘 <b>{{ counts.today }}</b></span>
        <span class="mt-c soon" :class="{ zero: !counts.week }">이번 주 <b>{{ counts.week }}</b></span>
        <span class="mt-c">이후 <b>{{ counts.later }}</b></span>
        <span class="mt-c">마감없음 <b>{{ counts.none }}</b></span>
      </div>
    </div>

    <!-- 컨트롤 — 뷰/정렬/보기방식. 세 뷰가 **같은 컨트롤**을 공유한다(뷰마다 다르면 못 외운다) -->
    <div class="mt-bar">
      <div class="mt-seg">
        <button :class="{ on: view === 'time' }" @click="view = 'time'" title="마감 버킷으로 평평하게 — 아침에 여는 뷰">시간 우선</button>
        <button :class="{ on: view === 'cluster' }" @click="view = 'cluster'" title="부모 Task 로 묶기 — 가장 급한 원자가 묶음 순서를 정한다">부모 클러스터</button>
        <button :class="{ on: view === 'hier' }" @click="view = 'hier'" title="Epic → Task → 내 하위">계층 우선</button>
      </div>
      <div class="mt-seg sm">
        <button v-for="s in sorts" :key="s.k" :class="{ on: sort === s.k }" @click="sort = s.k" :title="s.hint">{{ s.label }}</button>
      </div>
      <div class="mt-toggles">
        <label class="mt-tg" title="완료된 내 일감까지 함께 본다(서버에서 다시 받아옵니다)">
          <input type="checkbox" :checked="showDone" @change="toggleDone"> 완료 포함
        </label>
        <label class="mt-tg" title="동료 하위를 처음부터 펼친다(기본은 집계 칩만)">
          <input type="checkbox" v-model="showOthers"> 동료 하위 펼치기
        </label>
        <label class="mt-tg" title="마감일이 없는 일감을 목록에 포함">
          <input type="checkbox" v-model="showNoDue"> 마감 없는 것
        </label>
        <button class="mt-refresh" @click="load" title="다시 불러오기">↻</button>
      </div>
    </div>
    <div class="mt-hint">정렬: {{ sortHint }}</div>

    <div v-if="loading" class="loading">불러오는 중…</div>
    <div v-else-if="err" class="mt-err">{{ err }}</div>
    <div v-else-if="!visibleAtoms.length" class="mt-empty">
      조건에 맞는 일감이 없습니다.<span v-if="!showDone"> (완료 포함을 켜면 끝낸 일도 볼 수 있습니다)</span>
    </div>

    <!-- ── 시간 우선 ── -->
    <div v-else-if="view === 'time'" class="mt-list">
      <div v-for="b in buckets" :key="b.k" class="mt-bucket">
        <div class="mt-bh" :class="b.k"><span class="mt-bdot"></span>{{ b.label }}<b>{{ b.rows.length }}</b></div>
        <div v-for="a in b.rows" :key="a.key" class="mt-row tkt" :data-key="a.key" :class="{ done: a.statusCategory === 'done' }">
          <span class="mt-pri" :class="a.priBand" :title="'우선순위: ' + a.pri"></span>
          <span class="mt-dot" :class="'st-' + a.statusCategory"></span>
          <span class="mt-key">{{ a.key }}</span>
          <span class="mt-title">{{ a.title }}</span>
          <span class="mt-ctx">
            <span v-if="a.epicKey" class="mt-epic" :title="'Epic: ' + epicTitle(a.epicKey)">◆ {{ epicTitle(a.epicKey) }}</span>
            <span v-else class="mt-epic none">Epic 없음</span>
            <!-- 부모 캡슐 — 내가 Sub 담당일 때 상위 Task 진척을 행 안에서 바로 -->
            <span v-if="a.hasParent" class="mt-par" :class="{ notmine: !a.group.mine }"
                  :title="a.group.key + ' · ' + a.group.title + (a.group.mine ? '' : ' · ' + (a.group.assignee || '') + ' 담당')">
              {{ a.group.key }}
              <span class="mt-bar2"><i :style="{ width: (a.group.pct || 0) + '%' }"></i></span>
              <em v-if="a.group.pct !== null">{{ a.group.pct }}%</em>
            </span>
            <span class="mt-due" :class="dueBand(a.dueDays)">{{ dueLabel(a.dueDays) || '—' }}</span>
          </span>
        </div>
      </div>
    </div>

    <!-- ── 부모 클러스터 ── -->
    <div v-else-if="view === 'cluster'" class="mt-list">
      <div v-for="g in visibleGroups" :key="g.key" class="mt-cl"
           :class="{ urgent: urgent(g), notmine: !g.mine, solo: g.standalone }">
        <!-- 하위가 없는 내 Task 는 부모 헤더가 곧 그 일감이다 — 헤더+행으로 두 번 그리지 않는다 -->
        <div v-if="g.standalone" class="mt-row solo tkt" :data-key="g.key"
             :class="{ done: g.visAtoms[0].statusCategory === 'done' }">
          <span v-if="urgent(g)" class="mt-fire" title="마감이 지났습니다">🔥</span>
          <span class="mt-pri" :class="g.visAtoms[0].priBand" :title="'우선순위: ' + g.visAtoms[0].pri"></span>
          <span class="mt-dot" :class="'st-' + g.statusCategory"></span>
          <span class="mt-key">{{ g.key }}</span>
          <span class="mt-title">{{ g.title }}</span>
          <span v-if="g.epic" class="mt-epic" :title="'Epic: ' + epicTitle(g.epic)">◆ {{ epicTitle(g.epic) }}</span>
          <span v-else class="mt-epic none">Epic 없음</span>
          <span class="mt-st">{{ g.status }}</span>
          <span class="mt-due" :class="dueBand(g.visAtoms[0].dueDays)">{{ dueLabel(g.visAtoms[0].dueDays) || '—' }}</span>
        </div>
        <template v-else>
        <div class="mt-clh" :class="{ tkt: true }" :data-key="g.key">
          <span v-if="urgent(g)" class="mt-fire" title="마감 지난 일감이 있습니다">🔥</span>
          <span v-if="g.epic" class="mt-epic" :title="'Epic: ' + epicTitle(g.epic)">◆ {{ epicTitle(g.epic) }}</span>
          <span v-else class="mt-epic none">Epic 없음</span>
          <span class="mt-key">{{ g.key }}</span>
          <span class="mt-title">{{ g.title }}</span>
          <span v-if="!g.mine" class="mt-owner">{{ g.assignee || '미할당' }} 담당</span>
          <span v-if="g.pct !== null" class="mt-roll">
            <span class="mt-bar2 w"><i :style="{ width: g.pct + '%' }"></i></span><em>{{ g.pct }}%</em>
          </span>
        </div>
        <div class="mt-clb">
          <div v-for="a in g.visAtoms" :key="a.key" class="mt-row sub tkt" :data-key="a.key"
               :class="{ done: a.statusCategory === 'done' }">
            <span class="mt-pri" :class="a.priBand" :title="'우선순위: ' + a.pri"></span>
            <span class="mt-dot" :class="'st-' + a.statusCategory"></span>
            <span class="mt-key">{{ a.key }}</span>
            <span class="mt-title">{{ a.title }}</span>
            <span class="mt-st">{{ a.status }}</span>
            <span class="mt-due" :class="dueBand(a.dueDays)">{{ dueLabel(a.dueDays) || '—' }}</span>
          </div>
          <!-- 동료 하위 — 존재와 진척만 남기고 접어 둔다 -->
          <button v-if="g.others.length" class="mt-others" @click="toggleOthers(g)">
            <span class="chev" :class="{ open: othersOpen(g) }">▸</span>
            동료 {{ g.others.length }}개 · {{ g.othersDone }} 완료
            <span class="mt-bar2"><i :style="{ width: (g.others.length ? Math.round(g.othersDone / g.others.length * 100) : 0) + '%' }"></i></span>
          </button>
          <div v-if="othersOpen(g)" class="mt-otherlist">
            <div v-for="o in g.others" :key="o.key" class="mt-row ro tkt" :data-key="o.key">
              <span class="mt-dot" :class="'st-' + o.statusCategory"></span>
              <span class="mt-key">{{ o.key }}</span>
              <span class="mt-title">{{ o.title }}</span>
              <span class="mt-owner">{{ o.assignee || '미할당' }}</span>
            </div>
          </div>
        </div>
        </template>
      </div>
    </div>

    <!-- ── 계층 우선 ── -->
    <div v-else class="mt-list">
      <div v-for="s in epicSections" :key="s.key" class="mt-ep">
        <div class="mt-eph" @click="toggleEpic(s.key)">
          <span class="chev" :class="{ open: epicOpen(s.key) }">▸</span>
          <span v-if="s.key !== '__none__'" class="mt-epdia">◆</span>
          <span class="mt-eptitle" :class="{ none: s.key === '__none__' }">{{ s.title }}</span>
          <span class="mt-epn">{{ s.groups.length }}</span>
        </div>
        <template v-if="epicOpen(s.key)">
          <div v-for="g in s.groups" :key="g.key" class="mt-hg" :class="{ notmine: !g.mine }">
            <div class="mt-hgh tkt" :data-key="g.key">
              <span class="mt-key">{{ g.key }}</span>
              <span class="mt-title">{{ g.title }}</span>
              <span v-if="!g.mine" class="mt-owner">{{ g.assignee || '미할당' }} 담당</span>
              <span v-if="g.pct !== null" class="mt-roll">
                <span class="mt-bar2 w"><i :style="{ width: g.pct + '%' }"></i></span><em>{{ g.pct }}%</em>
              </span>
            </div>
            <div class="mt-hgb">
              <div v-for="a in g.visAtoms" :key="a.key" class="mt-row sub tkt" :data-key="a.key"
                   :class="{ done: a.statusCategory === 'done' }">
                <span class="mt-pri" :class="a.priBand"></span>
                <span class="mt-dot" :class="'st-' + a.statusCategory"></span>
                <span class="mt-key">{{ a.key }}</span>
                <span class="mt-title">{{ a.title }}</span>
                <span class="mt-due" :class="dueBand(a.dueDays)">{{ dueLabel(a.dueDays) || '—' }}</span>
              </div>
              <button v-if="g.others.length" class="mt-others" @click="toggleOthers(g)">
                <span class="chev" :class="{ open: othersOpen(g) }">▸</span>
                동료 {{ g.others.length }}개 · {{ g.othersDone }} 완료
              </button>
              <div v-if="othersOpen(g)" class="mt-otherlist">
                <div v-for="o in g.others" :key="o.key" class="mt-row ro tkt" :data-key="o.key">
                  <span class="mt-dot" :class="'st-' + o.statusCategory"></span>
                  <span class="mt-key">{{ o.key }}</span>
                  <span class="mt-title">{{ o.title }}</span>
                  <span class="mt-owner">{{ o.assignee || '미할당' }}</span>
                </div>
              </div>
            </div>
          </div>
        </template>
      </div>
    </div>
  </div>`,
};
