// MyTasksView.js — '내 Task'.
//
// 백엔드는 **사실 하나**(내 실행 원자 + 부모/동료/Epic 맥락)만 주고, 배치는 전부 여기서 한다.
// 상단 옵션 패널이 세 축을 정한다:
//
//  1) 상태 축   가로 = 할당됨/진행중/최근완료가 **세로로 긴 칼럼**(칸반). 티켓은 1차원 리스트.
//               세로 = 상태가 **가로로 꽉 찬 패널**로 쌓임. 티켓은 그리드.
//               ★ 그룹은 늘 상태의 **반대 축**에 놓인다 — 가로 모드면 그룹이 위아래로 쌓이고,
//                 세로 모드면 상태 패널 안에서 그룹이 좌우로 늘어선다.
//  2) 그룹화    없음 = 모든 Task/Sub-Task가 개별 카드
//               Task = 같은 Task가 그룹 패널, 그 안에 Sub-Task
//               Epic = 같은 Epic이 그룹 패널, 그 안에서 Task가 다시 Sub-Task들의 그룹 카드
//  3) 유관 보기 그룹 안에서 담당이 '나'인 티켓을 강조해 먼저 보여주고,
//               확장 버튼을 누르면 내가 담당이 아닌 티켓(동료 몫)도 함께 보여준다.
//
// 카드 모양은 배치와 무관하게 하나다(.mt-card) — 리스트든 그리드든 같은 것을 읽는다.
import { api } from "../../lib/api.js";
import TypeBadge from "../ui/TypeBadge.js";

const NO_DUE = 1e6;

function dueLabel(d) {
  if (d === null || d === undefined) return "";
  return d < 0 ? "D+" + -d : d === 0 ? "오늘" : "D-" + d;
}
function dueBand(d) {
  if (d === null || d === undefined) return "none";
  return d < 0 ? "over" : d === 0 ? "today" : d <= 7 ? "soon" : "later";
}

// 상태 축 — 순서가 곧 작업 흐름이다.
const STATES = [
  { k: "todo", label: "할당됨" },
  { k: "inprogress", label: "진행 중" },
  { k: "done", label: "최근 완료" },
];
const SORTS = [
  { k: "due", label: "마감", hint: "마감 → 우선순위" },
  { k: "pri", label: "우선순위", hint: "우선순위 → 마감" },
];

export default {
  name: "MyTasksView",
  components: { TypeBadge },
  data() {
    return {
      model: null, loading: true, err: "",
      axis: "h",            // h = 상태를 가로축(칸반) | v = 상태를 세로축(가로 패널)
      groupBy: "task",      // none | task | epic
      showRelated: false,   // 유관 Task(내 담당이 아닌 티켓)도 함께
      openGroups: {},       // 그룹별 개별 확장(유관 보기)
      scope: "assignee",    // assignee | reporter | both
      openFilter: "all",    // 할당됨 축: all | 2w   (서버 질의 조건)
      doneFilter: "1w",     // 완료 축 기간: 1w | 1m (서버 질의 조건)
      sort: "due",
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
    states() { return STATES; },
    sorts() { return SORTS; },
    doneDays() { return (this.model && this.model.doneWindowDays) || 7; },

    /** 모든 카드(내 것 + 유관) — 배치 이전의 평면 목록. 각 카드는 소속(부모/Epic)을 안다. */
    allCards() {
      const out = [];
      for (const g of this.groups) {
        for (const a of g.atoms) out.push(this.card(a, g, true));
        for (const o of g.others) out.push(this.card(o, g, false));
      }
      return out;
    },
    counts() {
      const c = { todo: 0, inprogress: 0, done: 0, related: 0 };
      for (const x of this.allCards) {
        if (!x.mine) { c.related++; continue; }
        c[x.statusCategory] = (c[x.statusCategory] || 0) + 1;
      }
      return c;
    },

    /** 화면에 그릴 그룹 목록. 그룹화 방식에 따라 1단(Task) / 2단(Epic) / 없음. */
    panels() {
      if (this.groupBy === "none") {
        return [{ key: "__all__", kind: "none", cards: this.visible(this.allCards) }];
      }
      if (this.groupBy === "task") {
        return this.groups.map((g) => this.taskPanel(g))
          .filter((p) => p.cards.length || p.hiddenCount)
          .sort((a, b) => a.rank - b.rank);
      }
      // Epic — 그룹 패널 안에서 Task 가 다시 Sub-Task 들의 그룹 카드가 된다.
      const by = new Map();
      for (const g of this.groups) {
        const k = g.epic || "__none__";
        if (!by.has(k)) by.set(k, []);
        by.get(k).push(g);
      }
      const keys = [...by.keys()].filter((k) => k !== "__none__");
      const out = keys.map((k) => this.epicPanel(k, by.get(k)));
      out.sort((a, b) => a.rank - b.rank);
      if (by.has("__none__")) out.push(this.epicPanel("__none__", by.get("__none__")));
      return out.filter((p) => p.subPanels.some((s) => s.cards.length || s.hiddenCount));
    },
  },
  methods: {
    async load() {
      this.loading = true; this.err = "";
      try {
        this.model = await api.myTasks({ scope: this.scope, openFilter: this.openFilter,
                                         doneFilter: this.doneFilter });
      }
      catch (e) { this.err = (e && e.message) || "불러오기 실패"; }
      finally { this.loading = false; }
    },
    setScope(v) { if (this.scope !== v) { this.scope = v; this.load(); } },
    // 축 필터는 JQL 조건이라 바꾸면 다시 받는다(클라이언트에서 걸러낼 수 있는 게 아니다)
    setOpenFilter(v) { if (this.openFilter !== v) { this.openFilter = v; this.load(); } },
    setDoneFilter(v) { if (this.doneFilter !== v) { this.doneFilter = v; this.load(); } },

    /** 원자/동료 티켓 → 카드. mine 이면 강조 대상이다. */
    card(t, g, mine) {
      return Object.assign({}, t, {
        mine,
        parent: g,
        epicKey: t.epic || g.epic || null,
        // 그룹 자체가 카드인 경우(하위 없는 단독 Task)와 하위 카드 구분
        isGroupSelf: t.key === g.key,
      });
    },
    // 유관 보기가 꺼져 있으면 내 담당만. 그룹 단위 확장은 openGroups 로 개별 허용.
    visible(cards, gkey) {
      if (this.showRelated || (gkey && this.openGroups[gkey])) return this.sorted(cards);
      return this.sorted(cards.filter((c) => c.mine));
    },
    hidden(cards, gkey) {
      if (this.showRelated || (gkey && this.openGroups[gkey])) return 0;
      return cards.filter((c) => !c.mine).length;
    },
    dueOf(c) { return (c.dueDays === null || c.dueDays === undefined) ? NO_DUE : c.dueDays; },
    sorted(cards) {
      const by = this.sort;
      return cards.slice().sort((a, b) => (by === "pri"
        ? (a.priRank - b.priRank || this.dueOf(a) - this.dueOf(b))
        : (this.dueOf(a) - this.dueOf(b) || a.priRank - b.priRank)) || (a.key < b.key ? -1 : 1));
    },
    /** 패널 정렬 키 — 그 안에서 가장 급한 내 카드가 순서를 정한다(급한 게 위/앞으로). */
    rankOf(cards) {
      const mine = cards.filter((c) => c.mine);
      const pool = mine.length ? mine : cards;
      if (!pool.length) return NO_DUE;
      return this.sort === "pri"
        ? Math.min(...pool.map((c) => c.priRank))
        : Math.min(...pool.map((c) => this.dueOf(c)));
    },
    taskPanel(g) {
      const cards = [].concat(g.atoms.map((a) => this.card(a, g, true)),
                              g.others.map((o) => this.card(o, g, false)));
      return {
        key: g.key, kind: "task", group: g,
        title: g.title, epicKey: g.epic,
        cards: this.visible(cards, g.key),
        hiddenCount: this.hidden(cards, g.key),
        rank: this.rankOf(cards),
      };
    },
    epicPanel(ek, gs) {
      const subPanels = gs.map((g) => this.taskPanel(g)).sort((a, b) => a.rank - b.rank);
      const all = [].concat(...gs.map((g) => [].concat(
        g.atoms.map((a) => this.card(a, g, true)), g.others.map((o) => this.card(o, g, false)))));
      return {
        key: ek, kind: "epic",
        title: ek === "__none__" ? "Epic 없음" : ((this.epicMap[ek] || {}).title || ek),
        none: ek === "__none__",
        subPanels, rank: this.rankOf(all),
      };
    },
    /** 패널의 카드를 상태별로 나눈다 — 상태 축이 가로든 세로든 이 함수를 쓴다. */
    byState(cards) {
      const m = { todo: [], inprogress: [], done: [] };
      for (const c of cards) (m[c.statusCategory] || m.todo).push(c);
      return m;
    },
    toggleGroup(k) { this.openGroups = Object.assign({}, this.openGroups, { [k]: !this.openGroups[k] }); },
    isOpen(k) { return this.showRelated || !!this.openGroups[k]; },
    epicTitle(k) { return k ? ((this.epicMap[k] || {}).title || k) : null; },
    dueLabel, dueBand,
  },
  template: `
  <div class="mytasks" :class="'ax-' + axis">
    <div class="mt-head">
      <div>
        <h2 class="mt-h">내 Task<span v-if="model && model.user" class="mt-who">{{ model.user.name || model.user.id }}</span></h2>
        <div class="mt-sub">상태 축·그룹화·유관 보기를 바꾸면 같은 데이터를 다르게 배치합니다.</div>
      </div>
      <div v-if="model" class="mt-counts">
        <span class="mt-c todo">할당됨 <b>{{ counts.todo }}</b></span>
        <span class="mt-c prog">진행 중 <b>{{ counts.inprogress }}</b></span>
        <span class="mt-c done">최근 {{ doneDays }}일 완료 <b>{{ counts.done }}</b></span>
        <span class="mt-c" :class="{ zero: !counts.related }">유관 <b>{{ counts.related }}</b></span>
      </div>
    </div>

    <!-- 옵션 패널 — 세 축을 여기서 정한다 -->
    <div class="mt-bar">
      <div class="mt-opt">
        <span class="mt-opt-l">상태 축</span>
        <div class="mt-seg">
          <button :class="{ on: axis === 'h' }" @click="axis = 'h'"
                  title="할당됨/진행중/최근완료를 세로로 긴 칼럼으로 — 티켓은 리스트">가로축</button>
          <button :class="{ on: axis === 'v' }" @click="axis = 'v'"
                  title="상태를 가로로 꽉 찬 패널로 쌓기 — 티켓은 그리드">세로축</button>
        </div>
      </div>
      <div class="mt-opt">
        <span class="mt-opt-l">그룹화</span>
        <div class="mt-seg">
          <button :class="{ on: groupBy === 'none' }" @click="groupBy = 'none'" title="모든 티켓을 개별 카드로">없음</button>
          <button :class="{ on: groupBy === 'task' }" @click="groupBy = 'task'" title="같은 Task 로 묶고 그 안에 Sub-Task">Task</button>
          <button :class="{ on: groupBy === 'epic' }" @click="groupBy = 'epic'" title="같은 Epic 으로 묶고, 그 안에서 Task 가 Sub-Task 를 묶는다">Epic</button>
        </div>
      </div>
      <div class="mt-opt">
        <span class="mt-opt-l">범위</span>
        <div class="mt-seg">
          <button :class="{ on: scope === 'assignee' }" @click="setScope('assignee')" title="담당자가 나">담당</button>
          <button :class="{ on: scope === 'reporter' }" @click="setScope('reporter')" title="내가 등록">등록</button>
          <button :class="{ on: scope === 'both' }" @click="setScope('both')" title="담당 + 등록">둘 다</button>
        </div>
      </div>
      <div class="mt-opt">
        <span class="mt-opt-l">정렬</span>
        <div class="mt-seg">
          <button v-for="s in sorts" :key="s.k" :class="{ on: sort === s.k }" @click="sort = s.k" :title="s.hint">{{ s.label }}</button>
        </div>
      </div>
      <!-- 상태 축별 범위 — 컬럼 헤더에 메뉴를 달면 헤더가 시끄러워지고 세로축 모드에선 둘 곳이 없다.
           컨트롤은 이 패널 한 곳에 모아 둔다. -->
      <div class="mt-opt">
        <span class="mt-opt-l st todo">할당됨</span>
        <div class="mt-seg">
          <button :class="{ on: openFilter === 'all' }" @click="setOpenFilter('all')" title="담당된 모든 미착수 티켓">모두</button>
          <button :class="{ on: openFilter === '2w' }" @click="setOpenFilter('2w')" title="최근 2주 안에 갱신된 것만 — 오래 방치된 건 감춘다">2주 내 갱신</button>
        </div>
      </div>
      <div class="mt-opt">
        <span class="mt-opt-l st done">완료</span>
        <div class="mt-seg">
          <button :class="{ on: doneFilter === '1w' }" @click="setDoneFilter('1w')" title="최근 1주 안에 완료">1주</button>
          <button :class="{ on: doneFilter === '1m' }" @click="setDoneFilter('1m')" title="최근 1달 안에 완료">1달</button>
        </div>
      </div>
      <label class="mt-tg" title="그룹 안에서 내 담당이 아닌 티켓(동료 몫)도 함께 본다">
        <input type="checkbox" v-model="showRelated"> 유관 Task 보기
      </label>
      <button class="mt-refresh" @click="load" title="다시 불러오기">↻</button>
    </div>

    <div v-if="loading" class="loading">불러오는 중…</div>
    <div v-else-if="err" class="mt-err">{{ err }}</div>
    <div v-else-if="!panels.length" class="mt-empty">표시할 일감이 없습니다.</div>

    <!-- ══ 상태 = 가로축 : 칼럼 헤더는 맨 위 한 줄, 그룹은 **각자 하나의 카드** 안에 3칼럼 ══
         (그룹마다 헤더를 반복하면 빈 헤더가 그룹 수만큼 늘어나고, 반대로 헤더만 위에 두고 카드를
          안 씌우면 어디부터 어디까지가 한 그룹인지 안 읽힌다 — 둘 다 피한 구조다.) -->
    <template v-else-if="axis === 'h'">
      <div class="mt-headrow">
        <div v-for="st in states" :key="'h-' + st.k" class="mt-colh" :class="'c-' + st.k">
          {{ st.label }}
          <b>{{ allCards.filter(c => c.statusCategory === st.k && (showRelated || c.mine)).length }}</b>
        </div>
      </div>

      <template v-for="p in panels" :key="p.key">
        <!-- 그룹화 없음 — 묶음이 없으니 카드 테두리도 없다 -->
        <div v-if="p.kind === 'none'" class="mt-gbody plain">
          <div v-for="st in states" :key="'n-' + st.k" class="mt-cell" :class="'c-' + st.k">
            <div v-for="c in byState(p.cards)[st.k]" :key="c.key" class="mt-card tkt"
                 :class="{ mine: c.mine, rel: !c.mine, done: c.statusCategory === 'done' }" :data-key="c.key">
              <span class="mt-pri" :class="c.priBand" :title="'우선순위: ' + c.pri"></span>
              <TypeBadge :type="c.type" />
              <span class="mt-key">{{ c.key }}</span>
              <span class="mt-title">{{ c.title }}</span>
              <span v-if="c.epicKey" class="mt-epic sm">◆ {{ epicTitle(c.epicKey) }}</span>
              <span v-if="!c.mine" class="mt-owner">{{ c.assignee || '미할당' }}</span>
              <span class="mt-due" :class="dueBand(c.dueDays)">{{ dueLabel(c.dueDays) || '—' }}</span>
            </div>
          </div>
        </div>

        <!-- Task 그룹 = 카드 하나 -->
        <div v-else-if="p.kind === 'task'" class="mt-gcard2 k-task">
          <div class="mt-gh">
            <span class="mt-pkey tkt" :data-key="p.key">{{ p.key }}</span>
            <span class="mt-pt">{{ p.title }}</span>
            <span v-if="p.epicKey" class="mt-epic">◆ {{ epicTitle(p.epicKey) }}</span>
            <span v-else class="mt-epic none">Epic 없음</span>
            <button v-if="p.hiddenCount" class="mt-more" @click="toggleGroup(p.key)">유관 {{ p.hiddenCount }} 보기</button>
            <button v-else-if="!showRelated && openGroups[p.key]" class="mt-more on" @click="toggleGroup(p.key)">유관 숨기기</button>
          </div>
          <div class="mt-gbody">
            <div v-for="st in states" :key="p.key + st.k" class="mt-cell"
                 :class="['c-' + st.k, { empty: !byState(p.cards)[st.k].length }]">
                <div v-for="c in byState(p.cards)[st.k]" :key="c.key" class="mt-card tkt"
                     :class="{ mine: c.mine, rel: !c.mine, done: c.statusCategory === 'done' }" :data-key="c.key">
                  <span class="mt-pri" :class="c.priBand" :title="'우선순위: ' + c.pri"></span>
                  <TypeBadge :type="c.type" />
                  <span class="mt-key">{{ c.key }}</span>
                  <span class="mt-title">{{ c.title }}</span>
                  <span v-if="!c.mine" class="mt-owner">{{ c.assignee || '미할당' }}</span>
                  <span class="mt-due" :class="dueBand(c.dueDays)">{{ dueLabel(c.dueDays) || '—' }}</span>
                </div>
            </div>
          </div>
        </div>

        <!-- Epic 그룹 = 카드 하나, 그 안에서 Task 가 다시 작은 카드 -->
        <div v-else class="mt-gcard2 k-epic" :class="{ none: p.none }">
          <div class="mt-gh">
            <span v-if="!p.none" class="mt-pdia">◆</span>
            <span class="mt-pt">{{ p.title }}</span>
            <span class="mt-pn">Task {{ p.subPanels.length }}</span>
          </div>
          <div class="mt-gcard2 k-task inner" v-for="sp in p.subPanels" :key="sp.key">
            <div class="mt-gh sub">
              <span class="mt-pkey tkt" :data-key="sp.key">{{ sp.key }}</span>
              <span class="mt-pt">{{ sp.title }}</span>
              <button v-if="sp.hiddenCount" class="mt-more" @click="toggleGroup(sp.key)">유관 {{ sp.hiddenCount }} 보기</button>
              <button v-else-if="!showRelated && openGroups[sp.key]" class="mt-more on" @click="toggleGroup(sp.key)">유관 숨기기</button>
            </div>
            <div class="mt-gbody">
              <div v-for="st in states" :key="sp.key + st.k" class="mt-cell"
                   :class="['c-' + st.k, { empty: !byState(sp.cards)[st.k].length }]">
                <div v-for="c in byState(sp.cards)[st.k]" :key="c.key" class="mt-card tkt"
                     :class="{ mine: c.mine, rel: !c.mine, done: c.statusCategory === 'done' }" :data-key="c.key">
                  <span class="mt-pri" :class="c.priBand" :title="'우선순위: ' + c.pri"></span>
                  <TypeBadge :type="c.type" />
                  <span class="mt-key">{{ c.key }}</span>
                  <span class="mt-title">{{ c.title }}</span>
                  <span v-if="!c.mine" class="mt-owner">{{ c.assignee || '미할당' }}</span>
                  <span class="mt-due" :class="dueBand(c.dueDays)">{{ dueLabel(c.dueDays) || '—' }}</span>
                </div>
              </div>
            </div>
          </div>
        </div>
      </template>
    </template>

    <!-- ══ 상태 = 세로축 : 상태 패널이 가로로 꽉 차서 쌓이고, 그 안에서 그룹이 좌우로 ══ -->
    <template v-else>
      <div v-for="st in states" :key="st.k" class="mt-band" :class="'c-' + st.k">
        <div class="mt-bandh">{{ st.label }}
          <b>{{ allCards.filter(c => c.statusCategory === st.k && (showRelated || c.mine)).length }}</b>
        </div>
        <!-- 그룹화 없음 → 카드 그리드 하나 -->
        <div v-if="groupBy === 'none'" class="mt-grid2">
          <div v-for="c in byState(panels[0].cards)[st.k]" :key="c.key" class="mt-card tkt"
               :class="{ mine: c.mine, rel: !c.mine, done: c.statusCategory === 'done' }" :data-key="c.key">
            <span class="mt-pri" :class="c.priBand"></span>
            <TypeBadge :type="c.type" />
            <span class="mt-key">{{ c.key }}</span>
            <span class="mt-title">{{ c.title }}</span>
            <span v-if="c.epicKey" class="mt-epic sm">◆ {{ epicTitle(c.epicKey) }}</span>
            <span v-if="!c.mine" class="mt-owner">{{ c.assignee || '미할당' }}</span>
            <span class="mt-due" :class="dueBand(c.dueDays)">{{ dueLabel(c.dueDays) || '—' }}</span>
          </div>
          <div v-if="!byState(panels[0].cards)[st.k].length" class="mt-none">해당 상태의 티켓 없음</div>
        </div>
        <!-- 그룹화 있음 → 그룹이 좌우로 늘어서고 각 그룹 안이 그리드 -->
        <div v-else class="mt-grouprow">
          <template v-for="p in panels" :key="p.key">
            <template v-if="p.kind === 'epic'">
              <div v-for="sp in p.subPanels" :key="sp.key" v-show="byState(sp.cards)[st.k].length"
                   class="mt-gcard">
                <div class="mt-gch">
                  <span class="mt-epic">◆ {{ p.title }}</span>
                  <span class="mt-pkey tkt" :data-key="sp.key">{{ sp.key }}</span>
                  <span class="mt-pt">{{ sp.title }}</span>
                </div>
                <div class="mt-grid2">
                  <div v-for="c in byState(sp.cards)[st.k]" :key="c.key" class="mt-card tkt"
                       :class="{ mine: c.mine, rel: !c.mine, done: c.statusCategory === 'done' }" :data-key="c.key">
                    <span class="mt-pri" :class="c.priBand"></span>
                    <TypeBadge :type="c.type" />
                    <span class="mt-key">{{ c.key }}</span>
                    <span class="mt-title">{{ c.title }}</span>
                    <span v-if="!c.mine" class="mt-owner">{{ c.assignee || '미할당' }}</span>
                    <span class="mt-due" :class="dueBand(c.dueDays)">{{ dueLabel(c.dueDays) || '—' }}</span>
                  </div>
                </div>
              </div>
            </template>
            <div v-else v-show="byState(p.cards)[st.k].length" class="mt-gcard">
              <div class="mt-gch">
                <span class="mt-pkey tkt" :data-key="p.key">{{ p.key }}</span>
                <span class="mt-pt">{{ p.title }}</span>
                <button v-if="p.hiddenCount" class="mt-more" @click="toggleGroup(p.key)">유관 {{ p.hiddenCount }}</button>
              </div>
              <div class="mt-grid2">
                <div v-for="c in byState(p.cards)[st.k]" :key="c.key" class="mt-card tkt"
                     :class="{ mine: c.mine, rel: !c.mine, done: c.statusCategory === 'done' }" :data-key="c.key">
                  <span class="mt-pri" :class="c.priBand"></span>
                  <TypeBadge :type="c.type" />
                  <span class="mt-key">{{ c.key }}</span>
                  <span class="mt-title">{{ c.title }}</span>
                  <span v-if="!c.mine" class="mt-owner">{{ c.assignee || '미할당' }}</span>
                  <span class="mt-due" :class="dueBand(c.dueDays)">{{ dueLabel(c.dueDays) || '—' }}</span>
                </div>
              </div>
            </div>
          </template>
        </div>
      </div>
    </template>
  </div>`,
};
