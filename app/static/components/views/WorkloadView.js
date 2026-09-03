// WorkloadView.js — 기능3 인력 워크로드. 두 막대(진행 중 / 최근 완료 1·2·4주), Task성·VoC성 색 구분,
// 모듈 평균 = 막대 뒤 세로 가이드선 + 헤더행 평균수치. [+] 확장 = 진행중/완료 티켓 리스트
//   (Due·D-day, 완료일시; 진행중=임박순·완료=최근순 정렬).
// 인력 = 본명(displayName 첫 어절) + 개발/운영 뱃지(id 사번 x+숫자/i+숫자). updated: 2026-07-09
import { api } from "../../lib/api.js";
import { vocBadgeSegs, vocStripTitle } from "../../lib/voc.js";
import { moduleColor, categoryColor, sigColor } from "../../lib/colors.js";
import { ymd, ymdhm, tkt, dday } from "../../lib/fmt.js";
import { pushToast } from "../../lib/toast.js";
import {
  WORKLOAD_PERSON_RETRY_DELAYS,
  WorkloadRequestScheduler,
  bucketState,
  fetchWorkloadBucketRows,
  summarizeDueRiskParts,
  workloadErrorKind,
} from "../workload/recovery.js";
import ProgressBar from "../ui/ProgressBar.js";
import TypeBadge from "../ui/TypeBadge.js";
import Avatar from "../ui/Avatar.js";

// 상세 3컬럼 — 상태 흐름 순서(할당 → 진행 → 완료). 세 버킷 모두 같은 행 컴포넌트를 쓴다.
// '최근 완료' 로 볼 기간(일) — 백엔드 JiraClient.WL_DONE_DAYS 와 같아야 한다.
const DONE_DAYS = [7, 14, 28];
const ASSIGNED_WINDOWS = [
  { k: "1w", label: "1주", hint: "최근 1주 안에 갱신된 Open·In Progress Task" },
  { k: "1m", label: "1달", hint: "최근 1달 안에 갱신된 Open·In Progress Task" },
  { k: "all", label: "전체", hint: "갱신 시점과 무관한 모든 Open·In Progress Task" },
];
const WL_COLS = [
  { k: "open", label: "할당됨", cls: "todo" },
  { k: "inProgress", label: "진행 중", cls: "" },
  { k: "done7d", label: "최근 완료", cls: "done" },   // 실제 라벨은 doneLabel(기간 포함)
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
             // '최근 완료' 로 볼 기간(일). 주 단위로 일하는 팀이 많아 1·2·4주로 고른다.
             doneDays: [7, 14, 28].includes(pref.doneDays) ? pref.doneDays : 7,
             // 할당된 Ticket(Open+In-Progress)의 updated 기간. 누락 없는 '전체'가 기본이다.
             assignedWindow: ["1w", "1m", "all"].includes(pref.assignedWindow) ? pref.assignedWindow : "all",
             dueRisk: null, dueRiskBusy: false, dueRiskFor: "",
             pstat: {}, busy: false, peopleLoadEpoch: 0 };   // pstat[pid] = 그 인력의 통계 행(사람 by 사람 로딩)
  },
  created() {
    this.bodyRefs = {};                // 비반응 DOM 참조(모듈 body)
    this.peopleRetryTimers = new Set();
    this.workScheduler = new WorkloadRequestScheduler();
    this.workNoticeEpoch = 0;
    this.workNotices = new Set();
    this.detailLoadEpoch = 0;
    this.dueRiskEpoch = 0;
    // 좌하단 플로팅 새로고침 — 뷰마다 캐시 비우고 다시 받는 함수 이름이 달라 여기서 잇는다.
    window.addEventListener("force-refresh", this._fr = async () => {
      try { await this.hardRefresh(); }
      finally { window.dispatchEvent(new CustomEvent("force-refresh-done")); }
    });
    // 재인증(auth-ok) 후 — 세션 끊긴 채 실패했던 조회를 가볍게 다시 받는다(서버 캐시는 안 비움).
    window.addEventListener("auth-ok", this._authok = () => {
      if (!this.d) this.load();
      else {
        // 성공한 사람·컬럼·마감 집계는 그대로 두고 인증 때문에 멈춘 조각만 다시 받는다.
        this.retryFailedPeople("auth");
        this.retryFailedBuckets("auth");
        this.retryDueRisk("auth");
      }
    });
  },
  async mounted() {
    await this.load();
    this._onResize = () => this.scheduleMeasure();
    window.addEventListener("resize", this._onResize);
  },
  unmounted() {
    this._cancelPeopleRetryTimers();
    this.peopleLoadEpoch++;
    this.detailLoadEpoch++;
    this.dueRiskEpoch++;
    if (this._onResize) window.removeEventListener("resize", this._onResize);
    window.removeEventListener("force-refresh", this._fr);
    window.removeEventListener("auth-ok", this._authok);
  },
  activated() { this.scheduleMeasure(); },   // keep-alive 재활성 시 평균선 재측정
  computed: {
    WL_COLS() { return WL_COLS; },
    DONE_DAYS() { return DONE_DAYS; },
    ASSIGNED_WINDOWS() { return ASSIGNED_WINDOWS; },
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
      return (m.people || []).map((p) => this.pstat[p.id])
        .filter((s) => s && !s.error && !s.loading && !s.retrying);
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
    /** '최근 N주 완료' — 기간 선택(doneDays)을 라벨에 그대로 반영한다. */
    doneLabel() { return "최근 " + (this.doneDays / 7) + "주 완료"; },
    assignedLabel() {
      const option = ASSIGNED_WINDOWS.find((item) => item.k === this.assignedWindow);
      return option && option.k !== "all" ? "최근 " + option.label + " 갱신" : "전체";
    },
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
      if (m && this.moduleDataComplete(m)) {
        const ss = (m.people || []).map((p) => this.pstat[p.id]).filter((s) => s && !s.error);
        out[m.module] = { ip: avg(ss.map((s) => this.assignedCount(s))),
                          dn: avg(ss.map((s) => this.barVal(s.done7d, this.metric))) };
      }
      return out;
    },
    // ── 모듈 통계(하단 섹션) — 이미 로딩된 인력 번들을 프론트에서 집계한다(추가 조회 없음). ──
    statsReady() { return !!(this.curMod && this.moduleComplete(this.curMod)); },
    // 에러까지 모두 끝난 상태(statsReady)와 전체 데이터가 실제로 준비된 상태를 구분한다.
    // 부분 실패 중에는 평균/정상-empty 문구를 확정값처럼 표시하지 않는다.
    statsComplete() { return !!(this.curMod && this.moduleDataComplete(this.curMod)); },
    /** 현재 모듈의 Epic 집계: 진행중+최근완료를 Epic별로(metric 반영) + Epic별 인력 분해. */
    moduleEpicAgg() {
      const people = this.curStats;
      const names = {}, agg = {}, byPerson = {};
      people.forEach((s) => Object.assign(names, s.epicNames || {}));
      // 기여도·지분은 **항상 티켓 수**. (할당+진행+완료 기준이라 소요시간은 무의미 — '완료 성과'
      //  토글은 개인별 워크로드 막대에만 영향, 이 통계엔 영향 없음.)
      const val = (e) => (e.count || 0);
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
    _notifyWorkloadError(kind, final = false) {
      if (kind === "permission") return;       // Jira 권한 제외는 best-effort로 조용히 건너뛴다
      if (kind === "auth") window.dispatchEvent(new CustomEvent("need-login"));
      const noticeKey = this.workNoticeEpoch + ":" + kind + ":" + (final ? "final" : "retry");
      if (this.workNotices.has(noticeKey)) return;
      this.workNotices.add(noticeKey);
      pushToast({
        kind: "error", key: "workload-partial-" + noticeKey,
        title: kind === "auth"
          ? "일부 워크로드를 인증 문제로 불러오지 못했습니다"
          : (final ? "일부 워크로드를 계속 불러오지 못했습니다"
                   : "일부 워크로드를 불러오지 못해 재시도합니다"),
        message: kind === "auth"
          ? "불러온 항목은 유지하고 인증 복구 후 실패분만 다시 받습니다."
          : "성공한 사람과 목록은 유지하고 실패한 항목만 다시 받습니다.",
        timeout: 7000,
      });
    },
    _beginNoticeEpoch() {
      this.workNoticeEpoch++;
      // 장기 상주 앱에서 과거 필터의 notice key를 끝없이 들고 있지 않는다.
      if (this.workNotices.size > 40) this.workNotices.clear();
    },
    _invalidateDueRisk() {
      this.dueRiskEpoch++;
      this.dueRisk = null;
      this.dueRiskBusy = false;
      this.dueRiskFor = "";
    },
    /** 버킷 한 조각의 공통 재시도. 상세·마감 리스크 모두 이 함수를 쓰므로 실패한 조각만
     *  0.8/2.4/5초 간격으로 재시도하고, 동일 요청은 위 스케줄러에서 합쳐진다. */
    _fetchBucketRows(id, bucket, doneDays, assignedWindow, opts = {}) {
      return fetchWorkloadBucketRows(Object.assign({}, opts, {
        id, bucket, doneDays, assignedWindow,
        scheduler: this.workScheduler,
        request: () => api.workloadBucket(id, bucket, doneDays, assignedWindow),
        onFailure: (kind, final) => this._notifyWorkloadError(kind, final),
      }));
    },
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
      const pids = (m.people || []).map((p) => p.id).filter((id) => {
        const state = this.pstat[id];
        return !state || (state.error && !state.retrying);
      });
      if (pids.length) this._loadPeople(pids);
    },
    /** 하단 메뉴에서 모듈 전환 — 그 모듈 인력만 로딩하고, 평균선 참조를 초기화한다. */
    selectModule(mod) {
      if (mod === this.mod) return;
      this._cancelPeopleRetryTimers();
      this.peopleLoadEpoch++;          // 이전 모듈의 완료 응답은 API 캐시만 채우고 현재 행엔 쓰지 않는다
      this._beginNoticeEpoch();
      this.mod = mod;
      this.bodyRefs = {};            // 이전 모듈 body 참조 폐기(측정 대상은 현재 모듈뿐)
      this.linePos = {};
      this._invalidateDueRisk();       // 마감 리스크는 모듈별 — 초기화
      this._savePrefs();
      this.loadModulePeople(mod);
      this.scheduleMeasure();
      this.$nextTick(() => this.loadDueRisk());
    },
    /** 사람 by 사람 통계 로딩 — **동시 요청 상한(CONC)**을 둔다. 한꺼번에 다 쏘면 서버(로컬 fake·
     *  prod 단일 SSO 큐)를 덮쳐 조회가 통째로 실패한다(각자 3개 검색이라 18명이면 54개 동시).
     *  하나 끝날 때마다 다음을 채워, 화면은 사람 순으로 채워지되 서버는 안 붐빈다. */
    _cancelPeopleRetryTimers() {
      for (const timer of (this.peopleRetryTimers || [])) clearTimeout(timer);
      if (this.peopleRetryTimers) this.peopleRetryTimers.clear();
    },
    /** 현재 선택 모듈에서 실패가 확정된 사람만 다시 받는다. 성공 데이터와 상세 펼침은 건드리지 않는다. */
    retryFailedPeople(kind = null) {
      const m = this.curMod;
      if (!m) return;
      const pids = (m.people || []).map((p) => p.id)
        .filter((id) => this.pstat[id]
          && (this.pstat[id].error || (this.pstat[id].partial && this.pstat[id].retryable))
          && !this.pstat[id].retrying && !this.pstat[id].partialRetrying
          && this.pstat[id].errorKind !== "permission"
          && (!kind || this.pstat[id].errorKind === kind));
      if (pids.length) this._loadPeople(pids);
    },
    retryPerson(pid) {
      const state = this.pstat[pid];
      if (!state || !(state.error || (state.partial && state.retryable))
          || state.retrying || state.partialRetrying || state.loading
          || state.errorKind === "permission") return;
      this._loadPeople([pid]);
    },
    _loadPeople(pids) {
      const CONC = 3;
      const epoch = this.peopleLoadEpoch;
      const doneDays = this.doneDays;
      const assignedWindow = this.assignedWindow;
      const queue = [...new Set(pids)].map((pid) => ({ pid, attempt: 0, readyAt: 0 }));
      let active = 0, wakeTimer = null, authPaused = false;
      queue.forEach(({ pid }) => {
        if (!(this.pstat[pid] && this.pstat[pid].partial && !this.pstat[pid].error)) {
          this.pstat[pid] = { id: pid, loading: true, retryAttempt: 0 };
        }
      });
      const clearWake = () => {
        if (!wakeTimer) return;
        clearTimeout(wakeTimer); this.peopleRetryTimers.delete(wakeTimer); wakeTimer = null;
      };
      const wakeLater = () => {
        clearWake();
        if (epoch !== this.peopleLoadEpoch || !queue.length) return;
        const wait = Math.max(0, Math.min(...queue.map((task) => task.readyAt)) - Date.now());
        wakeTimer = setTimeout(() => {
          this.peopleRetryTimers.delete(wakeTimer); wakeTimer = null; pump();
        }, wait);
        this.peopleRetryTimers.add(wakeTimer);
      };
      const pump = () => {
        if (epoch !== this.peopleLoadEpoch || authPaused) return clearWake();
        clearWake();
        let now = Date.now();
        while (active < CONC) {
          const index = queue.findIndex((task) => task.readyAt <= now);
          if (index < 0) break;
          const task = queue.splice(index, 1)[0];
          const pid = task.pid;
          active++;
          if (this.pstat[pid] && this.pstat[pid].partial && !this.pstat[pid].error) {
            this.pstat[pid] = Object.assign({}, this.pstat[pid], {
              retrying: false, partialRetrying: task.attempt > 0, retryAttempt: task.attempt,
            });
          } else {
            this.pstat[pid] = { id: pid, loading: true, retrying: task.attempt > 0,
                                retryAttempt: task.attempt };
          }
        this.workScheduler.schedule(
          ["person", pid, doneDays, assignedWindow].join(":"),
          () => api.workloadPerson(pid, doneDays, assignedWindow), 20, epoch)
          .then((r) => {
            if (epoch !== this.peopleLoadEpoch) return;
            // 이 API는 과거 호환 때문에 일시 조회 실패를 HTTP 200 + {error:true}로 줄 수 있다.
            // 성공 Promise라고 막대 0건으로 확정하지 말고 아래 재시도 경로로 보낸다.
            if (r && r.error) {
              const incomplete = new Error(r.message || r.detail
                || (typeof r.error === "string" ? r.error : "워크로드 집계 응답이 불완전합니다."));
              incomplete.payload = r;
              incomplete.status = r.status;
              incomplete.errorKind = r.errorKind;
              incomplete.needLogin = !!r.needLogin;
              throw incomplete;
            }
            if (r && r.partial && r.retryable) {
              const nextAttempt = task.attempt + 1;
              this.pstat[pid] = Object.assign({}, r, {
                retrying: false,
                partialRetrying: task.attempt < WORKLOAD_PERSON_RETRY_DELAYS.length
                  && r.errorKind !== "auth",
                retryAttempt: nextAttempt,
              });
              this._invalidateDueRisk();
              if (r.errorKind === "auth") {
                this._notifyWorkloadError("auth", true);
              } else if (task.attempt < WORKLOAD_PERSON_RETRY_DELAYS.length) {
                this._notifyWorkloadError(r.errorKind || "other", false);
                queue.push({ pid, attempt: nextAttempt,
                             readyAt: Date.now() + WORKLOAD_PERSON_RETRY_DELAYS[task.attempt] });
              } else {
                this._notifyWorkloadError(r.errorKind || "other", true);
              }
              return;
            }
            this.pstat[pid] = r;
            this._invalidateDueRisk();
          })
          .catch((e) => {
            if (epoch !== this.peopleLoadEpoch) return;
            const kind = workloadErrorKind(e);
            if (kind === "permission") {
              this.pstat[pid] = { id: pid, error: true, errorKind: kind,
                                  message: (e && e.message) || String(e) };
            } else if (kind === "auth") {
              // One confirmed session loss applies to this whole request generation. Do not send
              // every still-queued person through the same dead SSO session; retain successful
              // rows and mark only pending rows for auth-ok targeted retry.
              authPaused = true;
              clearWake();
              for (const pending of queue.splice(0)) {
                this.pstat[pending.pid] = { id: pending.pid, error: true, errorKind: "auth",
                  message: "인증 복구 대기 중" };
              }
              this._notifyWorkloadError(kind, true);
              this.pstat[pid] = { id: pid, error: true, errorKind: kind,
                                  message: (e && e.message) || String(e) };
            } else if (task.attempt < WORKLOAD_PERSON_RETRY_DELAYS.length) {
              const nextAttempt = task.attempt + 1;
              // 자동 재시도를 모두 소진하기 전에는 실패로 확정하지 않는다. 최초 로딩과 같은
              // 자리표시를 유지하고 시도 횟수만 알려, 성공할 수 있는 행을 빨갛게 경고하지 않는다.
              this.pstat[pid] = { id: pid, loading: true, retrying: true,
                retryAttempt: nextAttempt,
                message: (e && e.message) || String(e) };
              this._notifyWorkloadError(kind, false);
              queue.push({ pid, attempt: nextAttempt,
                           readyAt: Date.now() + WORKLOAD_PERSON_RETRY_DELAYS[task.attempt] });
            } else {
              this._notifyWorkloadError(kind, true);
              this.pstat[pid] = { id: pid, error: true, errorKind: kind,
                                  message: (e && e.message) || String(e) };
            }
          })
          .finally(() => {
            active--;
            if (epoch !== this.peopleLoadEpoch) return;
            this.scheduleMeasure(); this.loadDueRisk();
            if (!authPaused) pump();
          });
          now = Date.now();
        }
        if (active < CONC && queue.length) wakeLater();
      };
      pump();
    },
    /** 캐시를 비우고 전부 다시 받는다 — 낡은 값으로 화면을 지키는 구조라 사람이 끊을 수단이 필요하다. */
    async hardRefresh() {
      if (this.busy) return;
      this.busy = true;
      try {
        await api.refresh();
        this._cancelPeopleRetryTimers();
        this.peopleLoadEpoch++;
        this._beginNoticeEpoch();
        this._invalidateDueRisk();
        this.d = null; this.pstat = {}; this.tkd = {}; this.actOpen = {}; this.linePos = {};
        await this.load();
      } catch (e) {
        this.err = (e && e.message) || "다시 받지 못했습니다.";
      } finally { this.busy = false; }
    },
    /** 이 모듈 인력이 **전원** 로딩됐는가(성공/실패 불문 — 도착했으면 됨). 평균선/헤더 합계 게이트. */
    moduleComplete(m) {
      const ppl = m.people || [];
      return ppl.length > 0 && ppl.every((p) => {
        const state = this.pstat[p.id];
        return state && !state.loading && !state.retrying;
      });
    },
    moduleDataComplete(m) {
      const ppl = m.people || [];
      return ppl.length > 0 && ppl.every((p) => {
        const state = this.pstat[p.id];
        return state && !state.error && !state.loading && !state.retrying;
      });
    },
    /** 모듈 헤더 합계 — 로딩된 인력까지 누적(부분 진행도 보여준다). loaded/total 로 진행 표시. */
    moduleAgg(m) {
      const t = { ip: 0, op: 0, dn: 0, loaded: 0 };
      (m.people || []).forEach((p) => {
        const s = this.pstat[p.id];
        if (!s) return;
        if (s.loading || s.retrying) return;
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
    /** 마감 리스크 행의 소속 Epic 뱃지 — {라벨, 색}. Epic→시그니처색, VoC/Epic없음은 고정색. */
    riskEpic(t) {
      if (t.epic) return { label: t.epicName || t.epic, color: categoryColor(t.epic) };
      if (t.voc) return { label: "사용자 VoC", color: VOC_COLOR };
      return { label: "Epic 없음", color: NONE_COLOR };
    },
    mcolor(i) { return moduleColor(i); },
    // 사용자 VoC 제목 접두 [대분류 - 소분류] → 뱃지 세그먼트 / 제목에서 접두 제거(voc.js — Task 화면과 동일 규칙)
    vocSegs(title) { return vocBadgeSegs(title); },
    vocStrip(t) { return (t.voc && !t.epic) ? vocStripTitle(t.summary) : t.summary; },
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
          label: String(c), name: lb,         // 공간 넉넉하면 분류(Task/Sub-Task/VoC) 표시
          title: lb + " 진행 중 " + ni + " · 할당됨 " + no + " (합 " + c + ")",
        });
      });
      return segs;
    },
    _ensureDetailBox(id) {
      if (!this.tkd[id]) {
        this.tkd[id] = {
          open: null, inProgress: null, done7d: null,
          states: {
            open: bucketState(), inProgress: bucketState(), done7d: bucketState(),
          },
        };
      }
      const box = this.tkd[id];
      if (!box.states) box.states = {};
      for (const bucket of ["open", "inProgress", "done7d"]) {
        if (!box.states[bucket]) box.states[bucket] = bucketState();
      }
      return box;
    },
    bucketStateOf(id, bucket) {
      const box = this.tkd[id];
      return (box && box.states && box.states[bucket]) || bucketState();
    },
    detailComplete(id) {
      const box = this.tkd[id];
      return !!(box && ["open", "inProgress", "done7d"].every(
        (bucket) => this.bucketStateOf(id, bucket).status === "success"));
    },
    _detailSorter(bucket) {
      if (bucket === "done7d") {
        return (a, b) => (b.resolved || "").localeCompare(a.resolved || "");
      }
      return (a, b) => this.dueRank(a) - this.dueRank(b);
    },
    async _loadDetailBucket(id, bucket, opts = {}) {
      const box = this._ensureDetailBox(id);
      const doneDays = opts.doneDays === undefined ? this.doneDays : opts.doneDays;
      const assignedWindow = opts.assignedWindow || this.assignedWindow;
      const requestKey = [id, bucket, doneDays, assignedWindow].join("|");
      const viewEpoch = this.detailLoadEpoch;
      const prior = this.bucketStateOf(id, bucket);
      if ((prior.status === "loading" || prior.status === "retrying")
          && prior.requestKey === requestKey) return;
      if (opts.resetRows !== false) box[bucket] = null;
      box.states[bucket] = bucketState("loading", { requestKey });
      const isCurrent = () => viewEpoch === this.detailLoadEpoch && this.tkd[id] === box
        && this.bucketStateOf(id, bucket).requestKey === requestKey;
      const result = await this._fetchBucketRows(id, bucket, doneDays, assignedWindow, {
        priority: opts.priority === undefined ? 30 : opts.priority,
        freshness: this.peopleLoadEpoch,
        isCurrent,
        onRetry: (attempt, error) => {
          if (!isCurrent()) return;
          box.states[bucket] = bucketState("retrying", {
            requestKey, attempt, kind: "other",
            message: (error && error.message) || String(error),
          });
        },
        onPartial: (rows, attempt, error) => {
          if (!isCurrent()) return;
          box[bucket] = rows.slice().sort(this._detailSorter(bucket));
          box.states[bucket] = bucketState("partial", {
            requestKey, attempt, kind: workloadErrorKind(error),
            message: (error && error.message) || String(error),
          });
        },
      });
      if (!isCurrent() || result.status === "cancelled") return;
      if (result.status === "success") {
        box[bucket] = result.rows.slice().sort(this._detailSorter(bucket));
        box.states[bucket] = bucketState("success", { requestKey });
        return;
      }
      box.states[bucket] = bucketState(result.status, {
        requestKey, kind: result.kind || "other",
        message: (result.error && result.error.message) || "불러오지 못했습니다.",
      });
    },
    retryBucket(id, bucket) {
      const state = this.bucketStateOf(id, bucket);
      if (!["error", "partial"].includes(state.status) || state.kind === "permission") return;
      this._loadDetailBucket(id, bucket, { resetRows: false, priority: 40 });
    },
    retryFailedBuckets(kind = null) {
      for (const id of Object.keys(this.tkd)) {
        for (const bucket of ["open", "inProgress", "done7d"]) {
          const state = this.bucketStateOf(id, bucket);
          if (["error", "partial"].includes(state.status) && state.kind !== "permission"
              && (!kind || state.kind === kind)) this.retryBucket(id, bucket);
        }
      }
    },
    setMetric(mk) { this.metric = mk; this._savePrefs(); this.scheduleMeasure(); },
    /** '최근 완료' 기간 변경 — 서버 질의 조건이 바뀌므로 통계·완료 목록을 다시 받는다. */
    setDoneDays(d) {
      if (this.doneDays === d) return;
      this.doneDays = d; this._savePrefs();
      this._cancelPeopleRetryTimers();
      this.peopleLoadEpoch++;
      this._beginNoticeEpoch();
      this.pstat = {}; this.linePos = {};           // 받아 둔 카운트·평균선은 다른 질문의 답이다
      if (this.mod) this.loadModulePeople(this.mod);
      // 이미 펼쳐 둔 상세는 **완료 칸만** 다시 받는다. tkd 를 통째로 비우면 펼친 채로
      // '불러오는 중…' 에서 멈춘다(목록은 toggleAct 로만 채워진다).
      Object.keys(this.tkd).forEach((id) => {
        this._loadDetailBucket(id, "done7d", {
          doneDays: d, assignedWindow: this.assignedWindow, resetRows: true,
        });
      });
      this.scheduleMeasure();
    },
    /** 할당 갱신기간 변경 — Open·In-Progress 통계와 펼친 두 목록만 다시 투영한다. */
    setAssignedWindow(window) {
      if (!ASSIGNED_WINDOWS.some((item) => item.k === window) || this.assignedWindow === window) return;
      this.assignedWindow = window; this._savePrefs();
      this._cancelPeopleRetryTimers();
      this.peopleLoadEpoch++;
      this._beginNoticeEpoch();
      this.pstat = {}; this.linePos = {};
      this._invalidateDueRisk();
      if (this.mod) this.loadModulePeople(this.mod);
      Object.keys(this.tkd).forEach((id) => {
        ["open", "inProgress"].forEach((bucket) => {
          this._loadDetailBucket(id, bucket, {
            doneDays: this.doneDays, assignedWindow: window, resetRows: true,
          });
        });
      });
      this.scheduleMeasure();
    },
    setSort(k) { this.sortBy = k; this._savePrefs(); },
    setGrouping(g) { this.grouping = g; this._savePrefs(); this.scheduleMeasure(); },
    /** ① 모듈→Epic 스택 막대 세그먼트. */
    moduleEpicSegs() {
      return this.moduleEpicGroups.groups.map((g) => ({
        value: g.value, color: g.color, title: g.name + " " + g.value + "건 (" + g.pct + "%)",   // 항상 티켓 수
      }));
    },
    /** 마감 리스크 — 현재 모듈 인력의 할당/진행중 티켓에서 초과(D+)·임박(D-3) 집계(지연 로딩). */
    async loadDueRisk(opts = {}) {
      const mod = this.curMod ? this.curMod.module : "";
      const assignedWindow = this.assignedWindow;
      const excludeVoc = this.excludeVoc;
      const requestKey = mod + "|" + assignedWindow + "|" + (excludeVoc ? "no-voc" : "voc");
      const sameResult = this.dueRisk && this.dueRiskFor === requestKey ? this.dueRisk : null;
      if (this.dueRiskBusy && this.dueRiskFor === requestKey) return;
      if (sameResult && !opts.retryFailed) return;
      if (!this.statsReady) return;                // 전원 로딩된 뒤에만
      // 세션이 끊겨 사람 집계부터 실패한 동안에는 2×인력 버킷을 더 쌓지 않는다. auth-ok가 그
      // 사람만 복구한 뒤 마지막 finally에서 다시 진입한다.
      if ((this.curMod.people || []).some((p) => this.pstat[p.id]
          && this.pstat[p.id].errorKind === "auth")) return;

      const parts = Object.assign({}, (sameResult && sameResult.parts) || {});
      const targets = [];
      for (const p of (this.curMod.people || [])) for (const bucket of ["open", "inProgress"]) {
        const partKey = p.id + "|" + bucket;
        if (this.pstat[p.id] && this.pstat[p.id].errorKind === "permission") {
          // 사람 요약에서 이미 권한 제외가 확정됐으면 같은 사람의 2개 상세를 다시 두드리지 않는다.
          parts[partKey] = { id: p.id, bucket, status: "permission", kind: "permission", rows: [] };
          continue;
        }
        const previous = parts[partKey];
        if (opts.retryFailed) {
          if (!previous || previous.status !== "error"
              || (opts.retryKind && previous.kind !== opts.retryKind)) continue;
        }
        targets.push({ p, bucket, partKey });
      }
      if (opts.retryFailed && !targets.length) return;

      const epoch = ++this.dueRiskEpoch;
      this.dueRiskBusy = true; this.dueRiskFor = requestKey;
      const nameOf = (pid) => (this.pstat[pid] && this.pstat[pid].name) || pid;
      const publish = () => {
        if (epoch !== this.dueRiskEpoch || this.dueRiskFor !== requestKey) return;
        this.dueRisk = summarizeDueRiskParts({
          people: this.curMod.people || [], parts, excludeVoc,
          dueRank: (ticket) => this.dueRank(ticket), nameOf,
        });
      };
      // 첫 조각부터 화면에 누적한다. 느리거나 실패하는 마지막 요청 때문에 이미 확인된
      // 마감 위험까지 가려지지 않는다(완료 전 수치는 '+'로 하한임을 표시).
      publish();
      try {
        await Promise.all(targets.map(async ({ p, bucket, partKey }) => {
          const result = await this._fetchBucketRows(p.id, bucket, this.doneDays, assignedWindow, {
            priority: 0,
            freshness: epoch,
            isCurrent: () => epoch === this.dueRiskEpoch && this.dueRiskFor === requestKey,
          });
          if (result.status === "cancelled"
              || epoch !== this.dueRiskEpoch || this.dueRiskFor !== requestKey) return;
          parts[partKey] = Object.assign({ id: p.id, bucket }, result);
          publish();
        }));
      } finally {
        if (epoch === this.dueRiskEpoch && this.dueRiskFor === requestKey) this.dueRiskBusy = false;
      }
    },
    retryDueRisk(kind = null) {
      if (!this.dueRisk || !this.dueRisk.failures) return;
      this.loadDueRisk({ retryFailed: true, retryKind: kind });
    },
    _savePrefs() {
      try { localStorage.setItem("workload.opts", JSON.stringify({ metric: this.metric, sortBy: this.sortBy, mod: this.mod, grouping: this.grouping, excludeVoc: this.excludeVoc, doneDays: this.doneDays, assignedWindow: this.assignedWindow })); }
      catch (e) { /* 사파리 프라이빗 등 */ }
    },
    /** 'VoC 제외' 토글 — 막대·통계·마감리스크 모두 재산출(마감리스크는 필터가 바뀌므로 재로딩). */
    setExcludeVoc(on) {
      this.excludeVoc = on;
      this._savePrefs();
      this._beginNoticeEpoch();
      this._invalidateDueRisk();
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
        label: String(g.value), name: g.name, nameAtomic: g.kind === "none",   // 'Epic 없음' 은 전부/생략
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
      return groups.map((g) => ({ value: g.value, color: g.color, name: g.name, nameAtomic: g.kind === "none", title: g.name + " " + g.value + u }));
    },
    /** 모듈 안에서 인력 정렬 — 이름 / 할당된 Ticket수 / 완료(완료 성과, 계산식에 따라 값이 달라짐).
     *  값 기준(할당·완료)은 **많은 순**. 아직 통계가 안 온 사람은 -1 로 맨 뒤(도착하면 제자리로). */
    sortedPeople(m) {
      const ppl = (m.people || []).slice();
      const nm = (p) => (this.pstat[p.id] && this.pstat[p.id].name) || p.name || p.id;
      if (this.sortBy === "name") return ppl.sort((a, b) => nm(a).localeCompare(nm(b), "ko"));
      const val = (p) => {
        const s = this.pstat[p.id];
        if (!s || s.error || s.loading || s.retrying) return -1;
        return this.sortBy === "assigned" ? this.assignedCount(s) : this.barVal(s.done7d, this.metric);
      };
      return ppl.sort((a, b) => val(b) - val(a));
    },
    seg(bar, metric) {
      const u = metric === "hr" ? "h" : "건";
      const t = this.mv(bar, "task", metric), s = this.mv(bar, "subtask", metric), v = this.vocVal(bar, metric);
      return [
        { value: t, color: "var(--wl-task)", name: "Task", title: "Task " + t + u },
        { value: s, color: "var(--wl-subtask)", name: "Sub-Task", title: "Sub-Task " + s + u },
        { value: v, color: "var(--wl-voc)", name: "VoC", title: "VoC " + v + u },
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
      if (!this.actOpen[id]) return;
      // 세 리스트는 독립 상태를 갖고 도착하는 대로 렌더한다. 이미 성공한 컬럼은 접었다 펴도
      // 그대로 사용하고, 아직 시작하지 않은 컬럼만 큐에 올린다.
      this._ensureDetailBox(id);
      for (const bucket of ["inProgress", "open", "done7d"]) {
        if (this.bucketStateOf(id, bucket).status === "idle") {
          this._loadDetailBucket(id, bucket, { priority: 30 });
        }
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
        <div class="wl-tile"><div class="wl-tile-v">{{ totals.op }}{{ curMod && curMod.peopleCount && !statsComplete ? '+' : '' }}</div><div class="wl-tile-l">할당됨</div></div>
        <div class="wl-tile"><div class="wl-tile-v">{{ totals.ip }}{{ curMod && curMod.peopleCount && !statsComplete ? '+' : '' }}</div><div class="wl-tile-l">진행 중</div></div>
        <div class="wl-tile"><div class="wl-tile-v">{{ totals.dn }}{{ curMod && curMod.peopleCount && !statsComplete ? '+' : '' }}</div><div class="wl-tile-l">{{ doneLabel }}</div></div>
        <div class="wl-tile" :class="{ warn: statsComplete && loadSkew && loadSkew.pct >= 40 }">
          <div class="wl-tile-v">{{ statsComplete ? (loadSkew ? loadSkew.pct + '%' : '—') : '…' }}</div><div class="wl-tile-l">부하 편중 · 상위1명</div></div>
        <div class="wl-tile" :class="{ warn: dueRisk && dueRisk.over.length }">
          <div class="wl-tile-v">{{ dueRisk ? (dueRisk.complete ? dueRisk.over.length : dueRisk.over.length + '+') : '…' }}</div><div class="wl-tile-l">마감 초과</div></div>
      </div>

      <!-- ══ 패널 그리드 (그라파나풍) ══ -->
      <div class="wl-grid">
        <!-- 개인별 워크로드 (전체폭) — 이 패널 전용 옵션 툴바 내장 -->
        <div class="wl-panel span12">
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
            <div class="wl-opt"><span class="wl-opt-l">할당 갱신</span><div class="fab-seg">
              <button v-for="w in ASSIGNED_WINDOWS" :key="w.k" :class="{ on: assignedWindow === w.k }"
                      @click="setAssignedWindow(w.k)" :title="w.hint">{{ w.label }}</button></div></div>
            <div class="wl-opt"><span class="wl-opt-l">완료 기간</span><div class="fab-seg">
              <button v-for="d in DONE_DAYS" :key="d" :class="{ on: doneDays === d }"
                      @click="setDoneDays(d)" :title="'최근 ' + d + '일 안에 완료된 Task 만 센다'">{{ d / 7 }}주</button></div></div>
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
              <div class="wbars"><div class="wside"><div class="hl">할당된 Ticket (Open + In-Progress) · {{ assignedLabel }}</div></div><div class="wside"><div class="hl">{{ doneLabel }} ({{ doneUnit }})</div></div></div>
              <div></div>
            </div>
            <!-- 평균선은 좌표(linePos)와 평균값(avgByMod)이 **둘 다** 있을 때만. 하나만 보고 그리면
                 통계를 다시 받는 동안(avgByMod 가 비고 linePos 는 남은 순간) 렌더가 터진다. -->
            <template v-if="linePos[m.module] && avgByMod[m.module]">
              <div class="mavg-line" :style="{ left: linePos[m.module].ipX + 'px', top: linePos[m.module].top + 'px' }"></div>
              <div class="mavg-line" :style="{ left: linePos[m.module].doneX + 'px', top: linePos[m.module].top + 'px' }"></div>
              <div class="mavg-num" :style="{ left: linePos[m.module].ipX + 'px', top: linePos[m.module].hy + 'px' }">모듈 평균 {{ avgByMod[m.module].ip }}건</div>
              <div class="mavg-num" :style="{ left: linePos[m.module].doneX + 'px', top: linePos[m.module].hy + 'px' }">모듈 평균 {{ avgByMod[m.module].dn }}{{ doneUnit }}</div>
            </template>
            <template v-for="p in sortedPeople(m)" :key="p.id">
              <div class="prow">
                <span class="pname" :title="p.id"><Avatar :user="p.id" :name="(pstat[p.id] && pstat[p.id].name) || p.name" :size="20" /><b>{{ (pstat[p.id] && pstat[p.id].name) || p.name }}</b><span v-if="(pstat[p.id] && pstat[p.id].kind) || p.kind" class="kbadge" :class="(pstat[p.id] && pstat[p.id].kind) || p.kind">{{ ((pstat[p.id] && pstat[p.id].kind) || p.kind) === 'dev' ? '개발' : '운영' }}</span></span>
                <!-- 통계는 사람 by 사람으로 도착한다. 일시 실패는 그 사람만 자동 재시도하고,
                     성공한 행과 상세 펼침 상태는 그대로 둔다. -->
                <div v-if="!pstat[p.id]" class="wbars wl-pending"><span class="wl-pending-t">불러오는 중…</span></div>
                <div v-else-if="pstat[p.id].loading || pstat[p.id].retrying" class="wbars wl-pending">
                  <span class="wl-pending-t">{{ pstat[p.id].retryAttempt ? '불러오는 중… (재시도: ' + pstat[p.id].retryAttempt + ')' : '불러오는 중…' }}</span>
                </div>
                <div v-else-if="pstat[p.id].errorKind === 'permission'" class="wbars">
                  <span class="muted mini">조회 제외</span>
                </div>
                <div v-else-if="pstat[p.id].errorKind === 'auth' && !pstat[p.id].partial" class="wbars wl-pending" title="인증이 복구되면 이 행만 자동으로 다시 받습니다.">
                  <span class="wl-pending-t">인증 후 자동 재시도</span>
                </div>
                <div v-else-if="pstat[p.id].error" class="wbars wl-fail" title="이 인력의 집계 조회에 실패했습니다(0 이 아님). 이 행만 다시 시도할 수 있습니다.">
                  <span class="wl-fail-t">집계 조회 실패</span>
                  <button class="wl-retry" @click.stop="retryPerson(p.id)">다시 시도</button>
                </div>
                <div v-else class="wbars">
                  <ProgressBar class="wside" :segments="assignedSegs(pstat[p.id])" :scale="scale.ip" show-total dark-text />
                  <ProgressBar class="wside" :segments="doneSegs(pstat[p.id])" :scale="scale.dn" show-total dark-text />
                </div>
                <button class="plus" @click="toggleAct(p.id)">{{ actOpen[p.id] ? "−" : "+" }}</button>
              </div>
              <div v-if="actOpen[p.id]" class="act">
                <div v-if="!tkd[p.id]" class="loading">불러오는 중…</div>
                <template v-else>
                  <!-- 세 버킷에서 찾은 티켓의 **소속 Epic 분포**. 이 사람이 지금 어느 Epic 에
                       매여 있는지가 목록보다 먼저 읽혀야 한다. -->
                  <div class="wepic">
                    <div class="wepic-h">Epic 분포
                      <b>{{ detailComplete(p.id) ? epicDist(p.id).total : epicDist(p.id).total + '+' }}</b>
                      <span class="muted mini">할당됨·진행중·최근완료 합산</span>
                    </div>
                    <ProgressBar :segments="epicDist(p.id).segments" :height="18" show-total />
                    <div class="wepic-lg">
                      <span v-for="g in epicDist(p.id).groups" :key="g.key" class="wepic-i"
                            :class="{ voc: g.kind === 'voc', none: g.kind === 'none' }"
                            :title="g.name + ' · ' + g.value + '건'">
                        <i :style="{ background: g.color }"></i>{{ g.name }} <b>{{ g.value }}</b>
                      </span>
                      <span v-if="!epicDist(p.id).groups.length && detailComplete(p.id)" class="muted mini">티켓 없음</span>
                      <span v-else-if="!detailComplete(p.id)" class="muted mini">일부 목록 미확인</span>
                    </div>
                  </div>

                  <!-- 할당됨 / 진행중 / 최근완료 — 상태 흐름 순서대로 3컬럼 -->
                  <div class="tcols3">
                    <div v-for="c in WL_COLS" :key="c.k" class="tcol" :class="'c-' + c.k">
                      <div class="sec-t">{{ c.k === 'done7d' ? doneLabel : c.label }}
                        <b>{{ bucketStateOf(p.id, c.k).status === 'success' ? (tkd[p.id][c.k] || []).length : ((tkd[p.id][c.k] || []).length ? (tkd[p.id][c.k] || []).length + '+' : '…') }}</b>
                      </div>
                      <div class="tcol-body">
                      <div v-if="bucketStateOf(p.id, c.k).status === 'loading' || bucketStateOf(p.id, c.k).status === 'retrying'" class="loading">
                        {{ bucketStateOf(p.id, c.k).attempt ? '불러오는 중… (재시도: ' + bucketStateOf(p.id, c.k).attempt + ')' : '불러오는 중…' }}
                      </div>
                      <div v-else-if="bucketStateOf(p.id, c.k).status === 'permission'" class="muted mini">조회 제외</div>
                      <div v-else-if="bucketStateOf(p.id, c.k).status === 'error'" class="muted mini">
                        <span>{{ bucketStateOf(p.id, c.k).kind === 'auth' ? '인증 후 자동 재시도' : '불러오지 못했습니다' }}</span>
                        <button v-if="bucketStateOf(p.id, c.k).kind !== 'auth'" class="wl-retry" @click.stop="retryBucket(p.id, c.k)">다시 시도</button>
                      </div>
                      <template v-else-if="bucketStateOf(p.id, c.k).status === 'success' || bucketStateOf(p.id, c.k).status === 'partial'">
                        <div v-for="t in tkd[p.id][c.k]" :key="c.k + '-' + t.key" class="wtk tkt"
                             :class="c.cls" :data-key="t.key" role="button" tabindex="0"
                             :title="t.key + ' · ' + t.summary">
                          <TypeBadge :type="t.type" /><span class="ky">{{ t.key }}</span>
                          <span class="sm">{{ vocStrip(t) }}</span>
                          <span class="sched">
                            <span v-if="t.epic" class="ebadge" :style="{ '--sig': epicColorOf(p.id, t) }"
                                  :title="'Epic: ' + (t.epicName || t.epic)">{{ t.epicName || t.epic }}</span>
                            <span v-else-if="t.epicResolution && t.epicResolution.complete === false"
                                  class="ebadge none">{{ t.epicResolution.retryable ? 'Epic 미확인' : '조회 제외' }}</span>
                            <span v-else-if="t.voc" class="ebadge voc" :title="'사용자 VoC' + (vocSegs(t.summary).length > 1 ? ' — ' + vocSegs(t.summary).slice(1).join(' · ') : '')"><span v-for="(sg, si) in vocSegs(t.summary)" :key="si" class="vseg" :class="{ head: si === 0 }">{{ sg }}</span></span>
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
                      <div v-else class="loading">대기 중…</div>
                      </div>
                    </div>
                  </div>
                </template>
              </div>
            </template>
          </template>
        </div>
        </div><!-- /wl-panel 개인별 워크로드 -->

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
                <div v-else-if="statsComplete" class="mini ok">단독 참여 Epic 없음 ✓</div>
                <div v-else class="muted mini">확인된 단독 참여 Epic 없음 · 일부 인력 미확인</div>
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
                <span v-if="!epicSpread.length && statsComplete" class="muted mini">데이터 없음</span>
                <span v-else-if="!statsComplete" class="muted mini">일부 인력 미확인</span>
              </div>
            </div>
          </div>
          <!-- 마감 리스크 -->
          <div class="wl-panel">
            <div class="wl-panel-h"><b>마감 리스크</b> <span class="mini muted">초과 · 임박(D-3)</span></div>
            <div class="wl-panel-b">
              <div v-if="dueRiskBusy && !dueRisk" class="muted mini"><span class="spinner"></span> 불러오는 중…</div>
              <template v-else-if="dueRisk">
                <div class="wl-mon-big" :class="{ warn: dueRisk.over.length }">초과 <b>{{ dueRisk.over.length }}{{ dueRisk.complete ? '' : '+' }}</b> · 임박 <b>{{ dueRisk.soon.length }}{{ dueRisk.complete ? '' : '+' }}</b></div>
                <div v-if="dueRiskBusy" class="muted mini"><span class="spinner"></span> 실패한 항목 다시 확인 중…</div>
                <div class="wl-risk-list">
                  <span v-for="(x, i) in dueRisk.over.slice(0, 6)" :key="'o' + i" class="wl-risk-row tkt" :data-key="x.t.key" role="button"
                        :title="x.t.key + ' · ' + x.who + ' · ' + x.t.summary">
                    <b class="wl-risk-key">{{ x.t.key }}</b>
                    <span class="wl-risk-title">{{ vocStrip(x.t) }}</span>
                    <span v-if="x.t.voc && !x.t.epic" class="wl-risk-epic voc" :style="{ '--ec': riskEpic(x.t).color }"><span v-for="(sg, si) in vocSegs(x.t.summary)" :key="si" class="vseg" :class="{ head: si === 0 }">{{ sg }}</span></span>
                    <span v-else class="wl-risk-epic" :style="{ '--ec': riskEpic(x.t).color }">{{ riskEpic(x.t).label }}</span>
                    <b class="wl-risk-dd" :class="ddCls(x.t.due)">{{ dd(x.t.due) }}</b>
                  </span>
                  <span v-for="(x, i) in dueRisk.soon.slice(0, 4)" :key="'s' + i" class="wl-risk-row tkt" :data-key="x.t.key" role="button"
                        :title="x.t.key + ' · ' + x.who + ' · ' + x.t.summary">
                    <b class="wl-risk-key">{{ x.t.key }}</b>
                    <span class="wl-risk-title">{{ vocStrip(x.t) }}</span>
                    <span v-if="x.t.voc && !x.t.epic" class="wl-risk-epic voc" :style="{ '--ec': riskEpic(x.t).color }"><span v-for="(sg, si) in vocSegs(x.t.summary)" :key="si" class="vseg" :class="{ head: si === 0 }">{{ sg }}</span></span>
                    <span v-else class="wl-risk-epic" :style="{ '--ec': riskEpic(x.t).color }">{{ riskEpic(x.t).label }}</span>
                    <b class="wl-risk-dd" :class="ddCls(x.t.due)">{{ dd(x.t.due) }}</b>
                  </span>
                  <span v-if="!dueRisk.over.length && !dueRisk.soon.length && dueRisk.complete" class="mini ok">마감 위험 없음 ✓</span>
                  <span v-else-if="!dueRisk.complete" class="muted mini">{{ dueRisk.over.length || dueRisk.soon.length ? '일부 항목 미확인' : '확인된 위험 없음 · 일부 항목 미확인' }}</span>
                  <button v-if="dueRisk.failures && !dueRiskBusy" class="wl-retry" @click.stop="retryDueRisk()">실패 항목 다시 시도</button>
                </div>
              </template>
              <div v-else class="muted mini">—</div>
            </div>
          </div>
        </div>

        <!-- 우측 세로 스택: ① 모듈이 기여하는 Epic (위) → ② Epic별 인력 지분 (아래) -->
        <div class="wl-col span8">
          <!-- ① 모듈이 기여하는 Epic -->
          <div class="wl-panel">
            <div class="wl-panel-h"><b>모듈이 기여하는 Epic</b> <span class="mini muted">할당+진행+완료 · 건</span></div>
            <div class="wl-panel-b">
              <div v-if="!statsReady" class="muted mini">집계 중… ({{ moduleAgg(curMod).loaded }}/{{ curMod.peopleCount }})</div>
              <template v-else>
                <ProgressBar :segments="moduleEpicSegs()" :height="18" show-total />
                <div class="wl-epic-lg">
                  <span v-for="g in moduleEpicGroups.groups" :key="g.key" class="wl-epic-i"
                        :class="{ voc: g.kind === 'voc', none: g.kind === 'none' }" :title="g.name + ' · ' + g.value + '건'">
                    <i :style="{ background: g.color }"></i>{{ g.name }} <b>{{ g.pct }}%</b></span>
                  <span v-if="!moduleEpicGroups.groups.length && statsComplete" class="muted mini">집계할 작업이 없습니다.</span>
                  <span v-else-if="!statsComplete" class="muted mini">일부 인력 미확인</span>
                </div>
              </template>
            </div>
          </div>
          <!-- ② Epic별 인력 지분 -->
          <div class="wl-panel">
            <div class="wl-panel-h"><b>Epic별 인력 지분</b> <span class="mini muted">누가 얼마나 (상위 8)</span></div>
            <div class="wl-panel-b">
              <div v-if="!statsReady" class="muted mini">집계 중…</div>
              <template v-else>
                <div v-for="r in epicPeopleRows" :key="r.epic.key" class="wl-epr">
                  <div class="wl-epr-h">
                    <span class="wl-epr-sw" :style="{ background: r.epic.color }"></span><b class="wl-epr-name">{{ r.epic.name }}</b>
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
        </div>
      </div><!-- /wl-grid -->
    </template>
    <div v-else class="loading page">불러오는 중…</div>
  </div>`,
};
