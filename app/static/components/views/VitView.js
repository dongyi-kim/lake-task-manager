// VitView.js — 기능2 현안(PMO_VIT). 컬럼: 티켓(상태·타입·담당자/번호·이름/Started·Due(D-day)) ·
//   직계 하위 티켓(표: Sub Task·상태·시작일·종료일·담당자) + [자세히] 트리/코멘트.
//   현안/하위 티켓은 행 전체가 클릭 대상(.tkt[data-key]) → 인앱 티켓 다이얼로그.
// '완료 작업 안 보기' 토글로 직계 완료 티켓 숨김. updated: 2026-07-09
import { api } from "../../lib/api.js";
import { moduleColor, STATUS_ORDER, STATUS_VAR, statusLabel, typeLabel, TYPE_BG } from "../../lib/colors.js";
import { esc, mdISO, ymd, ymdhm, mdhm, tkt, dday } from "../../lib/fmt.js";
import TypeBadge from "../ui/TypeBadge.js";
import StatusPill from "../ui/StatusPill.js";

const KLAB = { created: "생성됨", done: "완료됨", resolved: "해결됨" };

// 데일리미팅 강화 임계 — '지난 N일' 변동 브리핑 창, '무변동 N일 이상'이면 정체로 본다.
const BRIEF_DAYS = 3;
const STALE_DAYS = 5;

export default {
  name: "VitView",
  components: { TypeBadge, StatusPill },
  data() { return { d: null, err: "", detail: {}, detailOpen: {}, hideDone: false,
                    mods: {}, modErr: {}, modPartial: {}, modLoading: {},
                    detailErr: {}, detailLoading: {}, busy: false,
                    briefDays: BRIEF_DAYS }; },
  computed: {
    allIssues() {
      if (!this.d) return [];
      return this.d.modules.flatMap((m) => this.mods[m.module] || []);
    },
    // 상단 칩 지표 — 모듈 본문이 전부 도착하면 전체 (완료/전체) 를 보여줄 수 있다.
    allLoaded() {
      return !!this.d && this.d.modules.every((m) => Array.isArray(this.mods[m.module])
        && !this.modLoading[m.module] && !this.modErr[m.module] && !this.modPartial[m.module]);
    },
    allDone() { return this.allIssues.filter((it) => it.statusCategory === "done").length; },
    // 상단 '오늘의 브리핑' — 지난 BRIEF_DAYS 일 완료·신규(자손 소식) + 지금 지연·정체인 현안 수.
    brief() {
      let done = 0, created = 0, late = 0, stale = 0;
      this.allIssues.forEach((it) => {
        (it.news || []).forEach((e) => {
          const ds = this.daysSince(e.date);
          if (ds != null && ds <= BRIEF_DAYS) { if (e.kind === "created") created++; else done++; }
        });
        if (this.isLate(it)) late++;
        else if (this.isStale(it)) stale++;
      });
      return { done, created, late, stale };
    },
  },
  // 모듈별 병렬 로딩: 골격(shell)을 먼저 그리고 각 모듈을 동시에 요청해 **도착하는 대로** 채운다.
  // (전부 모일 때까지 기다리지 않음 — 느린 모듈이 나머지를 막지 않는다)
  async mounted() { await this.load(); },
  created() {
    // 좌하단 플로팅 새로고침이 부른다 — 뷰마다 캐시를 비우고 다시 받는 함수가 이름이 달라
    // (hardRefresh/refresh) 여기서 한 번에 잇는다. 끝나면 버튼에 '됐다' 고 알린다.
    window.addEventListener("force-refresh", this._fr = async () => {
      try { await this.hardRefresh(); } finally {
        window.dispatchEvent(new CustomEvent("force-refresh-done"));
      }
    });
    // 재인증(auth-ok) 후 — 세션 끊긴 채 실패했던 조회를 가볍게 다시 받는다(서버 캐시는 안 비움).
    window.addEventListener("auth-ok", this._authok = () => {
      if (this.err || !this.d) { this.load(); return; }
      Object.keys(this.modErr).forEach((module) => this.retryModule(module, false));
      Object.keys(this.detailErr).filter((key) => this.detailOpen[key])
        .forEach((key) => this.loadDetail({ key }, false));
    });
  },
  unmounted() {
    this._loadSeq = (this._loadSeq || 0) + 1;
    this._detailEpoch = (this._detailEpoch || 0) + 1;
    window.removeEventListener("force-refresh", this._fr);
    window.removeEventListener("auth-ok", this._authok);
  },
  methods: {
    /** 골격(모듈 목록)을 먼저 받고, 모듈별 본문을 병렬로 채운다.
     *  ★ 이 로직이 예전엔 mounted() 안에 인라인이라, hardRefresh 가 this.load() 를 부르면
     *    'this.load is not a function' 이었다(새로고침 버튼이 그래서 죽었다). 메서드로 뺀다. */
    async load() {
      const loadSeq = this._loadSeq = (this._loadSeq || 0) + 1;
      this.err = ""; this.modLoading = {};
      try {
        const shell = await api.vitShell();
        if (loadSeq !== this._loadSeq) return;
        this.d = shell;
        shell.modules.forEach((m) => this.loadModule(m.module, loadSeq));
      } catch (e) { if (loadSeq === this._loadSeq) this.err = e.message; }
    },
    loadModule(module, loadSeq = this._loadSeq, evict = false) {
      if (loadSeq !== this._loadSeq) return Promise.resolve(null);
      if (evict) api.evict(module);
      this._moduleSeq = this._moduleSeq || {};
      const requestId = this._moduleSeq[module] = (this._moduleSeq[module] || 0) + 1;
      const fresh = () => loadSeq === this._loadSeq && requestId === this._moduleSeq[module];
      this.modLoading = Object.assign({}, this.modLoading, { [module]: true });
      return api.vitModule(module).then((r) => {
        if (!fresh()) return r;
        this.mods = Object.assign({}, this.mods, { [module]: (r && r.issues) || [] });
        const errors = Object.assign({}, this.modErr); delete errors[module]; this.modErr = errors;
        const partial = Object.assign({}, this.modPartial);
        if (r && r.partial) partial[module] = r.missing || 1;
        else delete partial[module];
        this.modPartial = partial;
        return r;
      }).catch((e) => {
        if (fresh()) this.modErr = Object.assign({}, this.modErr, {
          [module]: (e && e.message) || "불러오지 못했습니다.",
        });
        return null;
      }).finally(() => {
        if (!fresh()) return;
        const loading = Object.assign({}, this.modLoading); delete loading[module]; this.modLoading = loading;
      });
    },
    retryModule(module, evict = true) { return this.loadModule(module, this._loadSeq, evict); },
    /** 캐시를 비우고 전부 다시 받는다. 화면도 '모른다' 상태로 되돌린 뒤 새로 채운다 —
     *  옛 값을 남겨 두면 무엇이 새로 온 값인지 알 수 없다. */
    async hardRefresh() {
      if (this.busy) return;
      this.busy = true;
      try {
        await api.refresh();
        this._loadSeq = (this._loadSeq || 0) + 1;
        this._detailEpoch = (this._detailEpoch || 0) + 1;
        this.d = null; this.mods = {}; this.modErr = {}; this.modPartial = {}; this.modLoading = {};
        this.detail = {}; this.detailOpen = {}; this.detailErr = {}; this.detailLoading = {};
        await this.load();
      } catch (e) {
        this.err = (e && e.message) || "다시 받지 못했습니다.";
      } finally { this.busy = false; }
    },
    kids(it) {   // 직계 하위 티켓 — '완료 작업 안 보기' 시 done 제외 + 상태 정렬(Open→진행중→해결)
      const ch = it.children || [];
      const arr = this.hideDone ? ch.filter((c) => c.statusCategory !== "done") : ch.slice();
      return arr.sort((a, b) =>
        ((STATUS_ORDER[a.statusCategory] ?? 9) - (STATUS_ORDER[b.statusCategory] ?? 9)));
    },
    // 세로 진척 바용 — **직계 하위 티켓 전체** 기준(상태별 개수).
    // '완료 작업 안 보기'는 목록 표시만 거르는 옵션이므로 진척도는 영향받지 않아야 한다
    // (완료를 숨겼다고 진척률이 떨어져 보이면 오독을 부른다). → kids() 가 아니라 children 사용.
    kidStats(it) {
      const c = { todo: 0, inprogress: 0, done: 0 };
      (it.children || []).forEach((k) => { c[k.statusCategory] = (c[k.statusCategory] || 0) + 1; });
      const total = c.todo + c.inprogress + c.done;
      return { ...c, total, pct: total ? Math.round((c.done * 100) / total) : 0 };
    },
    mcolor(i) { return moduleColor(i); },
    md(s) { return mdISO(s); },
    fy(s) { return ymd(s); },
    fdt(s) { return ymdhm(s); },
    dd(s) { return dday(s); },
    tyLabel(t) { return typeLabel(t); },   // 이슈타입 → 짧은 라벨(제목 뒤 텍스트로 붙임)
    tyColor(t) { return TYPE_BG[t] || "var(--ty-task)"; },   // 타입 텍스트 색 = 타입 시그니처 색
    slb(s, cat) { return statusLabel(s) || ({ todo: "대기", inprogress: "진행 중", done: "완료" }[cat] || cat); },
    scls(cat) { return "s-" + ({ inprogress: "prog", done: "done" }[cat] || "todo"); },   // 상태 → 채운 셀 클래스
    dueOverdue(iso) {   // D-Day 당일 또는 그 이후(초과) → 붉게
      const due = new Date(iso.substring(0, 10) + "T00:00:00");
      const today = new Date(); today.setHours(0, 0, 0, 0);
      return Math.round((due - today) / 86400000) <= 0;
    },
    startedAt(it) {   // Created 와 Started 중 늦은 것 (ISO 문자열 사전순 = 시간순)
      const c = it.created || "", s = it.started || "";
      if (!c) return s; if (!s) return c;
      return s > c ? s : c;
    },
    tk(key) { return tkt(key, this.d && this.d.jiraBase); },
    jiraUrl(key) { return (this.d && this.d.jiraBase) ? this.d.jiraBase + "/browse/" + key : "#"; },
    prog(it) { return it.progress || { done: 0, total: 0, pct: 0 }; },
    // ── 데일리 강화: '움직임'·위험 신호 ─────────────────────────────
    daysSince(iso) {   // 오늘 기준 경과 일수(달력일). 값 없으면 null.
      if (!iso) return null;
      const d = new Date(iso.substring(0, 10) + "T00:00:00");
      if (isNaN(d)) return null;
      const t = new Date(); t.setHours(0, 0, 0, 0);
      return Math.round((t - d) / 86400000);
    },
    isDone(it) { return it.statusCategory === "done"; },
    lastActivity(it) {   // 루트 updated 와 최근 소식(news) 중 가장 최근 = '마지막 움직임'
      let best = it.updated || it.started || it.created || "";
      (it.news || []).forEach((e) => { if (e.date && e.date > best) best = e.date; });
      return best;
    },
    stallDays(it) { return this.daysSince(this.lastActivity(it)); },
    isLate(it) { return !!it.due && !this.isDone(it) && this.dueOverdue(it.due); },
    isStale(it) { const n = this.stallDays(it); return !this.isDone(it) && n != null && n >= STALE_DAYS; },
    recentCount(it, kind) {   // 지난 BRIEF_DAYS 일 소식 수. kind: 'created' | 'done'(=완료/해결)
      return (it.news || []).filter((e) => {
        const ds = this.daysSince(e.date);
        if (ds == null || ds > BRIEF_DAYS) return false;
        return kind === "created" ? e.kind === "created" : e.kind !== "created";
      }).length;
    },
    // 데일리 정렬 — 지연 → 정체 → 진행 → 완료. 동순위는 서버 정렬(updated 내림차순) 유지(안정정렬).
    rank(it) { if (this.isLate(it)) return 0; if (this.isStale(it)) return 1; return this.isDone(it) ? 3 : 2; },
    sortedIssues(module) { return (this.mods[module] || []).slice().sort((a, b) => this.rank(a) - this.rank(b)); },
    modRisk(module) {
      let late = 0, stale = 0;
      (this.mods[module] || []).forEach((it) => { if (this.isLate(it)) late++; else if (this.isStale(it)) stale++; });
      return { late, stale };
    },
    modCounts(module) {   // 모듈별 현안 상태 집계 — 전체/할일/진행중/완료
      const arr = this.mods[module] || [];
      let todo = 0, inprogress = 0, done = 0;
      arr.forEach((it) => {
        if (it.statusCategory === "done") done++;
        else if (it.statusCategory === "inprogress") inprogress++;
        else todo++;
      });
      return { total: arr.length, todo, inprogress, done };
    },
    newsHtml(ev) {
      return `<span class='d'>${ymdhm(ev.date)}</span><span class='act ${ev.kind}'>${KLAB[ev.kind] || ev.kind}</span>`
        + `${tkt(ev.key, this.d.jiraBase)} <span class='sm'>${esc(ev.title || "")}</span>`;
    },
    toggleDetail(it) {
      this.detailOpen[it.key] = !this.detailOpen[it.key];
      if (this.detailOpen[it.key] && !this.detail[it.key] && !this.detailLoading[it.key]) this.loadDetail(it);
    },
    loadDetail(it, evict = false) {
      const key = it.key;
      if (evict) api.evict(key);
      const epoch = this._detailEpoch || 0;
      this._detailSeq = this._detailSeq || {};
      const requestId = this._detailSeq[key] = (this._detailSeq[key] || 0) + 1;
      const fresh = () => epoch === (this._detailEpoch || 0) && requestId === this._detailSeq[key];
      this.detailLoading = Object.assign({}, this.detailLoading, { [key]: true });
      return api.vitDetail(key).then((value) => {
        if (!fresh()) return value;
        this.detail = Object.assign({}, this.detail, { [key]: value || { tree: [], comments: [] } });
        const errors = Object.assign({}, this.detailErr); delete errors[key]; this.detailErr = errors;
        return value;
      }).catch((e) => {
        if (fresh()) this.detailErr = Object.assign({}, this.detailErr, {
          [key]: (e && e.message) || "상세 정보를 불러오지 못했습니다.",
        });
        return null;
      }).finally(() => {
        if (!fresh()) return;
        const loading = Object.assign({}, this.detailLoading); delete loading[key]; this.detailLoading = loading;
      });
    },
    retryDetail(it) { return this.loadDetail(it, true); },
    // 자손 트리 → 상태정렬 + 안내선 정보 포함 평탄화
    flatTree(nodes, anc) {
      anc = anc || [];
      const out = [];
      const arr = (nodes || []).slice().sort((a, b) =>
        ((STATUS_ORDER[a.statusCategory] ?? 9) - (STATUS_ORDER[b.statusCategory] ?? 9)));
      arr.forEach((n, i) => {
        const isLast = i === arr.length - 1;
        out.push({ node: n, anc, isLast });
        if (n.children && n.children.length) out.push(...this.flatTree(n.children, anc.concat(!isLast)));
      });
      return out;
    },
    treeRowHtml(r) {
      const n = r.node;
      let g = ""; r.anc.forEach((c) => { g += `<span class='tg${c ? " v" : ""}'></span>`; });
      g += `<span class='tg node${r.isLast ? " last" : ""}'></span>`;
      const col = STATUS_VAR[n.statusCategory] || "var(--st-todo)";
      const prog = n.resolved ? ("완료 " + ymdhm(n.resolved)) : (n.created ? ("생성 " + ymdhm(n.created)) : "");
      return `<span class='typc'>${g}</span>`
        + `<span class='tcard'>`
        + `<span class='tbadge v-solid' style='--tc:${TYPE_BG[n.type] || "var(--ty-task)"}'>${typeLabel(n.type)}</span>`
        + `<span class='ky'>${esc(n.key)}</span>`
        + `<span class='sm'>${esc(n.summary || "")}</span>`
        + (n.assignee ? `<span class='asg'>${esc(n.assignee)}</span>` : "")
        + `<span class='pill' style='color:${col};border-color:${col}'>${esc(this.slb(n.status, n.statusCategory))}</span>`
        + `</span>`
        + `<span class='pg'>${prog}</span>`;
    },
    cdate(s) { return mdhm(s); },   // 댓글: 연도 없이 월일 시분
    cmtText(x) {   // HTML 태그를 렌더도 노출도 안 되게 — 평문만 추출(textContent) + 줄바꿈 접기
      const d = document.createElement("div");
      d.innerHTML = x.text || "";
      return (d.textContent || d.innerText || "").replace(/\s*[\r\n]+\s*/g, " ").trim();
    },
  },
  template: `
  <div class="vit-view">
    <div v-if="err" class="err">현안 데이터를 불러오지 못했습니다: {{ err }}</div>
    <template v-if="d">
      <!-- 상단 주요지표 — 전체·모듈별 (완료/전체). 모듈 본문이 도착하는 대로 개수→완료/전체 로 승격 -->
      <div class="chips">
        <div class="chip" style="background:var(--accent);color:#fff;border-color:transparent">
          <b v-if="allLoaded">{{ allDone }}/{{ d.summary.total }}</b><b v-else>{{ d.summary.total }}</b> 현안 (PMO_VIT)
        </div>
        <div v-for="(m, i) in d.modules" :key="m.module" class="chip">
          <span class="sw" :style="{ background: mcolor(i) }"></span> {{ m.module }}
          <b v-if="mods[m.module]"><em class="cd">{{ modCounts(m.module).done }}</em>/{{ m.count }}</b>
          <b v-else>{{ m.count }}</b>
        </div>
      </div>
      <div class="note" v-if="d.summary.skippedDup">상위가 이미 PMO_VIT 인 자손 현안 {{ d.summary.skippedDup }}건은 중복으로 숨김</div>

      <!-- 오늘의 브리핑 — 데일리미팅 한 줄 요약. 지난 N일 움직임(완료·신규) + 지금 위험(지연·정체). -->
      <div class="brief">
        <span class="bt">📋 오늘의 브리핑</span>
        <span class="bwin">지난 {{ briefDays }}일</span>
        <span class="bi done"><b>{{ brief.done }}</b> 완료</span>
        <span class="bi new"><b>{{ brief.created }}</b> 신규</span>
        <span class="bsep"></span>
        <span class="bwin">현재</span>
        <span class="bi late"><b>{{ brief.late }}</b> 지연</span>
        <span class="bi stale"><b>{{ brief.stale }}</b> 정체</span>
      </div>

      <div v-for="(m, i) in d.modules" :key="m.module" class="vgroup">
        <div class="vg-head"><span class="dot" :style="{ background: mcolor(i) }"></span><b>{{ m.module }}</b><span class="c">{{ m.count }} 현안</span>
          <span v-if="mods[m.module]" class="mcounts">전체 <b>{{ modCounts(m.module).total }}</b> · 할일 <b class="t">{{ modCounts(m.module).todo }}</b> · 진행중 <b class="p">{{ modCounts(m.module).inprogress }}</b> · 완료 <b class="d">{{ modCounts(m.module).done }}</b></span>
          <span v-if="mods[m.module] && modRisk(m.module).late" class="rk late sm">지연 {{ modRisk(m.module).late }}</span>
          <span v-if="mods[m.module] && modRisk(m.module).stale" class="rk stale sm">정체 {{ modRisk(m.module).stale }}</span>
        </div>
        <div v-if="modErr[m.module]" class="err">
          · 불러오지 못했습니다: {{ modErr[m.module] }}
          <button type="button" class="btn" :disabled="modLoading[m.module]" @click="retryModule(m.module)">
            {{ modLoading[m.module] ? '재시도 중…' : '이 모듈 재시도' }}
          </button>
        </div>
        <div v-if="modLoading[m.module] && !mods[m.module]" class="loading">· 현안과 하위 티켓을 불러오는 중…</div>
        <div v-if="Array.isArray(mods[m.module]) && !mods[m.module].length && !modErr[m.module] && !modPartial[m.module]" class="empty">· 현안 없음</div>
        <!-- 일부만 왔다 — 목록은 보여 주되 '이게 전부' 라고 말하지 않는다 -->
        <div v-if="modPartial[m.module]" class="err">
          · 일부를 불러오지 못했습니다({{ modPartial[m.module] }}건)
          <button type="button" class="btn" :disabled="modLoading[m.module]" @click="retryModule(m.module)">
            {{ modLoading[m.module] ? '재시도 중…' : '이 모듈 재시도' }}
          </button>
        </div>
        <div v-if="mods[m.module] && mods[m.module].length" class="tbl">
          <div class="vhead"><div>티켓</div><div class="ch-head"><span></span><span>상태</span><span>Sub Task</span><span>시작일</span><span>종료일</span><span>담당자</span></div><div></div></div>
          <template v-for="it in sortedIssues(m.module)" :key="it.key">
            <div class="vrow">
              <!-- 티켓 컬럼 3줄: [제목(크게)|타입|번호] / [상태|기간상태뱃지|담당자] / [시작|Due] -->
              <div class="c-info">
                <div class="l2 tkt" :data-key="it.key" role="button" tabindex="0"
                     :title="it.key + ' · ' + it.summary">
                  <span class="summ">{{ it.summary }}</span><span class="ty" :style="{ color: tyColor(it.type) }">{{ tyLabel(it.type) }}</span><span class="key">{{ it.key }}</span>
                </div>
                <div class="l1">
                  <StatusPill :cat="it.statusCategory" :label="it.status" />
                  <span v-if="isLate(it)" class="rk late">지연 {{ dd(it.due) }}</span>
                  <span v-else-if="isStale(it)" class="rk stale">정체 {{ stallDays(it) }}일</span>
                  <span v-else-if="it.due" class="rk dday" :class="{ hot: dueOverdue(it.due) }">{{ dd(it.due) }}</span>
                  <span class="who">{{ it.assignee || "미지정" }}</span>
                  <span v-if="recentCount(it,'done')" class="rk move">↑완료 {{ recentCount(it,'done') }}</span>
                  <span v-if="recentCount(it,'created')" class="rk new">+신규 {{ recentCount(it,'created') }}</span>
                </div>
                <div class="l3">
                  <span class="dt"><span class="dl">Started</span>{{ fy(startedAt(it)) || "—" }}</span>
                  <span class="dt"><span class="dl">Due</span><span v-if="it.due" :class="{ overdue: dueOverdue(it.due) }">{{ fy(it.due) }}</span><span v-else>—</span></span>
                </div>
              </div>
              <!-- 세로 진척 바(티켓 수 기준) + 하위 티켓 목록. 목록이 Open→진행중→해결 순이라
                   바의 구간 높이가 각 상태 묶음과 그대로 맞아떨어진다. -->
              <div class="c-kids">
                <div v-if="kidStats(it).total" class="cv-rail"
                     :title="'완료 ' + kidStats(it).done + '/' + kidStats(it).total + ' (' + kidStats(it).pct + '%) · 진행중 ' + kidStats(it).inprogress + ' · Open ' + kidStats(it).todo">
                  <span class="cv-seg s-todo" :style="{ flexGrow: kidStats(it).todo }"></span>
                  <span class="cv-seg s-prog" :style="{ flexGrow: kidStats(it).inprogress }"></span>
                  <span class="cv-seg s-done" :style="{ flexGrow: kidStats(it).done }"></span>
                </div>
                <div class="c-children">
                  <div v-for="c in kids(it)" :key="c.key" class="ctr tkt" :data-key="c.key"
                       role="button" tabindex="0" :title="c.key + ' · ' + c.summary">
                    <div class="scell" :class="scls(c.statusCategory)">{{ slb(c.status, c.statusCategory) }}</div>
                    <div class="ct-tkt"><span class="sm">{{ c.summary }}</span><span class="ty">{{ tyLabel(c.type) }}</span></div>
                    <div class="dt">{{ fy(c.created) || "—" }}</div>
                    <div class="dt">{{ c.resolved ? fy(c.resolved) : "—" }}</div>
                    <div class="asg">{{ c.assignee || "—" }}</div>
                  </div>
                  <div v-if="!kids(it).length" class="mini muted">{{ (it.children || []).length ? '표시할 하위 티켓 없음 (완료 숨김)' : '직계 하위 티켓 없음' }}</div>
                </div>
              </div>
              <div class="c-x"><button class="xbtn" @click="toggleDetail(it)">{{ detailOpen[it.key] ? "접기 ▴" : "자세히 ▾" }}</button></div>
            </div>
            <div v-if="detailOpen[it.key]" class="vrow"><div class="detail">
              <div v-if="detailErr[it.key]" class="err">
                · 상세 정보를 불러오지 못했습니다: {{ detailErr[it.key] }}
                <button type="button" class="btn" :disabled="detailLoading[it.key]" @click="retryDetail(it)">
                  {{ detailLoading[it.key] ? '재시도 중…' : '상세 재시도' }}
                </button>
              </div>
              <div v-if="detailLoading[it.key] && !detail[it.key]" class="loading">· 불러오는 중…</div>
              <div v-if="detail[it.key]" class="dcols">
                <div>
                  <div class="sec-t">소속 티켓 트리 ({{ flatTree(detail[it.key].tree).length }}개 · 상태·최근 진척)</div>
                  <div v-for="(r, k) in flatTree(detail[it.key].tree)" :key="k" class="tnode tkt"
                       :data-key="r.node.key" role="button" tabindex="0"
                       :title="r.node.key + ' · ' + r.node.summary" v-html="treeRowHtml(r)"></div>
                </div>
                <div>
                  <div class="sec-t">코멘트 (현안 티켓 기준)</div>
                  <template v-if="detail[it.key].comments && detail[it.key].comments.length">
                    <div v-for="(x, k) in detail[it.key].comments" :key="k" class="cmt"><b class="au">{{ x.author || "?" }}</b><span class="dt">{{ cdate(x.date) }}</span><span class="tx">{{ cmtText(x) }}</span></div>
                  </template>
                  <div v-else class="mini muted">코멘트 없음</div>
                </div>
              </div>
            </div></div>
          </template>
        </div>
      </div>
      <div class="fab">
        <button class="fab-btn" :class="{ on: hideDone }" @click="hideDone = !hideDone">{{ hideDone ? '☑' : '☐' }} 완료 작업 안 보기</button>
      </div>
    </template>
    <div v-else-if="!err" class="loading page">불러오는 중…</div>
  </div>`,
};
