// WorkloadView.js — 기능3 인력 워크로드. 두 막대(진행 중 / 최근 7일 완료), Task성·VoC성 색 구분,
// 모듈 평균 = 모듈 전체 막대를 관통하는 단일 세로선(오버레이) + 헤더행 평균수치, [+] 최근활동.
// 인력 = 본명(displayName 첫 어절) + 개발/운영 뱃지(id 사번 x*/i*). ProgressBar 공유. updated: 2026-07-08
import { api } from "../../lib/api.js";
import { moduleColor } from "../../lib/colors.js";
import { ymd, ymdhm, tkt, dday } from "../../lib/fmt.js";
import ProgressBar from "../ui/ProgressBar.js";
import TypeBadge from "../ui/TypeBadge.js";
import StatusPill from "../ui/StatusPill.js";

export default {
  name: "WorkloadView",
  components: { ProgressBar, TypeBadge, StatusPill },
  data() { return { d: null, err: "", open: {}, tkd: {}, actOpen: {}, linePos: {} }; },
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
  },
  methods: {
    mcolor(i) { return moduleColor(i); },
    seg(task, voc) {
      return [
        { value: task, color: "var(--wl-task)", title: "Task성 " + task },
        { value: voc, color: "var(--wl-voc)", title: "VoC성 " + voc },
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
        const m = this.d.modules.find((x) => x.module === mod);
        const xOf = (bar, avg, scale) => {
          const r = bar.getBoundingClientRect();
          const ratio = scale > 0 ? Math.min(avg / scale, 1) : 0;
          return (r.left - bRect.left) + ratio * r.width;
        };
        pos[mod] = {
          ipX: xOf(bars[0], m.avgInProgress, this.d.scaleInProgress),
          doneX: xOf(bars[1], m.avgDone7d, this.d.scaleDone7d),
          top: wRect.bottom - bRect.top,                       // 선 시작 y(헤더 아래)
          hy: (wRect.top + wRect.height / 2) - bRect.top,      // 수치 y(헤더 중앙)
        };
      }
      this.linePos = pos;
    },
    async toggleAct(id) {
      this.actOpen[id] = !this.actOpen[id];
      if (this.actOpen[id] && !this.tkd[id]) {
        try { this.tkd[id] = await api.workloadDetail(id); }
        catch (e) { this.tkd[id] = { inProgress: [], done7d: [], error: e.message }; }
      }
    },
    tk(key) { return tkt(key, this.d && this.d.jiraBase); },
    dueLine(t) { return t.due ? ("Due Date : " + ymd(t.due) + " (" + dday(t.due) + ")") : "마감 설정되지 않음"; },
    doneLine(t) { return t.resolved ? ("완료 " + ymdhm(t.resolved)) : ""; },
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
        <span><i class="sw task"></i> Task성 (Task·Sub-Task)</span>
        <span><i class="sw voc"></i> VoC성 (Component 사용자 VoC)</span>
        <span class="muted">· 왼쪽=진행 중, 오른쪽=최근 7일 완료 · 세로선 = 모듈 평균</span>
      </div>
      <div class="note">막대 최대값 = 전체 최대(진행중 {{ d.scaleInProgress }}건 / 완료 {{ d.scaleDone7d }}건). 세로선 = 해당 모듈 평균. 0은 생략.</div>

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
              <div class="wbars"><div class="wside"><div class="hl">진행 중 (건)</div></div><div class="wside"><div class="hl">최근 7일 완료 (건)</div></div></div>
              <div></div>
            </div>
            <template v-if="linePos[m.module]">
              <div class="mavg-line" :style="{ left: linePos[m.module].ipX + 'px', top: linePos[m.module].top + 'px' }"></div>
              <div class="mavg-line" :style="{ left: linePos[m.module].doneX + 'px', top: linePos[m.module].top + 'px' }"></div>
              <div class="mavg-num" :style="{ left: linePos[m.module].ipX + 'px', top: linePos[m.module].hy + 'px' }">평균 {{ m.avgInProgress }}</div>
              <div class="mavg-num" :style="{ left: linePos[m.module].doneX + 'px', top: linePos[m.module].hy + 'px' }">평균 {{ m.avgDone7d }}</div>
            </template>
            <template v-for="p in m.people" :key="p.id">
              <div class="prow">
                <span class="pname" :title="p.id"><b>{{ p.name }}</b><span v-if="p.kind" class="kbadge" :class="p.kind">{{ p.kind === 'dev' ? '개발' : '운영' }}</span></span>
                <div class="wbars">
                  <ProgressBar class="wside" :segments="seg(p.inProgress.task, p.inProgress.voc)" :scale="d.scaleInProgress" show-total dark-text />
                  <ProgressBar class="wside" :segments="seg(p.done7d.task, p.done7d.voc)" :scale="d.scaleDone7d" show-total dark-text />
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
                      <div class="l1">
                        <TypeBadge :type="t.type" /><span class="ky" v-html="tk(t.key)"></span>
                        <span class="sm">{{ t.summary }}</span>
                        <StatusPill :cat="t.statusCategory" :label="t.status" />
                      </div>
                      <div class="meta" :class="{ nodue: !t.due }">{{ dueLine(t) }}</div>
                    </div>
                    <div v-if="!tkd[p.id].inProgress.length" class="muted">진행 중 티켓 없음</div>
                  </div>
                  <div>
                    <div class="sec-t">최근 7일 완료 <b>{{ tkd[p.id].done7d.length }}</b></div>
                    <div v-for="t in tkd[p.id].done7d" :key="t.key" class="wtk done">
                      <div class="l1">
                        <TypeBadge :type="t.type" /><span class="ky" v-html="tk(t.key)"></span>
                        <span class="sm">{{ t.summary }}</span>
                        <StatusPill :cat="t.statusCategory" :label="t.status" />
                      </div>
                      <div class="meta" :class="{ nodue: !t.due }">{{ dueLine(t) }} <span class="doneat">· {{ doneLine(t) }}</span></div>
                    </div>
                    <div v-if="!tkd[p.id].done7d.length" class="muted">최근 7일 완료 티켓 없음</div>
                  </div>
                </div>
              </div>
            </template>
          </template>
        </div>
      </div>
    </template>
    <div v-else class="loading">불러오는 중…</div>
  </div>`,
};
