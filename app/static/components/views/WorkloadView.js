// WorkloadView.js — 기능3 인력 워크로드. 두 막대(진행 중 / 최근 7일 완료), Task성·VoC성 색 구분,
// 모듈 평균 = 막대 뒤 세로 가이드선 + 헤더행 평균수치. [+] 확장 = 진행중/완료 티켓 리스트
//   (Due·D-day, 완료일시; 진행중=임박순·완료=최근순 정렬).
// 인력 = 본명(displayName 첫 어절) + 개발/운영 뱃지(id 사번 x+숫자/i+숫자). updated: 2026-07-09
import { api } from "../../lib/api.js";
import { moduleColor } from "../../lib/colors.js";
import { ymd, ymdhm, tkt, dday } from "../../lib/fmt.js";
import ProgressBar from "../ui/ProgressBar.js";
import TypeBadge from "../ui/TypeBadge.js";

export default {
  name: "WorkloadView",
  components: { ProgressBar, TypeBadge },
  data() { return { d: null, err: "", open: {}, tkd: {}, actOpen: {}, linePos: {}, metric: "count" }; },
  created() { this.bodyRefs = {}; },   // 비반응 DOM 참조(모듈 body)
  async mounted() {
    try { this.d = await api.workload(); this.d.modules.forEach((m) => { this.open[m.module] = true; }); this.scheduleMeasure(); }
    catch (e) { this.err = e.message; }
    this._onResize = () => this.scheduleMeasure();
    window.addEventListener("resize", this._onResize);
  },
  unmounted() { if (this._onResize) window.removeEventListener("resize", this._onResize); },
  activated() { this.scheduleMeasure(); },   // keep-alive 재활성 시 평균선 재측정
  computed: {
    totals() {
      const t = { p: 0, ip: 0, dn: 0 };
      if (this.d) this.d.modules.forEach((m) => { t.p += m.peopleCount; t.ip += m.inProgressTotal; t.dn += m.done7dTotal; });
      return t;
    },
    // 완료 실적 계산식은 '완료' 막대에만 적용(진행중은 timespent 가 없어 항상 티켓 수).
    doneUnit() { return this.metric === "hr" ? "h" : "건"; },
    // 막대 스케일 = 전체 인력 최대값. 진행중은 count 고정, 완료는 선택 메트릭.
    scale() {
      let ip = 1, dn = 1;
      if (this.d) this.d.modules.forEach((m) => m.people.forEach((p) => {
        ip = Math.max(ip, this.barVal(p.inProgress, "count"));
        dn = Math.max(dn, this.barVal(p.done7d, this.metric));
      }));
      return { ip, dn };
    },
    // 모듈 평균(세로선/수치) — 진행중 count, 완료 선택 메트릭
    avgByMod() {
      const out = {};
      const avg = (xs) => xs.length ? Math.round(xs.reduce((a, b) => a + b, 0) / xs.length * 10) / 10 : 0;
      if (this.d) this.d.modules.forEach((m) => {
        out[m.module] = { ip: avg(m.people.map((p) => this.barVal(p.inProgress, "count"))),
                          dn: avg(m.people.map((p) => this.barVal(p.done7d, this.metric))) };
      });
      return out;
    },
  },
  methods: {
    mcolor(i) { return moduleColor(i); },
    mv(bar, kind, metric) { return (bar[metric] || {})[kind] || 0; },   // kind: 'task'|'subtask'|'voc'
    barVal(bar, metric) {   // 세 카테고리 합 (스케일·모듈평균·막대 총합 일치)
      return this.mv(bar, "task", metric) + this.mv(bar, "subtask", metric) + this.mv(bar, "voc", metric);
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
        try {
          const d = await api.workloadDetail(id);
          // 진행중: 마감 임박/초과일수록 상위(D-day 오름차순, 마감없음은 맨 뒤)
          d.inProgress.sort((a, b) => this.dueRank(a) - this.dueRank(b));
          // 완료: 최근 완료일수록 상위(resolved 내림차순)
          d.done7d.sort((a, b) => (b.resolved || "").localeCompare(a.resolved || ""));
          this.tkd[id] = d;
        } catch (e) { this.tkd[id] = { inProgress: [], done7d: [], error: e.message }; }
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
        <div class="chip">최근 7일 완료 <b>{{ totals.dn }}</b>건</div>
      </div>
      <div class="legend wl-legend">
        <span><i class="sw task"></i> Task</span>
        <span><i class="sw subtask"></i> Sub-Task</span>
        <span><i class="sw voc"></i> VoC (Component 사용자 VoC)</span>
        <span class="muted">· 왼쪽=진행 중, 오른쪽=최근 7일 완료 · 세로선 = 모듈 평균</span>
      </div>

      <div v-for="(m, i) in d.modules" :key="m.module" class="mod">
        <div class="mod-head" :class="{ open: open[m.module] }" @click="toggleMod(m.module)">
          <span class="chev">▸</span><span class="dot" :style="{ background: mcolor(i) }"></span>
          <b>{{ m.module }}</b><span class="pc">인력 {{ m.peopleCount }}</span>
          <span class="agg">진행중 <b>{{ m.inProgressTotal }}</b> · 최근7일 완료 <b>{{ m.done7dTotal }}</b></span>
        </div>
        <div v-if="open[m.module]" class="mod-body" :ref="(el) => setBody(m.module, el)">
          <div v-if="!m.people.length" class="empty">등록된 인력이 없습니다 (config/people.yaml)</div>
          <template v-else>
            <div class="whead">
              <div class="hl">인력</div>
              <div class="wbars"><div class="wside"><div class="hl">진행 중 (건)</div></div><div class="wside"><div class="hl">최근 7일 완료 ({{ doneUnit }})</div></div></div>
              <div></div>
            </div>
            <template v-if="linePos[m.module]">
              <div class="mavg-line" :style="{ left: linePos[m.module].ipX + 'px', top: linePos[m.module].top + 'px' }"></div>
              <div class="mavg-line" :style="{ left: linePos[m.module].doneX + 'px', top: linePos[m.module].top + 'px' }"></div>
              <div class="mavg-num" :style="{ left: linePos[m.module].ipX + 'px', top: linePos[m.module].hy + 'px' }">모듈 평균 {{ avgByMod[m.module].ip }}건</div>
              <div class="mavg-num" :style="{ left: linePos[m.module].doneX + 'px', top: linePos[m.module].hy + 'px' }">모듈 평균 {{ avgByMod[m.module].dn }}{{ doneUnit }}</div>
            </template>
            <template v-for="p in m.people" :key="p.id">
              <div class="prow">
                <span class="pname" :title="p.id"><b>{{ p.name }}</b><span v-if="p.kind" class="kbadge" :class="p.kind">{{ p.kind === 'dev' ? '개발' : '운영' }}</span></span>
                <div class="wbars">
                  <ProgressBar class="wside" :segments="seg(p.inProgress, 'count')" :scale="scale.ip" show-total dark-text />
                  <ProgressBar class="wside" :segments="seg(p.done7d, metric)" :scale="scale.dn" show-total dark-text />
                </div>
                <button class="plus" @click="toggleAct(p.id)">{{ actOpen[p.id] ? "−" : "+" }}</button>
              </div>
              <div v-if="actOpen[p.id]" class="act">
                <div v-if="!tkd[p.id]" class="loading">불러오는 중…</div>
                <div v-else-if="tkd[p.id].error" class="muted">불러오지 못했습니다: {{ tkd[p.id].error }}</div>
                <div v-else class="tcols">
                  <div>
                    <div class="sec-t">진행 중 <b>{{ tkd[p.id].inProgress.length }}</b></div>
                    <div v-for="t in tkd[p.id].inProgress" :key="t.key" class="wtk">
                      <TypeBadge :type="t.type" /><span class="ky" v-html="tk(t.key)"></span>
                      <span class="sm">{{ t.summary }}</span>
                      <span class="sched">
                        <span v-if="t.due" class="dbadge"><svg class="ic" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><rect x="3.5" y="5" width="17" height="16" rx="2"/><path d="M3.5 9.5h17M8 3v4M16 3v4"/></svg>Due {{ fy(t.due) }}</span>
                        <span v-else class="dbadge nodue">마감 설정되지 않음</span>
                        <span v-if="t.due" class="dchip" :class="ddCls(t.due)"><svg class="ic" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.4" stroke-linecap="round"><circle cx="12" cy="12" r="9"/><path d="M12 7.5V12l3 1.8"/></svg>{{ dd(t.due) }}</span>
                      </span>
                    </div>
                    <div v-if="!tkd[p.id].inProgress.length" class="muted">진행 중 티켓 없음</div>
                  </div>
                  <div>
                    <div class="sec-t">최근 7일 완료 <b>{{ tkd[p.id].done7d.length }}</b></div>
                    <div v-for="t in tkd[p.id].done7d" :key="t.key" class="wtk done">
                      <TypeBadge :type="t.type" /><span class="ky" v-html="tk(t.key)"></span>
                      <span class="sm">{{ t.summary }}</span>
                      <span class="sched">
                        <span v-if="t.due" class="dbadge"><svg class="ic" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><rect x="3.5" y="5" width="17" height="16" rx="2"/><path d="M3.5 9.5h17M8 3v4M16 3v4"/></svg>Due {{ fy(t.due) }}</span>
                        <span class="dbadge fin"><svg class="ic" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.6" stroke-linecap="round" stroke-linejoin="round"><path d="M4 12.5l5 5 11-11"/></svg>Finished {{ fdt(t.resolved) }}</span>
                      </span>
                    </div>
                    <div v-if="!tkd[p.id].done7d.length" class="muted">최근 7일 완료 티켓 없음</div>
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
