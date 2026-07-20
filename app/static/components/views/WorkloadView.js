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

export default {
  name: "WorkloadView",
  components: { ProgressBar, TypeBadge, Avatar },
  data() { return { d: null, err: "", open: {}, tkd: {}, actOpen: {}, linePos: {}, metric: "count", mods: {}, modErr: {} }; },
  created() { this.bodyRefs = {}; },   // 비반응 DOM 참조(모듈 body)
  async mounted() {
    // 모듈별 병렬 로딩: 골격 먼저 → 각 모듈 동시 요청 → 도착하는 대로 채움(느린 모듈이 안 막음).
    // 막대 스케일/모듈평균은 도착한 모듈까지로 계산되고, 남은 모듈이 오면 자연스럽게 갱신된다.
    try {
      this.d = await api.workloadShell();
      this.d.modules.forEach((m) => { this.open[m.module] = true; });
      this.d.modules.forEach((m) => {
        api.workloadModule(m.module)
          .then((r) => { this.mods[m.module] = r; this.scheduleMeasure(); })
          .catch((e) => { this.modErr[m.module] = e.message; });
      });
    } catch (e) { this.err = e.message; }
    this._onResize = () => this.scheduleMeasure();
    window.addEventListener("resize", this._onResize);
  },
  unmounted() { if (this._onResize) window.removeEventListener("resize", this._onResize); },
  activated() { this.scheduleMeasure(); },   // keep-alive 재활성 시 평균선 재측정
  computed: {
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
        const box = { open: null, inProgress: null, done7d: null, err: {} };
        this.tkd[id] = box;
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
                <div v-else class="tcols">
                  <div>
                    <div class="sec-t">진행 중 · 할당됨 <b>{{ (tkd[p.id].inProgress || []).length + (tkd[p.id].open || []).length }}</b></div>
                    <div v-if="tkd[p.id].inProgress === null" class="loading">진행 중 불러오는 중…</div>
                    <template v-else-if="tkd[p.id].inProgress.length">
                      <div class="sub-lbl">진행 중 <b>{{ tkd[p.id].inProgress.length }}</b></div>
                      <div v-for="t in tkd[p.id].inProgress" :key="'ip-' + t.key" class="wtk tkt"
                           :data-key="t.key" role="button" tabindex="0" :title="t.key + ' · ' + t.summary">
                        <TypeBadge :type="t.type" /><span class="ky">{{ t.key }}</span>
                        <span class="sm">{{ t.summary }}</span>
                        <span class="sched">
                          <span v-if="t.due" class="dbadge"><svg class="ic" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><rect x="3.5" y="5" width="17" height="16" rx="2"/><path d="M3.5 9.5h17M8 3v4M16 3v4"/></svg>Due {{ fy(t.due) }}</span>
                          <span v-else class="dbadge nodue">마감 설정되지 않음</span>
                          <span v-if="t.due" class="dchip" :class="ddCls(t.due)"><svg class="ic" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.4" stroke-linecap="round"><circle cx="12" cy="12" r="9"/><path d="M12 7.5V12l3 1.8"/></svg>{{ dd(t.due) }}</span>
                        </span>
                      </div>
                    </template>
                    <div v-if="tkd[p.id].open === null" class="loading">할당됨 불러오는 중…</div>
                    <template v-else-if="tkd[p.id].open.length">
                      <div class="sub-lbl todo">할당됨 (미착수) <b>{{ (tkd[p.id].open || []).length }}</b></div>
                      <div v-for="t in (tkd[p.id].open || [])" :key="'op-' + t.key" class="wtk todo tkt"
                           :data-key="t.key" role="button" tabindex="0" :title="t.key + ' · ' + t.summary">
                        <TypeBadge :type="t.type" /><span class="ky">{{ t.key }}</span>
                        <span class="sm">{{ t.summary }}</span>
                        <span class="sched">
                          <span v-if="t.due" class="dbadge"><svg class="ic" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><rect x="3.5" y="5" width="17" height="16" rx="2"/><path d="M3.5 9.5h17M8 3v4M16 3v4"/></svg>Due {{ fy(t.due) }}</span>
                          <span v-else class="dbadge nodue">마감 설정되지 않음</span>
                          <span v-if="t.due" class="dchip" :class="ddCls(t.due)"><svg class="ic" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.4" stroke-linecap="round"><circle cx="12" cy="12" r="9"/><path d="M12 7.5V12l3 1.8"/></svg>{{ dd(t.due) }}</span>
                        </span>
                      </div>
                    </template>
                    <div v-if="tkd[p.id].inProgress && tkd[p.id].open && !tkd[p.id].inProgress.length && !tkd[p.id].open.length" class="muted">진행 중·할당됨 티켓 없음</div>
                  </div>
                  <div>
                    <div class="sec-t">최근 7일 완료 <b>{{ (tkd[p.id].done7d || []).length }}</b></div>
                    <div v-if="tkd[p.id].done7d === null" class="loading">불러오는 중…</div>
                    <div v-for="t in (tkd[p.id].done7d || [])" :key="t.key" class="wtk done tkt"
                         :data-key="t.key" role="button" tabindex="0" :title="t.key + ' · ' + t.summary">
                      <TypeBadge :type="t.type" /><span class="ky">{{ t.key }}</span>
                      <span class="sm">{{ t.summary }}</span>
                      <span class="sched">
                        <span v-if="t.due" class="dbadge"><svg class="ic" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><rect x="3.5" y="5" width="17" height="16" rx="2"/><path d="M3.5 9.5h17M8 3v4M16 3v4"/></svg>Due {{ fy(t.due) }}</span>
                        <span class="dbadge fin"><svg class="ic" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.6" stroke-linecap="round" stroke-linejoin="round"><path d="M4 12.5l5 5 11-11"/></svg>Finished {{ fdt(t.resolved) }}</span>
                      </span>
                    </div>
                    <div v-if="tkd[p.id].done7d && !tkd[p.id].done7d.length" class="muted">최근 7일 완료 티켓 없음</div>
                  </div>
                </div>
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
