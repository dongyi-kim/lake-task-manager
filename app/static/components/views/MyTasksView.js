// MyTasksView.js — '내 Task'.
//
// 백엔드는 **사실 하나**(내 실행 원자 + 부모/동료/Epic 맥락)만 주고, 배치는 전부 여기서 한다.
// 상단 옵션 패널이 세 축을 정한다:
//
//  1) 상태 축   가로 = 할당됨/진행중/최근완료가 **세로로 긴 칼럼**(칸반). 티켓은 1차원 리스트.
//               세로 = 상태가 **가로로 꽉 찬 패널**로 쌓임. 티켓은 그리드.
//               ★ 그룹은 늘 상태의 **반대 축**에 놓인다 — 가로 모드면 그룹이 위아래로 쌓이고,
//                 세로 모드면 상태 패널 안에서 그룹이 좌우로 늘어선다.
//  2) 그룹화    없음     = 모든 Task/Sub-Task가 개별 카드
//               Sub Task = 같은 부모 Task로 묶고 그 안에 Sub-Task. 하위가 없는 Task는
//                          묶을 게 없으므로 그룹이 아니라 그냥 카드다.
//  3) 유관 보기 그룹 안에서 담당이 '나'인 티켓을 강조해 먼저 보여주고,
//               확장 버튼을 누르면 내가 담당이 아닌 티켓(동료 몫)도 함께 보여준다.
//
// 카드 모양은 배치와 무관하게 하나다(.mt-card) — 리스트든 그리드든 같은 것을 읽는다.
import { api } from "../../lib/api.js";
import TypeBadge from "../ui/TypeBadge.js";
import Avatar from "../ui/Avatar.js";
import PriIcon from "../ui/PriIcon.js";
import TaskCard, { isHot, isUrgent } from "../ui/TaskCard.js";
import DueText from "../ui/DueText.js";
import { categoryColor } from "../../lib/colors.js";

const NO_DUE = 1e6;


// 상태 축 — 순서가 곧 작업 흐름이다.
const STATES = [
  { k: "todo", label: "할당됨" },
  { k: "inprogress", label: "진행 중" },
  { k: "done", label: "최근 완료" },
];
// Epic 시그니처 컬러 — 같은 Epic 은 어느 화면·어느 카드에서도 같은 색.
// 사용자 VoC 는 Epic 이 없어도 **전용 Epic 처럼** 자기 색을 갖는다(Epic 이 배정돼 있으면 그쪽 우선).
// Epic 도 VoC 도 아니면 색을 주지 않는다 — 없는 소속을 색으로 지어내지 않는다.
const VOC_SIG = "var(--ty-story)";
function epicSig(card) {
  if (card.epicKey) return categoryColor(card.epicKey);
  if (card.voc) return VOC_SIG;
  return null;
}

// 옵션 정의 — 라벨/설명이 한곳에 있어야 콤보박스와 저장 키가 어긋나지 않는다.
// reload: true 면 서버 질의 조건이라 값이 바뀌면 다시 받아야 한다(클라이언트에서 거를 수 없다).
const OPTIONS = [
  { key: "axis", label: "상태 축", opts: [
    { k: "h", label: "가로축", hint: "할당됨/진행중/최근완료를 세로로 긴 칼럼으로 — 티켓은 리스트" },
    { k: "v", label: "세로축", hint: "상태를 가로로 꽉 찬 패널로 쌓기 — 티켓은 그리드" }] },
  { key: "groupBy", label: "그룹화", opts: [
    { k: "none", label: "없음", hint: "모든 티켓을 개별 카드로" },
    { k: "sub", label: "Sub Task", hint: "부모 Task 로 묶고 그 안에 Sub-Task — 하위가 없는 Task 는 그냥 카드" }] },
  { key: "subView", label: "Sub Task", opts: [
    { k: "collapsed", label: "모두 접기", hint: "하위를 모두 접는다 — 부모 Task 만 본다" },
    { k: "mine", label: "내 티켓만", hint: "하위 중 내가 담당인 것만 펼친다" },
    { k: "all", label: "모든 티켓", hint: "동료가 담당인 하위(유관)까지 모두 펼친다" }] },
  { key: "scope", label: "범위", reload: true, opts: [
    { k: "assignee", label: "담당", hint: "담당자가 나" },
    { k: "reporter", label: "등록", hint: "내가 등록" },
    { k: "both", label: "둘 다", hint: "담당 + 등록" }] },
  { key: "sort", label: "정렬", opts: [
    { k: "due", label: "마감", hint: "1차 마감 → 2차 우선순위" },
    { k: "pri", label: "우선순위", hint: "1차 우선순위 → 2차 마감" }] },
  { key: "openFilter", label: "할당됨", cls: "todo", reload: true, opts: [
    { k: "all", label: "모두", hint: "담당된 모든 미착수 티켓" },
    { k: "2w", label: "2주 내 갱신", hint: "최근 2주 안에 갱신된 것만 — 오래 방치된 건 감춘다" }] },
  { key: "doneFilter", label: "완료", cls: "done", reload: true, opts: [
    { k: "1w", label: "1주", hint: "최근 1주 안에 완료" },
    { k: "1m", label: "1달", hint: "최근 1달 안에 완료" }] },
];
const PREF_KEY = "mytasks.opts";

export default {
  name: "MyTasksView",
  components: { TypeBadge, Avatar, TaskCard, PriIcon, DueText },
  data() {
    return {
      model: null, loading: true, err: "",
      axis: "h",            // h = 상태를 가로축(칸반) | v = 상태를 세로축(가로 패널)
      groupBy: "sub",       // none | sub (부모 Task 로 묶기)
      // 하위(Sub-Task) 보기 — **화면 전체에 하나**. collapsed | mine | all.
      // 전에는 그룹마다 버튼을 달아 개별로 바꿨는데, 그룹이 열 개면 열 번 눌러야 원하는 상태가 되고
      // 지금 무엇이 펼쳐져 있는지도 카드마다 달라 한눈에 안 잡혔다. 보기 방식은 화면의 성격이지
      // 그룹 하나하나의 속성이 아니다.
      subView: "mine",
      bandClosed: {},       // 세로축 모드에서 접어 둔 상태 밴드 { todo|inprogress|done: true }
      scope: "assignee",    // assignee | reporter | both
      openFilter: "all",    // 할당됨 축: all | 2w   (서버 질의 조건)
      doneFilter: "1w",     // 완료 축 기간: 1w | 1m (서버 질의 조건)
      sort: "due",
    };
  },
  mounted() { this.loadPrefs(); this.load(); },
  computed: {
    groups() { return (this.model && this.model.groups) || []; },
    epicMap() {
      const m = {};
      for (const e of (this.model && this.model.epics) || []) m[e.key] = e;
      return m;
    },
    states() { return STATES; },
    options() { return OPTIONS; },
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

    /** 화면에 그릴 목록.
     *  ★ 그룹은 **하위가 실제로 있을 때만** 만든다. Sub-Task 가 없는 Task 는 묶을 게 없으므로
     *    자기 자신만 담은 그룹 패널이 아니라 **그냥 티켓 카드**다(그게 사실에 맞는 표현이다). */
    panels() {
      if (this.groupBy === "none") {
        return [{ key: "__all__", kind: "none", cards: this.visible(this.allCards) }];
      }
      const out = this.groups.filter((g) => g.hasSubs).map((g) => this.taskPanel(g));
      const solo = this.soloPanel(this.groups.filter((g) => !g.hasSubs));
      if (solo) out.push(solo);
      return out.sort((a, b) => a.rank[0] - b.rank[0] || a.rank[1] - b.rank[1]);
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
    /** 옵션 하나 바꾸기. reload 옵션은 JQL 조건이라 바꾸면 다시 받는다
     *  (서버 질의 자체가 달라지므로 클라이언트에서 걸러낼 수 있는 게 아니다). */
    setOpt(o, v) {
      if (this[o.key] === v) return;
      this[o.key] = v;
      this.savePrefs();
      if (o.reload) this.load();
    },
    hintOf(o) { const cur = o.opts.find((x) => x.k === this[o.key]); return cur ? cur.hint : o.label; },

    /** 옵션은 브라우저에 남긴다 — 매번 같은 배치로 맞추는 건 화면이 할 일이지 사람이 할 일이 아니다.
     *  값 검증까지 하는 이유: 옵션 목록이 바뀌면 저장된 옛 값이 어디에도 없는 상태가 되고,
     *  그러면 select 가 빈 채로 뜨고 필터는 이상하게 걸린다. */
    loadPrefs() {
      let saved = null;
      try { saved = JSON.parse(localStorage.getItem(PREF_KEY) || "null"); } catch (e) { saved = null; }
      if (!saved) return;
      for (const o of OPTIONS) {
        if (o.opts.some((x) => x.k === saved[o.key])) this[o.key] = saved[o.key];
      }
    },
    savePrefs() {
      const out = {};
      for (const o of OPTIONS) out[o.key] = this[o.key];
      try { localStorage.setItem(PREF_KEY, JSON.stringify(out)); } catch (e) { /* 사파리 프라이빗 등 */ }
    },

    /** 원자/동료 티켓 → 카드. mine 이면 강조 대상이다. */
    card(t, g, mine) {
      // Sub-Task 에 마감이 없으면 **부모 Task 의 마감**을 쓴다. 실무에서 마감은 보통 부모에만
      // 걸리고 하위는 비워 두는데, 그걸 '마감 없음' 으로 두면 실제로는 부모와 같은 날 끝나야 할
      // 일이 안 급한 것처럼 보인다. 다만 빌려 온 값이므로 dueInherited 로 표시해 구분한다.
      const own = t.dueDays !== null && t.dueDays !== undefined;
      const inherit = !own && t.key !== g.key && g.dueDays !== null && g.dueDays !== undefined;
      return Object.assign({}, t, {
        mine,
        parent: g,
        epicKey: t.epic || g.epic || null,
        due: inherit ? g.due : t.due,
        dueDays: inherit ? g.dueDays : t.dueDays,
        dueInherited: inherit,
        // 그룹 자체가 카드인 경우(하위 없는 단독 Task)와 하위 카드 구분
        isGroupSelf: t.key === g.key,
      });
    },
    /** 그룹이 아닌(=하위 없는) 카드 묶음용 — 기본은 내 담당만, '유관 기본 펼침' 이면 전부.
        '모두 접기' 는 하위에만 걸리는 말이라 여기선 '내 것만' 과 같게 둔다 — 접을 하위가 없다. */
    visible(cards) {
      return this.sorted(this.subView === "all" ? cards : cards.filter((c) => c.mine));
    },
    dueOf(c) { return (c.dueDays === null || c.dueDays === undefined) ? NO_DUE : c.dueDays; },
    sorted(cards) {
      const by = this.sort;
      return cards.slice().sort((a, b) => (by === "pri"
        ? (a.priRank - b.priRank || this.dueOf(a) - this.dueOf(b))
        : (this.dueOf(a) - this.dueOf(b) || a.priRank - b.priRank)) || (a.key < b.key ? -1 : 1));
    },
    /** 패널 정렬 키 — 그 안에서 가장 급한 내 카드가 순서를 정한다(급한 게 위/앞으로).
     *  ★ 카드와 **같은 1·2차 규칙**을 쓴다. 1차만 보면 마감이 같은 그룹들의 순서가 우선순위와
     *    무관하게 흔들려, 그룹 안 카드는 제대로 정렬됐는데 그룹끼리는 아닌 상태가 된다. */
    rankOf(cards) {
      const mine = cards.filter((c) => c.mine);
      const pool = mine.length ? mine : cards;
      if (!pool.length) return [NO_DUE, 9];
      const due = Math.min(...pool.map((c) => this.dueOf(c)));
      const pri = Math.min(...pool.map((c) => c.priRank));
      return this.sort === "pri" ? [pri, due] : [due, pri];
    },
    taskPanel(g) {
      const mineCards = g.atoms.map((a) => this.card(a, g, true));
      const all = mineCards.concat(g.others.map((ot) => this.card(ot, g, false)));
      const mode = this.subView;
      const shown = mode === "collapsed" ? [] : (mode === "all" ? all : mineCards);
      return {
        key: g.key, kind: "task", group: g,
        title: g.title, epicKey: g.epic,
        mode, mineCount: mineCards.length, allCount: all.length,
        cards: this.sorted(shown),
        rank: this.rankOf(all),
      };
    },
    /** 하위 없는 Task 들을 묶음 없이 카드로만 모은 덩어리(그룹 패널이 아니다). */
    soloPanel(gs) {
      const cards = [];
      for (const g of gs) {
        for (const a of g.atoms) cards.push(this.card(a, g, true));
        for (const ot of g.others) cards.push(this.card(ot, g, false));
      }
      const vis = this.visible(cards);
      if (!vis.length) return null;
      return { key: "__solo__", kind: "solo", cards: vis, rank: this.rankOf(cards) };
    },
    /** 패널의 카드를 상태별로 나눈다 — 상태 축이 가로든 세로든 이 함수를 쓴다. */
    byState(cards) {
      const m = { todo: [], inprogress: [], done: [] };
      for (const c of cards) (m[c.statusCategory] || m.todo).push(c);
      return m;
    },
    /** 세로축 모드의 상태 밴드 접기 — 지금 안 보는 상태를 통째로 치우고 화면을 벌 수 있게. */
    bandOpen(k) { return !this.bandClosed[k]; },
    toggleBand(k) { this.bandClosed = Object.assign({}, this.bandClosed, { [k]: !this.bandClosed[k] }); },
    bandCount(k) {
      return this.allCards.filter((c) => c.statusCategory === k && (this.subView === "all" || c.mine)).length;
    },
    /** 하위 보기 모드 — 그룹별 설정이 있으면 그것, 없으면 상단 옵션이 정한 기본값. */
    /** 펼치기 버튼 — 접기 → 내 것만 → 전체 → 접기 로 순환한다. */
    epicTitle(k) { return k ? ((this.epicMap[k] || {}).title || k) : null; },
    /** 카드 좌측 띠 색 — 소속 Epic 의 시그니처 컬러. 없으면 null(중립). */
    sigOf(c) { return epicSig(c); },
    sigStyle(c) {
      // 그룹(부모 Task)은 epicKey 가 아니라 epic 필드를 쓴다 — 둘 다 받아 준다.
      const v = epicSig({ epicKey: c.epicKey || c.epic, voc: c.voc });
      return v ? { "--sig": v } : {};
    },
    // 급함 판정은 2줄 카드와 **같은 함수**를 쓴다. 그룹·Sub-Task 카드가 다른 기준으로 붉어지면
    // 같은 화면에서 '급함' 의 뜻이 두 개가 된다.
    isHotC(c) { return c.statusCategory !== "done" && isHot(c.dueDays); },
    isUrgentC(c) { return c.statusCategory !== "done" && isUrgent(c); },
  },
  template: `
  <div class="mytasks" :class="'ax-' + axis">
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
          <b>{{ allCards.filter(c => c.statusCategory === st.k && (subView === 'all' || c.mine)).length }}</b>
        </div>
      </div>

      <template v-for="p in panels" :key="p.key">
        <!-- 그룹화 없음 / 하위 없는 Task 묶음 — 묶을 게 없으니 카드 테두리도 없다 -->
        <div v-if="p.kind === 'none' || p.kind === 'solo'" class="mt-gbody plain">
          <div v-for="st in states" :key="'n-' + st.k" class="mt-cell"
               :class="['c-' + st.k, { empty: !byState(p.cards)[st.k].length }]">
            <TaskCard v-for="c in byState(p.cards)[st.k]" :key="c.key" :card="c"
                   :style="sigStyle(c)" :epic-title="epicTitle(c.epicKey)" />
          </div>
        </div>

        <!-- Task 그룹 = 카드 하나 -->
        <div v-else-if="p.kind === 'task'" class="mt-gcard2 k-task" :style="sigStyle(p.group)">
          <div class="mt-gh">
            <div class="mt-card parent tkt" :data-key="p.key" :style="sigStyle(p.group)"
                 :class="{ mine: p.group.mine, rel: !p.group.mine, done: p.group.statusCategory === 'done',
                        urgent: isUrgentC(p.group) }">
              <span v-if="isHotC(p.group)" class="tc-hot inline" title="마감이 일주일 이내입니다">🔥</span>
              <PriIcon :rank="p.group.priRank" :name="p.group.pri" />
              <TypeBadge :type="p.group.type" />
              <span class="mt-key">{{ p.key }}</span>
              <span class="mt-title">{{ p.title }}</span>
              <span v-if="p.group.pct !== null" class="mt-roll" :title="'하위 진척 ' + p.group.pct + '%'">
                <span class="mt-pbar"><i :style="{ width: p.group.pct + '%' }"></i></span>
                <em>{{ p.group.pct }}%</em>
              </span>
              <span v-if="p.epicKey" class="mt-epic" :title="'Epic: ' + epicTitle(p.epicKey)">{{ epicTitle(p.epicKey) }}</span>
              <span v-else-if="p.group.voc" class="mt-epic">사용자 VoC</span>
              <span v-else class="mt-epic none">Epic 없음</span>
              <span class="mt-owner" :class="{ me: p.group.mine }"
                    :title="(p.group.assignee || '미할당') + ' 담당' + (p.group.mine ? ' (나)' : '')">
                <Avatar :user="p.group.assigneeId" :name="p.group.assignee" :size="16" />{{ p.group.assignee || '미할당' }}</span>
              <DueText :card="p.group" />

            </div>
          </div>
          <div v-if="p.mode !== 'collapsed'" class="mt-gbody">
            <div v-for="st in states" :key="p.key + st.k" class="mt-cell"
                 :class="['c-' + st.k, { empty: !byState(p.cards)[st.k].length }]">
                <div v-for="c in byState(p.cards)[st.k]" :key="c.key" class="mt-card tkt"
                     :class="{ mine: c.mine, rel: !c.mine, done: c.statusCategory === 'done',
                             urgent: isUrgentC(c) }" :style="sigStyle(c)" :data-key="c.key">
                  <span v-if="isHotC(c)" class="tc-hot inline" title="마감이 일주일 이내입니다">🔥</span>
                  <PriIcon :rank="c.priRank" :name="c.pri" />
                  <span class="mt-key">{{ c.key }}</span>
                  <span class="mt-title">{{ c.title }}</span>
                  <span v-if="!c.mine || subView === 'all'" class="mt-owner" :class="{ me: c.mine }"
                        :title="(c.assignee || '미할당') + ' 담당' + (c.mine ? ' (나)' : '')">
                <Avatar :user="c.assigneeId" :name="c.assignee" :size="15" />{{ c.assignee || '미할당' }}</span>
                  <DueText :card="c" />
                </div>
            </div>
          </div>
        </div>

      </template>
    </template>

    <!-- ══ 상태 = 세로축 : 상태 패널이 가로로 꽉 차서 쌓이고, 그 안에서 그룹이 좌우로 ══ -->
    <template v-else>
      <div v-for="st in states" :key="st.k" class="mt-band" :class="['c-' + st.k, { closed: !bandOpen(st.k) }]">
        <button class="mt-bandh" @click="toggleBand(st.k)"
                :title="bandOpen(st.k) ? '접기' : '펼치기'">
          <span class="chev" :class="{ open: bandOpen(st.k) }">▸</span>{{ st.label }}
          <b>{{ bandCount(st.k) }}</b>
        </button>
        <template v-if="bandOpen(st.k)">
        <!-- 그룹화 없음 → 카드 그리드 하나 -->
        <div v-if="groupBy === 'none'" class="mt-grid2">
          <TaskCard v-for="c in byState(panels[0].cards)[st.k]" :key="c.key" :card="c"
                   :style="sigStyle(c)" :epic-title="epicTitle(c.epicKey)" />
          <div v-if="!byState(panels[0].cards)[st.k].length" class="mt-none">해당 상태의 티켓 없음</div>
        </div>
        <!-- 그룹화 있음 → 그룹이 좌우로 늘어서고 각 그룹 안이 그리드 -->
        <div v-else class="mt-grouprow">
          <template v-for="p in panels" :key="p.key">
            <!-- 하위 없는 Task 묶음 — 카드로만 -->
            <template v-if="p.kind === 'solo'">
              <TaskCard v-for="c in byState(p.cards)[st.k]" :key="'so-' + c.key" :card="c"
                   :style="sigStyle(c)" :epic-title="epicTitle(c.epicKey)" />
            </template>
            <div v-else v-show="byState(p.cards)[st.k].length" class="mt-gcard2 k-task"
                 :style="sigStyle(p.group)">
              <div class="mt-gh">
            <div class="mt-card parent tkt" :data-key="p.key" :style="sigStyle(p.group)"
                 :class="{ mine: p.group.mine, rel: !p.group.mine, done: p.group.statusCategory === 'done',
                        urgent: isUrgentC(p.group) }">
              <span v-if="isHotC(p.group)" class="tc-hot inline" title="마감이 일주일 이내입니다">🔥</span>
              <PriIcon :rank="p.group.priRank" :name="p.group.pri" />
              <TypeBadge :type="p.group.type" />
              <span class="mt-key">{{ p.key }}</span>
              <span class="mt-title">{{ p.title }}</span>
              <span v-if="p.group.pct !== null" class="mt-roll" :title="'하위 진척 ' + p.group.pct + '%'">
                <span class="mt-pbar"><i :style="{ width: p.group.pct + '%' }"></i></span>
                <em>{{ p.group.pct }}%</em>
              </span>
              
              <span class="mt-owner" :class="{ me: p.group.mine }"
                    :title="(p.group.assignee || '미할당') + ' 담당' + (p.group.mine ? ' (나)' : '')">
                <Avatar :user="p.group.assigneeId" :name="p.group.assignee" :size="16" />{{ p.group.assignee || '미할당' }}</span>
              <DueText :card="p.group" />

            </div>
              </div>
              <div class="mt-gbody one">
                <div v-for="c in byState(p.cards)[st.k]" :key="c.key" class="mt-card tkt"
                     :class="{ mine: c.mine, rel: !c.mine, done: c.statusCategory === 'done',
                             urgent: isUrgentC(c) }" :style="sigStyle(c)" :data-key="c.key">
                  <span v-if="isHotC(c)" class="tc-hot inline" title="마감이 일주일 이내입니다">🔥</span>
                  <PriIcon :rank="c.priRank" :name="c.pri" />
                  <span class="mt-key">{{ c.key }}</span>
                  <span class="mt-title">{{ c.title }}</span>
                  <span v-if="!c.mine || subView === 'all'" class="mt-owner" :class="{ me: c.mine }"
                        :title="(c.assignee || '미할당') + ' 담당' + (c.mine ? ' (나)' : '')">
                <Avatar :user="c.assigneeId" :name="c.assignee" :size="15" />{{ c.assignee || '미할당' }}</span>
                  <DueText :card="c" />
                </div>
              </div>
            </div>
          </template>
        </div>
        </template>
      </div>
    </template>
    <!-- 옵션 패널 — 세 축을 여기서 정한다. **화면 하단에 띄운다**:
         이 화면의 본론은 목록이고 옵션은 가끔 건드리는 것이라, 위에 두면 스크롤 한 판을
         옵션이 먼저 먹는다. 마크업도 목록 뒤에 둬 탭 이동이 목록을 먼저 지나가게 한다
         (보이는 순서와 읽는 순서를 맞춘다). -->
    <div class="mt-bar float">
      <label v-for="o in options" :key="o.key" class="mt-opt">
        <span class="mt-opt-l" :class="o.cls">{{ o.label }}</span>
        <select class="mt-sel" :value="$data[o.key]" @change="setOpt(o, $event.target.value)"
                :title="hintOf(o)">
          <option v-for="v in o.opts" :key="v.k" :value="v.k" :title="v.hint">{{ v.label }}</option>
        </select>
      </label>
      <button class="mt-refresh" @click="load" title="다시 불러오기">↻</button>
    </div>
  </div>`,
};
