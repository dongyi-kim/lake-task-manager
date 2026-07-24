// WorkloadView.js — 기능3 인력 워크로드. 두 막대(진행 중 / 최근 7일 완료), Task성·VoC성 색 구분,
// 모듈 평균 = 막대 뒤 세로 가이드선 + 헤더행 평균수치. [+] 확장 = 진행중/완료 티켓 리스트
//   (Due·D-day, 완료일시; 진행중=임박순·완료=최근순 정렬).
// 인력 = 본명(displayName 첫 어절) + 개발/운영 뱃지(id 사번 x+숫자/i+숫자). updated: 2026-07-09
import { api } from "../../lib/api.js";
import { moduleColor } from "../../lib/colors.js";
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
// Epic 분포 색 — 건수 많은 순으로 팔레트를 배정(같은 화면 안에서만 일관되면 된다).
const EPIC_COLORS = ["var(--c1)", "var(--c2)", "var(--c3)", "var(--c4)",
                     "var(--c5)", "var(--c6)", "var(--c7)"];
const VOC_COLOR = "var(--ty-story)";      // 사용자 VoC — 전용 Epic 취급이라 고정색
const NONE_COLOR = "var(--border-hi)";    // Epic 없음

export default {
  name: "WorkloadView",
  components: { ProgressBar, TypeBadge, Avatar },
  data() { return { d: null, err: "", open: {}, tkd: {}, actOpen: {}, linePos: {}, metric: "count",
                    mods: {}, modErr: {}, busy: false }; },
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
    // 지금까지 도착한 모듈만 (스케일·평균·합계는 이걸 기준으로 계산)
    loaded() { return (this.d ? this.d.modules : []).map((m) => this.mods[m.module]).filter(Boolean); },
    totals() {
      const t = { p: 0, op: 0, ip: 0, dn: 0 };
      this.loaded.forEach((m) => { t.p += m.peopleCount; t.op += (m.openTotal || 0); t.ip += m.inProgressTotal; t.dn += m.done7dTotal; });
      return t;
    },
    // 완료 실적 계산식은 '완료' 막대에만 적용(진행중은 timespent 가 없어 항상 티켓 수).
    doneUnit() { return this.metric === "hr" ? "h" : "건"; },
    // 막대 스케일 = 전체 인력 최대값. 진행중은 count 고정, 완료는 선택 메트릭.
    scale() {
      let ip = 1, dn = 1;
      this.loaded.forEach((m) => m.people.forEach((p) => {
        ip = Math.max(ip, this.assignedCount(p));   // 진행중 + 미착수
        dn = Math.max(dn, this.barVal(p.done7d, this.metric));
      }));
      return { ip, dn };
    },
    // 모듈 평균(세로선/수치) — 진행중 count, 완료 선택 메트릭
    avgByMod() {
      const out = {};
      const avg = (xs) => xs.length ? Math.round(xs.reduce((a, b) => a + b, 0) / xs.length * 10) / 10 : 0;
      this.loaded.forEach((m) => {
        out[m.module] = { ip: avg(m.people.map((p) => this.assignedCount(p))),
                          dn: avg(m.people.map((p) => this.barVal(p.done7d, this.metric))) };
      });
      return out;
    },
  },
  methods: {
    /** 모듈별 병렬 로딩: 골격 먼저 → 각 모듈 동시 요청 → 도착하는 대로 채움(느린 모듈이 안 막음).
     *  ★ 메서드로 둔다 — hardRefresh 가 this.load() 를 부른다(예전엔 mounted 인라인이라 죽었다). */
    async load() {
      this.err = "";
      try {
        this.d = await api.workloadShell();
        this.d.modules.forEach((m) => { this.open[m.module] = true; });
        this.d.modules.forEach((m) => {
          api.workloadModule(m.module)
            .then((r) => { this.mods[m.module] = r; this.scheduleMeasure(); })
            .catch((e) => { this.modErr[m.module] = e.message; });
        });
      } catch (e) { this.err = e.message; }
    },
    /** 캐시를 비우고 전부 다시 받는다 — 낡은 값으로 화면을 지키는 구조라 사람이 끊을 수단이 필요하다. */
    async hardRefresh() {
      if (this.busy) return;
      this.busy = true;
      try {
        await api.refresh();
        this.d = null; this.mods = {}; this.modErr = {}; this.tkd = {}; this.actOpen = {};
        await this.load();
      } catch (e) {
        this.err = (e && e.message) || "다시 받지 못했습니다.";
      } finally { this.busy = false; }
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
      epics.forEach((g, i) => { g.color = EPIC_COLORS[i % EPIC_COLORS.length]; });
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
    barVal(bar, metric) {   // 세 카테고리 합 (스케일·모듈평균·막대 총합 일치)
      return this.mv(bar, "task", metric) + this.mv(bar, "subtask", metric) + this.mv(bar, "voc", metric);
    },
    // 왼쪽 막대 = 미완료 할당. 정렬: Task→Sub-Task→VoC. 타입당 세그먼트 1개(폭=진행중+할당됨 합),
    // 오른쪽 '할당됨' 비율만 사선 오버레이(hatchFrac) → 숫자(합)는 세그먼트 중앙에 위치.
    segAssigned(p) {
      const ip = p.inProgress || {}, op = p.open || {};
      const kinds = [["task", "Task"], ["subtask", "Sub-Task"], ["voc", "VoC"]];
      const segs = [];
      kinds.forEach(([k, lb]) => {
        const ni = this.mv(ip, k, "count"), no = this.mv(op, k, "count"), c = ni + no;
        segs.push({
          value: c, color: "var(--wl-" + k + ")",
          hatchFrac: c > 0 ? no / c : 0,      // 오른쪽 이 비율만 사선(할당됨)
          label: String(c),
          title: lb + " 진행 중 " + ni + " · 할당됨 " + no + " (합 " + c + ")",
        });
      });
      return segs;
    },
    setMetric(mk) { this.metric = mk; this.scheduleMeasure(); },
    seg(bar, metric) {
      const u = metric === "hr" ? "h" : "건";
      const t = this.mv(bar, "task", metric), s = this.mv(bar, "subtask", metric), v = this.mv(bar, "voc", metric);
      return [
        { value: t, color: "var(--wl-task)", title: "Task " + t + u },
        { value: s, color: "var(--wl-subtask)", title: "Sub-Task " + s + u },
        { value: v, color: "var(--wl-voc)", title: "VoC " + v + u },
      ];
    },
    toggleMod(m) { this.open[m] = !this.open[m]; this.scheduleMeasure(); },
    setBody(mod, el) { if (el) this.bodyRefs[mod] = el; },
    scheduleMeasure() { this.$nextTick(() => this.measureLines()); },
    measureLines() {
      if (!this.d) return;
      const pos = {};
      for (const mod in this.bodyRefs) {
        const body = this.bodyRefs[mod];
        if (!body || !body.getBoundingClientRect || !this.open[mod]) continue;
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
  <div>
    <div v-if="err" class="err">워크로드 데이터를 불러오지 못했습니다: {{ err }}</div>
    <template v-else-if="d">
      <div class="chips">
        <div class="chip">인력 <b>{{ totals.p }}</b>명</div>
        <div class="chip">진행 중 <b>{{ totals.ip }}</b>건</div>
        <div class="chip">할당됨 <b>{{ totals.op }}</b>건</div>
        <div class="chip">최근 7일 완료 <b>{{ totals.dn }}</b>건</div>
      </div>
      <div class="legend wl-legend">
        <span><i class="sw task"></i> Task</span>
        <span><i class="sw subtask"></i> Sub-Task</span>
        <span><i class="sw voc"></i> VoC (Component 사용자 VoC)</span>
        <span><i class="sw solid-sw"></i> 단색 = 진행 중</span>
        <span><i class="sw hatch"></i> 사선 = 할당됨(미착수)</span>
        <span class="muted">· 왼쪽=미완료 할당(타입별 진행 중 + 할당됨), 오른쪽=최근 7일 완료 · 세로선 = 모듈 평균</span>
      </div>

      <div v-for="(m, i) in d.modules" :key="m.module" class="mod">
        <div class="mod-head" :class="{ open: open[m.module] }" @click="toggleMod(m.module)">
          <span class="chev">▸</span><span class="dot" :style="{ background: mcolor(i) }"></span>
          <b>{{ m.module }}</b><span class="pc">인력 {{ m.peopleCount }}</span>
          <span v-if="mods[m.module]" class="agg">진행중 <b>{{ mods[m.module].inProgressTotal }}</b> · 할당됨 <b>{{ mods[m.module].openTotal || 0 }}</b> · 최근7일 완료 <b>{{ mods[m.module].done7dTotal }}</b></span>
          <span v-else-if="modErr[m.module]" class="agg">— 불러오기 실패</span>
          <span v-else class="agg muted">불러오는 중…</span>
        </div>
        <div v-if="open[m.module]" class="mod-body" :ref="(el) => setBody(m.module, el)">
          <div v-if="modErr[m.module]" class="err">모듈을 불러오지 못했습니다: {{ modErr[m.module] }}</div>
          <div v-else-if="!mods[m.module]" class="loading">불러오는 중…</div>
          <div v-else-if="!mods[m.module].people.length" class="empty">등록된 인력이 없습니다 (config/people.yaml)</div>
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
            <template v-for="p in mods[m.module].people" :key="p.id">
              <div class="prow">
                <span class="pname" :title="p.id"><Avatar :user="p.id" :name="p.name" :size="20" /><b>{{ p.name }}</b><span v-if="p.kind" class="kbadge" :class="p.kind">{{ p.kind === 'dev' ? '개발' : '운영' }}</span></span>
                <div v-if="p.error" class="wbars wl-fail" title="이 인력의 집계 조회에 실패했습니다(0 이 아님). 새로고침으로 재시도하세요.">
                  <span class="wl-fail-t">집계 조회 실패 — 새로고침으로 재시도</span>
                </div>
                <div v-else class="wbars">
                  <ProgressBar class="wside" :segments="segAssigned(p)" :scale="scale.ip" show-total dark-text />
                  <ProgressBar class="wside" :segments="seg(p.done7d, metric)" :scale="scale.dn" show-total dark-text />
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
                            <span v-if="t.epic" class="ebadge" :style="{ '--ec': epicColorOf(p.id, t) }"
                                  :title="'Epic: ' + (t.epicName || t.epic)">◆ {{ t.epicName || t.epic }}</span>
                            <span v-else-if="t.voc" class="ebadge voc">◆ 사용자 VoC</span>
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
      </div>
      <div class="fab">
        <div class="fab-panel">
          <div class="t">완료 실적 계산식</div>
          <div class="fab-seg">
            <button :class="{ on: metric === 'count' }" @click="setMetric('count')">Task 수</button>
            <button :class="{ on: metric === 'hr' }" @click="setMetric('hr')">소요시간</button>
          </div>
        </div>
      </div>
    </template>
    <div v-else class="loading page">불러오는 중…</div>
  </div>`,
};
