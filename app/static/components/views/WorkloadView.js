// WorkloadView.js — 기능3 인력 워크로드. 두 막대(진행 중 / 최근 7일 완료), Task성·VoC성 색 구분,
// 모듈 평균 = 막대 뒤 세로 가이드선 + 헤더행 평균수치. [+] 확장 = 진행중/완료 티켓 리스트
//   (Due·D-day, 완료일시; 진행중=임박순·완료=최근순 정렬).
// 인력 = 본명(displayName 첫 어절) + 개발/운영 뱃지(id 사번 x+숫자/i+숫자). updated: 2026-07-09
import { api } from "../../lib/api.js";
import { moduleColor, categoryColor, sigColor } from "../../lib/colors.js";
import { ymd, ymdhm, tkt, dday } from "../../lib/fmt.js";
import ProgressBar from "../ui/ProgressBar.js";
import TypeBadge from "../ui/TypeBadge.js";
import Avatar from "../ui/Avatar.js";

// 상세 3컬럼 — 상태 흐름 순서(할당 → 진행 → 완료). 세 버킷 모두 같은 행 컴포넌트를 쓴다.
const WL_COLS = [
  { k: "open", label: "할당됨", cls: "todo" },
  { k: "inProgress", label: "진행 중", cls: "" },
  { k: "done7d", label: "최근 7일 완료", cls: "done" },
];
// Epic 분포 색 — **시그니처 컬러(categoryColor(epicKey))**. 같은 Epic 은 어느 화면·어느 사람에서도
// 같은 색이다(내 Task·WBS 와 정책 통일). 예전엔 화면 안 건수 순 팔레트라 사람마다 색이 달랐다.
const VOC_COLOR = "var(--ty-story)";      // 사용자 VoC — 전용 Epic 취급이라 고정색
const NONE_COLOR = "var(--border-hi)";    // Epic 없음

export default {
  name: "WorkloadView",
  components: { ProgressBar, TypeBadge, Avatar },
  data() {
    // 옵션(완료 성과 계산식·정렬)은 브라우저에 남긴다 — 매번 다시 고르는 건 화면이 할 일이다.
    let pref = {};
    try { pref = JSON.parse(localStorage.getItem("workload.opts") || "{}") || {}; } catch (e) { pref = {}; }
    return { d: null, err: "", open: {}, tkd: {}, actOpen: {}, linePos: {},
             metric: ["count", "hr"].includes(pref.metric) ? pref.metric : "count",
             sortBy: ["name", "assigned", "done"].includes(pref.sortBy) ? pref.sortBy : "name",
             // 한 번에 한 모듈만 본다(부하↓). 선택 모듈은 브라우저에 남긴다.
             mod: typeof pref.mod === "string" ? pref.mod : "",
             // 막대 색 구분: 'type'(티켓유형=기존) | 'epic'(소속 Epic)
             grouping: ["type", "epic"].includes(pref.grouping) ? pref.grouping : "type",
             // 'VoC 제외' — 소속 Epic 없는 사용자 VoC(__voc__) 를 막대·통계에서 뺀다.
             excludeVoc: pref.excludeVoc === true,
             dueRisk: null, dueRiskBusy: false, dueRiskFor: "",
             pstat: {}, busy: false };   // pstat[pid] = 그 인력의 통계 행(사람 by 사람 로딩)
  },
  created() {
    this.bodyRefs = {};                // 비반응 DOM 참조(모듈 body)
    // 좌하단 플로팅 새로고침 — 뷰마다 캐시 비우고 다시 받는 함수 이름이 달라 여기서 잇는다.
    window.addEventListener("force-refresh", this._fr = async () => {
      try { await this.hardRefresh(); }
      finally { window.dispatchEvent(new CustomEvent("force-refresh-done")); }
    });
  },
  async mounted() {
    await this.load();
    this._onResize = () => this.scheduleMeasure();
    window.addEventListener("resize", this._onResize);
  },
  unmounted() {
    if (this._onResize) window.removeEventListener("resize", this._onResize);
    window.removeEventListener("force-refresh", this._fr);
  },
  activated() { this.scheduleMeasure(); },   // keep-alive 재활성 시 평균선 재측정
  computed: {
    WL_COLS() { return WL_COLS; },
    // 지금 보고 있는 모듈(하단 메뉴에서 고른 것). 없으면 첫 모듈.
    curMod() {
      const ms = (this.d && this.d.modules) || [];
      return ms.find((m) => m.module === this.mod) || ms[0] || null;
    },
    curIdx() {
      const ms = (this.d && this.d.modules) || [];
      const i = ms.findIndex((m) => m.module === (this.curMod && this.curMod.module));
      return i < 0 ? 0 : i;
    },
    // 현재 모듈 인력 중 도착·성공한 통계만 (스케일·합계·평균 기준 — 다른 모듈 사람은 섞지 않는다)
    curStats() {
      const m = this.curMod;
      if (!m) return [];
      return (m.people || []).map((p) => this.pstat[p.id]).filter((s) => s && !s.error);
    },
    // 화면엔 선택한 모듈 하나만 (부하↓). 기존 모듈 마크업을 그대로 재사용하려고 배열로 감싼다.
    shownModules() { return this.curMod ? [this.curMod] : []; },
    totals() {
      const t = { p: this.curMod ? this.curMod.peopleCount : 0, op: 0, ip: 0, dn: 0 };
      this.curStats.forEach((s) => {
        t.op += this.barVal(s.open, "count");
        t.ip += this.barVal(s.inProgress, "count");
        t.dn += this.barVal(s.done7d, "count");
      });
      return t;
    },
    // 완료 실적 계산식은 '완료' 막대에만 적용(진행중은 timespent 가 없어 항상 티켓 수).
    doneUnit() { return this.metric === "hr" ? "h" : "건"; },
    // 막대 스케일 = 현재 모듈 인력 최대값. 사람이 더 로딩되면 커질 수 있다(막대가 자리 잡아간다).
    scale() {
      let ip = 1, dn = 1;
      this.curStats.forEach((s) => {
        ip = Math.max(ip, this.assignedCount(s));   // 진행중 + 미착수
        dn = Math.max(dn, this.barVal(s.done7d, this.metric));
      });
      return { ip, dn };
    },
    // 모듈 평균(세로선/수치) — **현재 모듈 전원이 로딩됐을 때만** 낸다.
    avgByMod() {
      const out = {};
      const avg = (xs) => xs.length ? Math.round(xs.reduce((a, b) => a + b, 0) / xs.length * 10) / 10 : 0;
      const m = this.curMod;
      if (m && this.moduleComplete(m)) {
        const ss = (m.people || []).map((p) => this.pstat[p.id]).filter((s) => s && !s.error);
        out[m.module] = { ip: avg(ss.map((s) => this.assignedCount(s))),
                          dn: avg(ss.map((s) => this.barVal(s.done7d, this.metric))) };
      }
      return out;
    },
    // ── 모듈 통계(하단 섹션) — 이미 로딩된 인력 번들을 프론트에서 집계한다(추가 조회 없음). ──
    statsReady() { return !!(this.curMod && this.moduleComplete(this.curMod)); },
    /** 현재 모듈의 Epic 집계: 진행중+최근완료를 Epic별로(metric 반영) + Epic별 인력 분해. */
    moduleEpicAgg() {
      const people = this.curStats, metric = this.metric;
      const names = {}, agg = {}, byPerson = {};
      people.forEach((s) => Object.assign(names, s.epicNames || {}));
      const val = (e) => (metric === "hr" ? (e.hr || 0) : (e.count || 0));
      people.forEach((s) => {
        ["open", "inProgress", "done7d"].forEach((bk) => {  // 할당+진행중+최근완료 (어느 상태든 소속 표시)
          const eps = (s[bk] && s[bk].epics) || {};
          for (const k in eps) {
            const v = val(eps[k]);
            if (!v) continue;
            agg[k] = (agg[k] || 0) + v;
            (byPerson[k] || (byPerson[k] = {}));
            byPerson[k][s.id] = (byPerson[k][s.id] || 0) + v;
          }
        });
      });
      return { agg, byPerson, names };
    },
    /** ① 모듈 → Epic 기여도: 정렬된 그룹(비중%) — 가로 스택 막대 + 범례. */
    moduleEpicGroups() {
      const { agg, names } = this.moduleEpicAgg;
      let groups = Object.keys(agg).map((k) =>
        Object.assign({ key: k, value: agg[k] }, this.groupMeta(k, names)));
      groups = this.orderGroups(groups);
      const total = groups.reduce((a, g) => a + g.value, 0) || 1;
      groups.forEach((g) => { g.pct = Math.round((g.value * 100) / total); });
      return { groups, total };
    },
    /** ② Epic → 인력 지분: 상위 Epic 마다 인력 세그먼트(지분%). 참여 1명이면 single 표식(버스팩터). */
    epicPeopleRows() {
      const { byPerson } = this.moduleEpicAgg;
      const nameOf = (pid) => (this.pstat[pid] && this.pstat[pid].name) || pid;
      // Epic + **사용자 VoC(Epic처럼 취급)**. 'VoC 제외' 면 orderGroups 가 이미 voc 를 뺐다.
      const epics = this.moduleEpicGroups.groups.filter((g) => g.kind === "epic" || g.kind === "voc").slice(0, 8);
      return epics.map((g) => {
        const pp = byPerson[g.key] || {};
        const segs = Object.keys(pp).map((pid) => ({
          pid, name: nameOf(pid), value: pp[pid], color: sigColor(pid),   // 그 사람 아바타와 같은 시그니처색
        })).sort((a, b) => b.value - a.value);
        const total = segs.reduce((a, s) => a + s.value, 0) || 1;
        segs.forEach((s) => {
          s.pct = Math.round((s.value * 100) / total);
          s.label = "(" + s.pct + "%)";                   // 좁으면 이 괄호%만 남고 이름은 숨는다(컨테이너 쿼리)
          s.title = s.name + " " + s.value + " (" + s.pct + "%)";
        });
        return { epic: g, total, segs, single: segs.length === 1 };
      });
    },
    /** 모니터링 — 버스팩터: 참여 인력이 1명뿐인 Epic(지식 집중 리스크). */
    busFactor() { return this.epicPeopleRows.filter((r) => r.single).map((r) => ({ epic: r.epic, person: r.segs[0] })); },
    /** 모니터링 — 부하 편중도: 미완료 할당(진행중+할당) 인력별, 상위 1명 비중. */
    loadSkew() {
      const load = this.curStats.map((s) => ({ id: s.id, name: s.name, v: this.assignedCount(s) }))
        .filter((x) => x.v > 0).sort((a, b) => b.v - a.v);
      const total = load.reduce((a, x) => a + x.v, 0);
      if (!total || !load.length) return null;
      return { top: load[0], pct: Math.round((load[0].v * 100) / total), total, n: load.length };
    },
    /** 모니터링 — 인력별 Epic 분산: 한 사람이 걸친 Epic 수(많은 순). ≥4 면 과다.
     *  소속 Epic 없는 사용자 VoC 도 처리 중이면 함께 표시("VoC + N Epic"). 'VoC 제외' 면 VoC 는 뺀다. */
    epicSpread() {
      return this.curStats.map((s) => {
        const keys = new Set(); let voc = false;
        ["open", "inProgress", "done7d"].forEach((bk) =>
          Object.keys((s[bk] && s[bk].epics) || {}).forEach((k) => {
            if (k === "__voc__") { if (!this.excludeVoc) voc = true; }
            else if (!k.startsWith("__")) keys.add(k);
          }));
        const label = voc ? ("VoC" + (keys.size ? " + " + keys.size + " Epic" : "")) : (keys.size + " Epic");
        return { id: s.id, name: s.name, count: keys.size, voc, label };
      }).filter((x) => x.count > 0 || x.voc).sort((a, b) => b.count - a.count);
    },
  },
  methods: {
    /** 모듈별 병렬 로딩: 골격 먼저 → 각 모듈 동시 요청 → 도착하는 대로 채움(느린 모듈이 안 막음).
     *  ★ 메서드로 둔다 — hardRefresh 가 this.load() 를 부른다(예전엔 mounted 인라인이라 죽었다). */
    async load() {
      this.err = "";
      try {
        this.d = await api.workloadShell();          // 골격 + 로스터(명단) 먼저 — 즉시 그려진다
        this.d.modules.forEach((m) => { this.open[m.module] = true; });
        // 선택 모듈: 저장된 값 우선 → 없으면 **세션 사용자 소속 모듈**(myModule) → 그것도 없으면 첫 모듈.
        const names = this.d.modules.map((m) => m.module);
        if (!names.includes(this.mod)) {
          this.mod = (this.d.myModule && names.includes(this.d.myModule)) ? this.d.myModule : (names[0] || "");
        }
        // **선택한 모듈의 인력만** 통계를 받는다(한 화면 = 한 모듈, 부하↓).
        this.loadModulePeople(this.mod);
      } catch (e) { this.err = e.message; }
    },
    /** 선택 모듈의 인력 통계를 사람 by 사람으로 로딩(이미 받은 사람은 건너뜀). */
    loadModulePeople(mod) {
      const m = (this.d && this.d.modules || []).find((x) => x.module === mod);
      if (!m) return;
      const pids = (m.people || []).map((p) => p.id).filter((id) => !this.pstat[id]);
      if (pids.length) this._loadPeople(pids);
    },
    /** 하단 메뉴에서 모듈 전환 — 그 모듈 인력만 로딩하고, 평균선 참조를 초기화한다. */
    selectModule(mod) {
      if (mod === this.mod) return;
      this.mod = mod;
      this.bodyRefs = {};            // 이전 모듈 body 참조 폐기(측정 대상은 현재 모듈뿐)
      this.linePos = {};
      this.dueRisk = null; this.dueRiskFor = "";    // 마감 리스크는 모듈별 — 초기화
      this._savePrefs();
      this.loadModulePeople(mod);
      this.scheduleMeasure();
      this.$nextTick(() => this.loadDueRisk());
    },
    /** 사람 by 사람 통계 로딩 — **동시 요청 상한(CONC)**을 둔다. 한꺼번에 다 쏘면 서버(로컬 fake·
     *  prod 단일 SSO 큐)를 덮쳐 조회가 통째로 실패한다(각자 3개 검색이라 18명이면 54개 동시).
     *  하나 끝날 때마다 다음을 채워, 화면은 사람 순으로 채워지되 서버는 안 붐빈다. */
    _loadPeople(pids) {
      const CONC = 3;
      let i = 0;
      const next = () => {
        if (i >= pids.length) return;
        const pid = pids[i++];
        api.workloadPerson(pid)
          .then((r) => { this.pstat[pid] = r; })
          .catch((e) => { this.pstat[pid] = { id: pid, error: true, message: (e && e.message) || String(e) }; })
          .finally(() => { this.scheduleMeasure(); this.loadDueRisk(); next(); });
      };
      for (let k = 0; k < Math.min(CONC, pids.length); k++) next();
    },
    /** 캐시를 비우고 전부 다시 받는다 — 낡은 값으로 화면을 지키는 구조라 사람이 끊을 수단이 필요하다. */
    async hardRefresh() {
      if (this.busy) return;
      this.busy = true;
      try {
        await api.refresh();
        this.d = null; this.pstat = {}; this.tkd = {}; this.actOpen = {}; this.linePos = {};
        await this.load();
      } catch (e) {
        this.err = (e && e.message) || "다시 받지 못했습니다.";
      } finally { this.busy = false; }
    },
    /** 이 모듈 인력이 **전원** 로딩됐는가(성공/실패 불문 — 도착했으면 됨). 평균선/헤더 합계 게이트. */
    moduleComplete(m) {
      const ppl = m.people || [];
      return ppl.length > 0 && ppl.every((p) => this.pstat[p.id]);
    },
    /** 모듈 헤더 합계 — 로딩된 인력까지 누적(부분 진행도 보여준다). loaded/total 로 진행 표시. */
    moduleAgg(m) {
      const t = { ip: 0, op: 0, dn: 0, loaded: 0 };
      (m.people || []).forEach((p) => {
        const s = this.pstat[p.id];
        if (!s) return;
        t.loaded++;
        if (s.error) return;
        t.ip += this.barVal(s.inProgress, "count");
        t.op += this.barVal(s.open, "count");
        t.dn += this.barVal(s.done7d, "count");
      });
      return t;
    },
    /** 세 버킷 티켓의 소속 Epic 분포.
     *  규칙: Epic 이 있으면 그 Epic. 없고 VoC 컴포넌트면 **'사용자 VoC' 를 전용 Epic 처럼** 따로 센다
     *  (Epic 없음에 섞으면 VoC 물량이 안 보인다). VoC 라도 Epic 이 배정돼 있으면 그 Epic 쪽으로 센다. */
    epicDist(pid) {
      const d = this.tkd[pid] || {};
      const all = [].concat(d.open || [], d.inProgress || [], d.done7d || []);
      const by = new Map();
      for (const t of all) {
        const key = t.epic ? t.epic : (t.voc ? "__voc__" : "__none__");
        const name = t.epic ? (t.epicName || t.epic) : (t.voc ? "사용자 VoC" : "Epic 없음");
        const kind = t.epic ? "epic" : (t.voc ? "voc" : "none");
        const g = by.get(key) || { key, name, kind, value: 0 };
        g.value++;
        by.set(key, g);
      }
      // 실제 Epic 을 건수 순으로 먼저, VoC·Epic 없음은 성격이 달라 항상 끝에 고정한다.
      const epics = [...by.values()].filter((g) => g.kind === "epic").sort((a, b) => b.value - a.value);
      epics.forEach((g) => { g.color = categoryColor(g.key); });   // 시그니처 컬러(키 기반, 전 화면 공통)
      const voc = by.get("__voc__"); if (voc) voc.color = VOC_COLOR;
      const none = by.get("__none__"); if (none) none.color = NONE_COLOR;
      const groups = epics.concat(voc ? [voc] : [], none ? [none] : []);
      return {
        groups, total: all.length,
        segments: groups.map((g) => ({ value: g.value, color: g.color,
                                       title: g.name + " · " + g.value + "건" })),
      };
    },
    /** 행의 Epic 뱃지 색 — 위 분포 막대와 같은 색이어야 눈으로 이어진다. */
    epicColorOf(pid, t) {
      const g = this.epicDist(pid).groups.find((x) => x.key === (t.epic || "__none__"));
      return (g && g.color) || NONE_COLOR;
    },
    mcolor(i) { return moduleColor(i); },
    openCount(p) { return p.open ? this.barVal(p.open, "count") : 0; },   // 미착수(To Do) 건수
    assignedCount(p) { return this.barVal(p.inProgress, "count") + this.openCount(p); },  // 미완료 할당
    mv(bar, kind, metric) { return (bar[metric] || {})[kind] || 0; },   // kind: 'task'|'subtask'|'voc'
    /** 소속 Epic 없는 사용자 VoC(__voc__) 양 — 'VoC 제외' 시 막대·통계·합계에서 뺀다. */
    noVocOf(bar, metric) {
      const g = bar && bar.epics && bar.epics.__voc__;
      if (!g) return 0;
      return metric === "hr" ? (g.hr || 0) : (g.count || 0);
    },
    /** VoC 제외 반영한 카테고리 voc 값(=voc 전체 − Epic없는 VoC). */
    vocVal(bar, metric) {
      return this.mv(bar, "voc", metric) - (this.excludeVoc ? this.noVocOf(bar, metric) : 0);
    },
    barVal(bar, metric) {   // 세 카테고리 합 (스케일·모듈평균·막대 총합 일치). VoC 제외 반영.
      return this.mv(bar, "task", metric) + this.mv(bar, "subtask", metric) + this.vocVal(bar, metric);
    },
    // 왼쪽 막대 = 미완료 할당. 정렬: Task→Sub-Task→VoC. 타입당 세그먼트 1개(폭=진행중+할당됨 합),
    // 오른쪽 '할당됨' 비율만 사선 오버레이(hatchFrac) → 숫자(합)는 세그먼트 중앙에 위치.
    segAssigned(p) {
      const ip = p.inProgress || {}, op = p.open || {};
      const kinds = [["task", "Task"], ["subtask", "Sub-Task"], ["voc", "VoC"]];
      const segs = [];
      kinds.forEach(([k, lb]) => {
        let ni = this.mv(ip, k, "count"), no = this.mv(op, k, "count");
        if (k === "voc" && this.excludeVoc) {           // Epic 없는 VoC 만큼 뺀다
          ni -= this.noVocOf(ip, "count"); no -= this.noVocOf(op, "count");
        }
        const c = ni + no;
        segs.push({
          value: c, color: "var(--wl-" + k + ")",
          hatchFrac: c > 0 ? no / c : 0,      // 오른쪽 이 비율만 사선(할당됨)
          label: String(c),
          title: lb + " 진행 중 " + ni + " · 할당됨 " + no + " (합 " + c + ")",
        });
      });
      return segs;
    },
    setMetric(mk) { this.metric = mk; this._savePrefs(); this.scheduleMeasure(); },
    setSort(k) { this.sortBy = k; this._savePrefs(); },
    setGrouping(g) { this.grouping = g; this._savePrefs(); this.scheduleMeasure(); },
    /** ① 모듈→Epic 스택 막대 세그먼트. */
    moduleEpicSegs() {
      const u = this.metric === "hr" ? "h" : "건";
      return this.moduleEpicGroups.groups.map((g) => ({
        value: g.value, color: g.color, title: g.name + " " + g.value + u + " (" + g.pct + "%)",
      }));
    },
    /** 마감 리스크 — 현재 모듈 인력의 할당/진행중 티켓에서 초과(D+)·임박(D-3) 집계(지연 로딩). */
    async loadDueRisk() {
      const mod = this.curMod ? this.curMod.module : "";
      if (this.dueRiskBusy || (this.dueRisk && this.dueRiskFor === mod)) return;
      if (!this.statsReady) return;                // 전원 로딩된 뒤에만
      this.dueRiskBusy = true; this.dueRiskFor = mod;
      const people = (this.curMod && this.curMod.people) || [];
      const over = [], soon = [];
      const nameOf = (pid) => (this.pstat[pid] && this.pstat[pid].name) || pid;
      try {
        await Promise.all(people.map(async (p) => {
          for (const bk of ["open", "inProgress"]) {
            let rows = [];
            try { rows = (await api.workloadBucket(p.id, bk)) || []; } catch (e) { rows = []; }
            rows.forEach((t) => {
              if (!t.due) return;
              if (this.excludeVoc && t.voc && !t.epic) return;   // 소속 Epic 없는 VoC 제외
              const d = this.dueRank(t);
              if (d < 0) over.push({ t, who: nameOf(p.id) });
              else if (d <= 3) soon.push({ t, who: nameOf(p.id) });
            });
          }
        }));
      } finally {
        // 임박순 정렬
        over.sort((a, b) => this.dueRank(a.t) - this.dueRank(b.t));
        soon.sort((a, b) => this.dueRank(a.t) - this.dueRank(b.t));
        if (this.dueRiskFor === mod) this.dueRisk = { over, soon };
        this.dueRiskBusy = false;
      }
    },
    _savePrefs() {
      try { localStorage.setItem("workload.opts", JSON.stringify({ metric: this.metric, sortBy: this.sortBy, mod: this.mod, grouping: this.grouping, excludeVoc: this.excludeVoc })); }
      catch (e) { /* 사파리 프라이빗 등 */ }
    },
    /** 'VoC 제외' 토글 — 막대·통계·마감리스크 모두 재산출(마감리스크는 필터가 바뀌므로 재로딩). */
    setExcludeVoc(on) {
      this.excludeVoc = on;
      this._savePrefs();
      this.dueRisk = null; this.dueRiskFor = "";
      this.$nextTick(() => this.loadDueRisk());
      this.scheduleMeasure();
    },
    // ── 막대 세그먼트: 'type'(티켓유형) / 'epic'(소속 Epic) 두 모드 ──
    // 왼쪽 '할당된 Ticket' 막대. type=Task/Sub-Task/VoC, epic=소속 Epic 별.
    assignedSegs(p) { return this.grouping === "epic" ? this.segAssignedEpic(p) : this.segAssigned(p); },
    // 오른쪽 '최근 7일 완료' 막대(metric 반영).
    doneSegs(p) { return this.grouping === "epic" ? this.segDoneEpic(p, this.metric) : this.seg(p.done7d, this.metric); },
    /** 그룹 키 → 표시 메타(이름·성격·색). Epic 은 시그니처 컬러(전 화면 공통). */
    groupMeta(key, names) {
      if (key === "__voc__") return { name: "사용자 VoC", kind: "voc", color: VOC_COLOR };
      if (key === "__none__") return { name: "Epic 없음", kind: "none", color: NONE_COLOR };
      return { name: (names && names[key]) || key, kind: "epic", color: categoryColor(key) };
    },
    /** 실제 Epic(건수 많은 순) 먼저, VoC·Epic 없음은 성격이 달라 항상 끝. (상세 epicDist 와 동일 규칙)
     *  'VoC 제외' 면 소속 Epic 없는 VoC 그룹을 통째로 뺀다(막대·통계 공통). */
    orderGroups(list) {
      const epics = list.filter((g) => g.kind === "epic").sort((a, b) => b.value - a.value);
      const voc = this.excludeVoc ? null : list.find((g) => g.kind === "voc");
      const none = list.find((g) => g.kind === "none");
      return epics.concat(voc ? [voc] : [], none ? [none] : []);
    },
    /** 왼쪽 막대(할당됨) — Epic 별. 세그 폭=진행중+할당됨 합, 오른쪽 '할당됨' 비율만 사선. */
    segAssignedEpic(p) {
      const ipE = (p.inProgress && p.inProgress.epics) || {};
      const opE = (p.open && p.open.epics) || {};
      const names = p.epicNames || {};
      const keys = new Set([...Object.keys(ipE), ...Object.keys(opE)]);
      let groups = [...keys].map((k) => {
        const ni = (ipE[k] && ipE[k].count) || 0, no = (opE[k] && opE[k].count) || 0;
        return Object.assign({ key: k, ni, no, value: ni + no }, this.groupMeta(k, names));
      }).filter((g) => g.value > 0);
      groups = this.orderGroups(groups);
      return groups.map((g) => ({
        value: g.value, color: g.color, hatchFrac: g.value > 0 ? g.no / g.value : 0,
        label: String(g.value),
        title: g.name + " 진행 중 " + g.ni + " · 할당됨 " + g.no + " (합 " + g.value + ")",
      }));
    },
    /** 오른쪽 막대(최근 7일 완료) — Epic 별. metric=count|hr. */
    segDoneEpic(p, metric) {
      const dE = (p.done7d && p.done7d.epics) || {};
      const names = p.epicNames || {};
      const u = metric === "hr" ? "h" : "건";
      let groups = Object.keys(dE).map((k) => {
        const v = metric === "hr" ? (dE[k].hr || 0) : (dE[k].count || 0);
        return Object.assign({ key: k, value: v }, this.groupMeta(k, names));
      }).filter((g) => g.value > 0);
      groups = this.orderGroups(groups);
      return groups.map((g) => ({ value: g.value, color: g.color, title: g.name + " " + g.value + u }));
    },
    /** 모듈 안에서 인력 정렬 — 이름 / 할당된 Ticket수 / 완료(완료 성과, 계산식에 따라 값이 달라짐).
     *  값 기준(할당·완료)은 **많은 순**. 아직 통계가 안 온 사람은 -1 로 맨 뒤(도착하면 제자리로). */
    sortedPeople(m) {
      const ppl = (m.people || []).slice();
      const nm = (p) => (this.pstat[p.id] && this.pstat[p.id].name) || p.name || p.id;
      if (this.sortBy === "name") return ppl.sort((a, b) => nm(a).localeCompare(nm(b), "ko"));
      const val = (p) => {
        const s = this.pstat[p.id];
        if (!s || s.error) return -1;
        return this.sortBy === "assigned" ? this.assignedCount(s) : this.barVal(s.done7d, this.metric);
      };
      return ppl.sort((a, b) => val(b) - val(a));
    },
    seg(bar, metric) {
      const u = metric === "hr" ? "h" : "건";
      const t = this.mv(bar, "task", metric), s = this.mv(bar, "subtask", metric), v = this.vocVal(bar, metric);
      return [
        { value: t, color: "var(--wl-task)", title: "Task " + t + u },
        { value: s, color: "var(--wl-subtask)", title: "Sub-Task " + s + u },
        { value: v, color: "var(--wl-voc)", title: "VoC " + v + u },
      ];
    },
    setBody(mod, el) { if (el) this.bodyRefs[mod] = el; },
    scheduleMeasure() { this.$nextTick(() => this.measureLines()); },
    measureLines() {
      if (!this.d) { this.linePos = {}; return; }
      const pos = {};
      for (const mod in this.bodyRefs) {
        const body = this.bodyRefs[mod];
        if (!body || !body.getBoundingClientRect || !body.isConnected) continue;
        if (!this.avgByMod[mod]) continue;      // 모듈 전원 로딩 전엔 평균선 안 그린다
        const prow = body.querySelector(".prow");
        const whead = body.querySelector(".whead");
        if (!prow || !whead) continue;
        const bars = prow.querySelectorAll(".pbar");   // [진행중, 7일완료]
        if (bars.length < 2) continue;
        const bRect = body.getBoundingClientRect();
        const wRect = whead.getBoundingClientRect();
        const avg = this.avgByMod[mod] || { ip: 0, dn: 0 };
        const xOf = (bar, av, scale) => {
          const r = bar.getBoundingClientRect();
          const ratio = scale > 0 ? Math.min(av / scale, 1) : 0;
          return (r.left - bRect.left) + ratio * r.width;
        };
        pos[mod] = {
          ipX: xOf(bars[0], avg.ip, this.scale.ip),
          doneX: xOf(bars[1], avg.dn, this.scale.dn),
          top: wRect.bottom - bRect.top,                       // 선 시작 y(헤더 아래)
          hy: (wRect.top + wRect.height / 2) - bRect.top,      // 수치 y(헤더 중앙)
        };
      }
      this.linePos = pos;
    },
    async toggleAct(id) {
      this.actOpen[id] = !this.actOpen[id];
      if (this.actOpen[id] && !this.tkd[id]) {
        // 세 리스트(할당됨/진행중/완료)를 **각각 병렬**로 받아 도착하는 대로 렌더한다.
        this.tkd[id] = { open: null, inProgress: null, done7d: null, err: {} };
        // ★ 반드시 this.tkd[id](= 리액티브 프록시)를 통해 쓴다.
        //   지역 변수(원본 객체)에 바로 쓰면 프록시의 set 트랩을 건너뛰어 **리렌더가 안 걸린다**.
        //   데이터는 들어와 있는데 화면은 '불러오는 중…' 인 채로 멈추고,
        //   접었다 펴서 리렌더가 일어나야 그제야 보였다.
        const box = this.tkd[id];
        const byDue = (a, b) => this.dueRank(a) - this.dueRank(b);
        const byResolved = (a, b) => (b.resolved || "").localeCompare(a.resolved || "");
        const load = (bucket, sorter) => api.workloadBucket(id, bucket)
          .then((rows) => { box[bucket] = (rows || []).slice().sort(sorter); })
          .catch((e) => { box[bucket] = []; box.err[bucket] = e.message; });
        load("inProgress", byDue);
        load("open", byDue);
        load("done7d", byResolved);
      }
    },
    dueRank(t) {
      // 남은 일수(D-day). 음수(초과)일수록 작아 상위. 마감 없으면 맨 뒤.
      if (!t.due) return Infinity;
      const due = new Date(t.due.substring(0, 10) + "T00:00:00");
      const today = new Date(); today.setHours(0, 0, 0, 0);
      return Math.round((due - today) / 86400000);
    },
    tk(key) { return tkt(key, this.d && this.d.jiraBase); },
    fy(s) { return ymd(s); },
    fdt(s) { return ymdhm(s); },
    dd(s) { return dday(s); },
    ddCls(iso) {
      // D-4 이상(여유) = 노랑(warn), D-3~D+(임박·초과) = 빨강(danger)
      const due = new Date(iso.substring(0, 10) + "T00:00:00");
      const today = new Date(); today.setHours(0, 0, 0, 0);
      const days = Math.round((due - today) / 86400000);
      return days >= 4 ? "warn" : "danger";
    },
  },
  template: `
  <div class="wl-view">
    <div v-if="err" class="err">워크로드 데이터를 불러오지 못했습니다: {{ err }}</div>
    <template v-else-if="d">
      <!-- ══ 컨트롤: 모듈 선택 (옵션은 개인별 워크로드 패널 안) ══ -->
      <div class="wl-ctl">
        <span class="wl-opt-l">모듈</span>
        <select class="wl-modsel" :value="mod" @change="selectModule($event.target.value)" aria-label="모듈 선택">
          <option v-for="m in d.modules" :key="m.module" :value="m.module">{{ m.module }} · 인력 {{ m.peopleCount }}</option>
        </select>
      </div>

      <!-- ══ Stat 타일 ══ -->
      <div class="wl-tiles">
        <div class="wl-tile"><div class="wl-tile-v">{{ totals.p }}</div><div class="wl-tile-l">인력</div></div>
        <div class="wl-tile"><div class="wl-tile-v">{{ totals.op }}</div><div class="wl-tile-l">할당됨</div></div>
        <div class="wl-tile"><div class="wl-tile-v">{{ totals.ip }}</div><div class="wl-tile-l">진행 중</div></div>
        <div class="wl-tile"><div class="wl-tile-v">{{ totals.dn }}</div><div class="wl-tile-l">최근 7일 완료</div></div>
        <div class="wl-tile" :class="{ warn: loadSkew && loadSkew.pct >= 40 }">
          <div class="wl-tile-v">{{ loadSkew ? loadSkew.pct + '%' : '—' }}</div><div class="wl-tile-l">부하 편중 · 상위1명</div></div>
        <div class="wl-tile" :class="{ warn: dueRisk && dueRisk.over.length }">
          <div class="wl-tile-v">{{ dueRisk ? dueRisk.over.length : '…' }}</div><div class="wl-tile-l">마감 초과</div></div>
      </div>

      <!-- ══ 패널 그리드 (그라파나풍) ══ -->
      <div class="wl-grid">
        <!-- 개인별 워크로드 (넓게) — 이 패널 전용 옵션 툴바 내장 -->
        <div class="wl-panel span8">
          <div class="wl-panel-h"><b>개인별 워크로드</b>
            <span v-if="grouping === 'epic'" class="wl-hlg">
              <span v-for="g in moduleEpicGroups.groups" :key="g.key" class="wl-hlg-i" :title="g.name + ' · ' + g.pct + '%'">
                <i :style="{ background: g.color }"></i>{{ g.name }}</span>
              <span v-if="!moduleEpicGroups.groups.length" class="mini muted">색 = 소속 Epic(시그니처)</span>
              <span class="mini muted">· 단색=진행중 사선=할당</span>
            </span>
            <span v-else class="wl-hlg">
              <span class="wl-hlg-i"><i class="sw task"></i>Task</span>
              <span class="wl-hlg-i"><i class="sw subtask"></i>Sub-Task</span>
              <span class="wl-hlg-i"><i class="sw voc"></i>VoC</span>
              <span class="mini muted">· 단색=진행중 사선=할당 세로선=평균</span>
            </span>
          </div>
          <!-- 개인별 워크로드 전용 옵션 -->
          <div class="wl-subopts">
            <div class="wl-opt"><span class="wl-opt-l">Task 구분</span><div class="fab-seg">
              <button :class="{ on: grouping === 'type' }" @click="setGrouping('type')">티켓유형</button>
              <button :class="{ on: grouping === 'epic' }" @click="setGrouping('epic')">소속 Epic</button></div></div>
            <div class="wl-opt"><span class="wl-opt-l">완료 성과</span><div class="fab-seg">
              <button :class="{ on: metric === 'count' }" @click="setMetric('count')">Task 수</button>
              <button :class="{ on: metric === 'hr' }" @click="setMetric('hr')">소요시간</button></div></div>
            <div class="wl-opt"><span class="wl-opt-l">정렬</span><div class="fab-seg">
              <button :class="{ on: sortBy === 'name' }" @click="setSort('name')">이름</button>
              <button :class="{ on: sortBy === 'assigned' }" @click="setSort('assigned')">할당</button>
              <button :class="{ on: sortBy === 'done' }" @click="setSort('done')">완료</button></div></div>
            <div class="wl-opt"><span class="wl-opt-l">VoC 포함</span>
              <button class="sm-switch" :class="{ on: !excludeVoc }" role="switch" :aria-checked="!excludeVoc"
                      @click="setExcludeVoc(!excludeVoc)" :title="excludeVoc ? '소속 Epic 없는 VoC 제외됨 — 눌러 포함' : '소속 Epic 없는 VoC 포함됨 — 눌러 제외'">
                <span class="sm-knob"></span></button>
            </div>
          </div>
          <div v-for="m in shownModules" :key="m.module" class="wl-panel-b wl-people" :ref="(el) => setBody(m.module, el)">
          <div v-if="!m.people || !m.people.length" class="empty">등록된 인력이 없습니다 (config/people.yaml)</div>
          <template v-else>
            <div class="whead">
              <div class="hl">인력</div>
              <div class="wbars"><div class="wside"><div class="hl">할당된 Ticket (Open + In-Progress)</div></div><div class="wside"><div class="hl">최근 7일 완료 ({{ doneUnit }})</div></div></div>
              <div></div>
            </div>
            <template v-if="linePos[m.module]">
              <div class="mavg-line" :style="{ left: linePos[m.module].ipX + 'px', top: linePos[m.module].top + 'px' }"></div>
              <div class="mavg-line" :style="{ left: linePos[m.module].doneX + 'px', top: linePos[m.module].top + 'px' }"></div>
              <div class="mavg-num" :style="{ left: linePos[m.module].ipX + 'px', top: linePos[m.module].hy + 'px' }">모듈 평균 {{ avgByMod[m.module].ip }}건</div>
              <div class="mavg-num" :style="{ left: linePos[m.module].doneX + 'px', top: linePos[m.module].hy + 'px' }">모듈 평균 {{ avgByMod[m.module].dn }}{{ doneUnit }}</div>
            </template>
            <template v-for="p in sortedPeople(m)" :key="p.id">
              <div class="prow">
                <span class="pname" :title="p.id"><Avatar :user="p.id" :name="(pstat[p.id] && pstat[p.id].name) || p.name" :size="20" /><b>{{ (pstat[p.id] && pstat[p.id].name) || p.name }}</b><span v-if="(pstat[p.id] && pstat[p.id].kind) || p.kind" class="kbadge" :class="(pstat[p.id] && pstat[p.id].kind) || p.kind">{{ ((pstat[p.id] && pstat[p.id].kind) || p.kind) === 'dev' ? '개발' : '운영' }}</span></span>
                <!-- 통계는 사람 by 사람으로 도착 — 아직이면 로딩, 실패면 재시도 안내, 오면 막대 -->
                <div v-if="!pstat[p.id]" class="wbars wl-pending"><span class="wl-pending-t">불러오는 중…</span></div>
                <div v-else-if="pstat[p.id].error" class="wbars wl-fail" title="이 인력의 집계 조회에 실패했습니다(0 이 아님). 새로고침으로 재시도하세요.">
                  <span class="wl-fail-t">집계 조회 실패 — 새로고침으로 재시도</span>
                </div>
                <div v-else class="wbars">
                  <ProgressBar class="wside" :segments="assignedSegs(pstat[p.id])" :scale="scale.ip" show-total dark-text />
                  <ProgressBar class="wside" :segments="doneSegs(pstat[p.id])" :scale="scale.dn" show-total dark-text />
                </div>
                <button class="plus" @click="toggleAct(p.id)">{{ actOpen[p.id] ? "−" : "+" }}</button>
              </div>
              <div v-if="actOpen[p.id]" class="act">
                <div v-if="!tkd[p.id]" class="loading">불러오는 중…</div>
                <div v-else-if="tkd[p.id].error" class="muted">불러오지 못했습니다: {{ tkd[p.id].error }}</div>
                <template v-else>
                  <!-- 세 버킷에서 찾은 티켓의 **소속 Epic 분포**. 이 사람이 지금 어느 Epic 에
                       매여 있는지가 목록보다 먼저 읽혀야 한다. -->
                  <div class="wepic">
                    <div class="wepic-h">Epic 분포
                      <b>{{ epicDist(p.id).total }}</b>
                      <span class="muted mini">할당됨·진행중·최근완료 합산</span>
                    </div>
                    <ProgressBar :segments="epicDist(p.id).segments" :height="18" show-total />
                    <div class="wepic-lg">
                      <span v-for="g in epicDist(p.id).groups" :key="g.key" class="wepic-i"
                            :class="{ voc: g.kind === 'voc', none: g.kind === 'none' }"
                            :title="g.name + ' · ' + g.value + '건'">
                        <i :style="{ background: g.color }"></i>{{ g.name }} <b>{{ g.value }}</b>
                      </span>
                      <span v-if="!epicDist(p.id).groups.length" class="muted mini">티켓 없음</span>
                    </div>
                  </div>

                  <!-- 할당됨 / 진행중 / 최근완료 — 상태 흐름 순서대로 3컬럼 -->
                  <div class="tcols3">
                    <div v-for="c in WL_COLS" :key="c.k" class="tcol" :class="'c-' + c.k">
                      <div class="sec-t">{{ c.label }}
                        <b>{{ (tkd[p.id][c.k] || []).length }}</b>
                      </div>
                      <div class="tcol-body">
                      <div v-if="tkd[p.id][c.k] === null" class="loading">불러오는 중…</div>
                      <template v-else>
                        <div v-for="t in tkd[p.id][c.k]" :key="c.k + '-' + t.key" class="wtk tkt"
                             :class="c.cls" :data-key="t.key" role="button" tabindex="0"
                             :title="t.key + ' · ' + t.summary">
                          <TypeBadge :type="t.type" /><span class="ky">{{ t.key }}</span>
                          <span class="sm">{{ t.summary }}</span>
                          <span class="sched">
                            <span v-if="t.epic" class="ebadge" :style="{ '--sig': epicColorOf(p.id, t) }"
                                  :title="'Epic: ' + (t.epicName || t.epic)">{{ t.epicName || t.epic }}</span>
                            <span v-else-if="t.voc" class="ebadge voc">사용자 VoC</span>
                            <span v-else class="ebadge none">Epic 없음</span>
                            <span v-if="c.k === 'done7d'" class="dbadge fin"><svg class="ic" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.6" stroke-linecap="round" stroke-linejoin="round"><path d="M4 12.5l5 5 11-11"/></svg>{{ fdt(t.resolved) }}</span>
                            <template v-else>
                              <span v-if="t.due" class="dchip" :class="ddCls(t.due)"><svg class="ic" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.4" stroke-linecap="round"><circle cx="12" cy="12" r="9"/><path d="M12 7.5V12l3 1.8"/></svg>{{ dd(t.due) }}</span>
                              <span v-else class="dbadge nodue">마감 없음</span>
                            </template>
                          </span>
                        </div>
                        <div v-if="!(tkd[p.id][c.k] || []).length" class="muted mini">없음</div>
                      </template>
                      </div>
                    </div>
                  </div>
                </template>
              </div>
            </template>
          </template>
        </div>
        </div><!-- /wl-panel 개인별 워크로드 -->

        <!-- ① 모듈이 기여하는 Epic (개인별 워크로드 오른쪽) -->
        <div class="wl-panel span4">
          <div class="wl-panel-h"><b>모듈이 기여하는 Epic</b> <span class="mini muted">할당+진행+완료 · {{ doneUnit }}</span></div>
          <div class="wl-panel-b">
            <div v-if="!statsReady" class="muted mini">집계 중… ({{ moduleAgg(curMod).loaded }}/{{ curMod.peopleCount }})</div>
            <template v-else>
              <ProgressBar :segments="moduleEpicSegs()" :height="18" show-total />
              <div class="wl-epic-lg">
                <span v-for="g in moduleEpicGroups.groups" :key="g.key" class="wl-epic-i"
                      :class="{ voc: g.kind === 'voc', none: g.kind === 'none' }" :title="g.name + ' · ' + g.value + doneUnit">
                  <i :style="{ background: g.color }"></i>{{ g.name }} <b>{{ g.pct }}%</b></span>
                <span v-if="!moduleEpicGroups.groups.length" class="muted mini">집계할 작업이 없습니다.</span>
              </div>
            </template>
          </div>
        </div>

        <!-- 좌측 세로 스택: 버스팩터 · 인력별 Epic 분산 · 마감 리스크 -->
        <div class="wl-col span4">
          <!-- 버스팩터 -->
          <div class="wl-panel">
            <div class="wl-panel-h"><b>버스팩터</b> <span class="mini muted">참여 1명 Epic</span></div>
            <div class="wl-panel-b">
              <div v-if="!statsReady" class="muted mini">집계 중…</div>
              <template v-else>
                <div v-if="busFactor.length" class="wl-mon-list">
                  <span v-for="b in busFactor" :key="b.epic.key" class="wl-mon-row warn"><i :style="{ background: b.epic.color }"></i>{{ b.epic.name }} — {{ b.person.name }}</span>
                </div>
                <div v-else class="mini ok">단독 참여 Epic 없음 ✓</div>
              </template>
            </div>
          </div>
          <!-- 인력별 Epic 분산 -->
          <div class="wl-panel">
            <div class="wl-panel-h"><b>인력별 Epic 분산</b> <span class="mini muted">≥4 과다</span></div>
            <div class="wl-panel-b">
              <div v-if="!statsReady" class="muted mini">집계 중…</div>
              <div v-else class="wl-mon-list">
                <span v-for="e in epicSpread" :key="e.id" class="wl-mon-row" :class="{ warn: e.count >= 4 }"><Avatar :user="e.id" :name="e.name" :size="14" />{{ e.name }} <b :class="{ voc: e.voc }">{{ e.label }}</b></span>
                <span v-if="!epicSpread.length" class="muted mini">데이터 없음</span>
              </div>
            </div>
          </div>
          <!-- 마감 리스크 -->
          <div class="wl-panel">
            <div class="wl-panel-h"><b>마감 리스크</b> <span class="mini muted">초과 · 임박(D-3)</span></div>
            <div class="wl-panel-b">
              <div v-if="dueRiskBusy && !dueRisk" class="muted mini"><span class="spinner"></span> 불러오는 중…</div>
              <template v-else-if="dueRisk">
                <div class="wl-mon-big" :class="{ warn: dueRisk.over.length }">초과 <b>{{ dueRisk.over.length }}</b> · 임박 <b>{{ dueRisk.soon.length }}</b></div>
                <div class="wl-mon-list">
                  <span v-for="(x, i) in dueRisk.over.slice(0, 5)" :key="'o' + i" class="wl-mon-row warn tkt" :data-key="x.t.key" role="button">{{ x.t.key }} · {{ x.who }} <b>{{ dd(x.t.due) }}</b></span>
                  <span v-for="(x, i) in dueRisk.soon.slice(0, 3)" :key="'s' + i" class="wl-mon-row tkt" :data-key="x.t.key" role="button">{{ x.t.key }} · {{ x.who }} <b>{{ dd(x.t.due) }}</b></span>
                  <span v-if="!dueRisk.over.length && !dueRisk.soon.length" class="mini ok">마감 위험 없음 ✓</span>
                </div>
              </template>
              <div v-else class="muted mini">—</div>
            </div>
          </div>
        </div>

        <!-- ② Epic별 인력 지분 (넓게, 오른쪽) -->
        <div class="wl-panel span8">
          <div class="wl-panel-h"><b>Epic별 인력 지분</b> <span class="mini muted">누가 얼마나 (상위 8)</span></div>
          <div class="wl-panel-b">
            <div v-if="!statsReady" class="muted mini">집계 중…</div>
            <template v-else>
              <div v-for="r in epicPeopleRows" :key="r.epic.key" class="wl-epr">
                <div class="wl-epr-h">
                  <span class="wl-epr-badge" :style="{ '--ec': r.epic.color }">{{ r.epic.name }}</span>
                  <span v-if="r.single" class="wl-warn-chip" title="참여 인력 1명 — 지식 집중(버스팩터) 리스크">⚠ 단독</span>
                </div>
                <ProgressBar :segments="r.segs" :height="16" />
                <div class="wl-epr-lg">
                  <span v-for="s in r.segs" :key="s.pid" class="wl-epr-p" :title="s.title">
                    <Avatar :user="s.pid" :name="s.name" :size="14" />{{ s.name }} <b>{{ s.pct }}%</b></span>
                </div>
              </div>
              <div v-if="!epicPeopleRows.length" class="muted mini">집계할 Epic 이 없습니다.</div>
            </template>
          </div>
        </div>
      </div><!-- /wl-grid -->
    </template>
    <div v-else class="loading page">불러오는 중…</div>
  </div>`,
};
