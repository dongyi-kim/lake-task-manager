// WbsView.js — 기능1 WBS Dashboard(간트). 게이지/KPI/컨트롤/범례는 Vue 선언형,
// 간트 본문(라벨트리·타임라인 바·트리커넥터)은 검증된 명령형 renderGantt()를 refs 로 구동.
// 4계층 lazy(Epic→Task→Sub-Task), 파일탐색기식 가이드선/[+], 상태색 바. updated: 2026-07-08
import { api } from "../../lib/api.js";
import { moduleColor } from "../../lib/colors.js";

export default {
  name: "WbsView",
  data() { return { D: null, err: "", unit: "month", expanded: {}, epicTree: {}, hideBugVoc: true, _rt: null, _onResize: null }; },
  async mounted() {
    try { this.D = await api.wbs(); this.$nextTick(() => this.renderGantt()); }
    catch (e) { this.err = e.message; }
    this._onResize = () => { clearTimeout(this._rt); this._rt = setTimeout(() => this.renderGantt(), 150); };
    window.addEventListener("resize", this._onResize);
  },
  unmounted() { if (this._onResize) window.removeEventListener("resize", this._onResize); if (this._raf) cancelAnimationFrame(this._raf); },
  activated() { this.$nextTick(() => this.renderGantt()); },   // keep-alive 재활성 시 재렌더
  computed: {
    pmo() { return this.D ? this.D.rollup.pmo : null; },
    kpis() {
      if (!this.pmo) return [];
      const p = this.pmo;
      return [
        { v: p.rawTotalSp, k: "총 Story Point" }, { v: p.rawDoneSp, k: "완료 SP" },
        { v: this.pctStr(p.progressPct), k: "진척률" }, { v: p.rawMockSp, k: "mock SP (추정)", mock: true },
      ];
    },
    gaugeArc() {
      const circ = 2 * Math.PI * 52;
      return { arr: circ, off: this.pmo ? circ * (1 - this.pmo.progressPct / 100) : circ };
    },
    scopeText() {
      if (!this.D) return "";
      const p = this.pmo, T = this.D.timeline;
      return `Modules ${p.moduleCount} · WBS Task ${p.wbsCount} · Epic ${p.epicCount} · 일정 ${T.start} ~ ${T.end}. `
        + "Epic 진척률(Σ완료SP/Σ총SP)만 이 도구의 산출물이며, WBS/Module/PMO 는 이를 가중치로 조합한 롤업입니다.";
    },
  },
  methods: {
    pctStr(n) { return Number(n).toFixed(1) + "%"; },   // 진척률 소수점 1자리 고정
    mcolor(i) { return moduleColor(i); },
    unitLabel(u) { return { day: "일", week: "주", month: "월" }[u]; },
    setUnit(u) { this.unit = u; this.renderGantt(); },
    toggleBugVoc() { this.hideBugVoc = !this.hideBugVoc; this.renderGantt(); },   // 시각 숨김(계산엔 영향 없음)
    expandAll() { this.D.wbs.forEach((w) => { this.expanded[w.id] = true; }); this.renderGantt(); },
    collapseAll() { this.expanded = {}; this.renderGantt(); },
    async refresh() {
      try { await api.refresh(); this.epicTree = {}; this.expanded = {}; this.D = await api.wbs(); this.$nextTick(() => this.renderGantt()); }
      catch (e) { this.err = e.message; }
    },
    loadEpicTree(key) {
      if (this.epicTree[key] !== undefined) return;
      this.epicTree[key] = "loading";
      api.epicTree(key).then((t) => { this.epicTree[key] = t || []; this.scheduleRender(); })
        .catch(() => { this.epicTree[key] = []; this.scheduleRender(); });
    },
    scheduleRender() {   // 여러 epic-tree 도착을 1프레임 1회 렌더로 coalesce
      if (this._raf) return;
      this._raf = requestAnimationFrame(() => { this._raf = null; this.renderGantt(); });
    },

    renderGantt() {
      const D = this.D; if (!D) return;
      const self = this;
      const R = this.$refs;
      const glabels = R.glabels, gscroll = R.gscroll, taxis = R.taxis, tgrid = R.tgrid, trows = R.trows, gtime = R.gtime;
      if (!glabels) return;
      const expanded = this.expanded, epicTree = this.epicTree, unit = this.unit;
      const hideBV = this.hideBugVoc;
      const keep = (n) => !hideBV || (n.type !== "Bug" && n.component !== "사용자 VoC");   // Bug/VoC 시각 숨김

      const pct = (n) => Number(n).toFixed(1) + "%";   // 진척률 소수점 1자리 고정
      const el = (tag, cls, html) => { const e = document.createElement(tag); if (cls) e.className = cls; if (html != null) e.innerHTML = html; return e; };
      const pd = (s) => { const a = s.split("-"); return new Date(+a[0], +a[1] - 1, +a[2]); };
      const DAY = 86400000;
      const days = (a, b) => Math.round((b - a) / DAY);
      const md = (d) => (d.getMonth() + 1) + "/" + d.getDate();
      const now = new Date(); const TODAY = new Date(now.getFullYear(), now.getMonth(), now.getDate());
      const T = D.timeline; const projStart = pd(T.start), projEnd = pd(T.end); const totalDays = days(projStart, projEnd);

      const epicWbsCount = {};
      D.wbs.forEach((w) => { w.epics.forEach((p) => { epicWbsCount[p.epicKey] = (epicWbsCount[p.epicKey] || 0) + 1; }); });
      const wbsById = {}; D.wbs.forEach((w) => { wbsById[w.id] = w; });
      const moduleRollup = {}; D.rollup.modules.forEach((m) => { moduleRollup[m.id] = m; });

      const H = { mod: 40, wbs: 44, epic: 32, task: 28, sub: 26 };
      const TBADGE = { Epic: "Epic", Story: "Story", Task: "Task", Bug: "Bug", "Sub-Task": "Sub", Improvement: "Impr", "New Feature": "Feat" };
      const TCLASS = { Epic: "bEpic", Story: "bStory", Task: "bTask", Bug: "bBug", "Sub-Task": "bSub", Improvement: "bImp", "New Feature": "bImp" };
      const badge = (t) => "<span class='tb " + (TCLASS[t] || "bTask") + "'>" + (TBADGE[t] || t) + "</span>";
      const SCLASS = { todo: "sTodo", inprogress: "sProg", done: "sDone" };
      const spill = (cat, name) => "<span class='spill " + (SCLASS[cat] || "sTodo") + "'>" + (name || cat) + "</span>";
      const fdate = (x) => { if (!x) return "?"; const d = pd(x); return (d.getMonth() + 1) + "/" + d.getDate(); };
      const pxPerDay = (avail) => {
        const fit = avail / totalDays;
        if (unit === "month") return fit;
        if (unit === "week") return Math.max(fit, 3.4);
        return Math.max(fit, 9);
      };

      while (glabels.childNodes.length > 1) glabels.removeChild(glabels.lastChild);
      taxis.innerHTML = ""; tgrid.innerHTML = ""; trows.innerHTML = "";

      const avail = gscroll.clientWidth || (document.querySelector(".wrap").clientWidth - 384);
      const ppd = pxPerDay(avail);
      const W = Math.max(totalDays * ppd, avail);
      gtime.style.width = W + "px";
      const xpx = (d) => days(projStart, d) * ppd;

      const addLine = (x, major) => { const g = el("div", "gl" + (major ? " major" : "")); g.style.left = x + "px"; tgrid.appendChild(g); };
      const addTick = (x, text, isMonth) => { const t = el("div", "tk" + (isMonth ? " mlabel" : ""), text); t.style.left = x + "px"; taxis.appendChild(t); };

      if (unit === "month") {
        let d = new Date(projStart.getFullYear(), projStart.getMonth(), 1);
        while (d <= projEnd) {
          if (d >= projStart) { addLine(xpx(d), true); addTick(xpx(d) + 4, (d.getMonth() + 1) + "월", true); }
          d = new Date(d.getFullYear(), d.getMonth() + 1, 1);
        }
      } else if (unit === "week") {
        let w = new Date(projStart), i = 0;
        while (w <= projEnd) {
          addLine(xpx(w), true);
          if (i % ((ppd * 7 >= 46) ? 1 : 2) === 0) addTick(xpx(w), md(w), false);
          w = new Date(w.getTime() + 7 * DAY); i++;
        }
      } else {
        let dd = new Date(projStart), j = 0;
        while (dd <= projEnd) {
          const isWeek = (j % 7 === 0);
          addLine(xpx(dd), isWeek);
          if (isWeek) addTick(xpx(dd), md(dd), false);
          dd = new Date(dd.getTime() + DAY); j++;
        }
      }
      if (TODAY >= projStart && TODAY <= projEnd) {
        const tl = el("div", "today"); tl.style.left = xpx(TODAY) + "px"; tgrid.appendChild(tl);
        const tt = el("div", "tk todaylbl", "오늘"); tt.style.left = xpx(TODAY) + "px"; tt.style.color = "var(--today)"; tt.style.fontWeight = "700"; taxis.appendChild(tt);
      }

      function schedCells(lr, dates) {
        const d = dates || {};
        lr.appendChild(el("div", "lsd", d.s ? fdate(d.s) : ""));
        lr.appendChild(el("div", "ltil", (d.s || d.e) ? "~" : ""));
        lr.appendChild(el("div", "led", d.e ? fdate(d.e) : ""));
      }
      function guideHTML(o) {
        let h = "";
        (o.anc || []).forEach((cont) => { h += "<span class='gd" + (cont ? " v" : "") + "'></span>"; });
        if (!o.isRoot) h += "<span class='gd node" + (o.isLast ? " last" : "") + "'></span>";
        h += o.hasChildren ? "<span class='tgl'>" + (o.isOpen ? "−" : "+") + "</span>" : "<span class='tgl ph'></span>";
        return h;
      }
      function treeConn(tr, c) {
        if (!c) return;
        if (c.rootDown != null) { const d = el("div", "tcx down"); d.style.left = c.rootDown + "px"; tr.appendChild(d); return; }
        if (c.parentX == null) return;
        (c.ancX || []).forEach((x) => { const v = el("div", "tcx"); v.style.left = x + "px"; tr.appendChild(v); });
        const pv = el("div", "tcx" + (c.isLast ? " half" : "")); pv.style.left = c.parentX + "px"; tr.appendChild(pv);
        if (c.childX > c.parentX) { const e = el("div", "tce"); e.style.left = c.parentX + "px"; e.style.width = (c.childX - c.parentX) + "px"; tr.appendChild(e); }
      }
      function labelRow(level, o) {
        const lr = el("div", "lrow " + level + (o.hasChildren ? " clk" : "") + (o.isOpen ? " open" : ""));
        lr.style.height = H[level] + "px";
        const nm = el("div", "lname");
        nm.innerHTML = guideHTML(o) + "<span class='lc'>" + o.html + "</span>";
        lr.appendChild(nm);
        lr.appendChild(el("div", "lpct", o.pct || ""));
        schedCells(lr, o.dates);
        if (o.hasChildren) lr.addEventListener("click", o.onToggle);
        glabels.appendChild(lr);
      }
      function barRow(level, item) {
        const tr = el("div", "trow " + level); tr.style.height = H[level] + "px";
        treeConn(tr, item.conn);
        if (item.pill) {
          const pp = el("div", "tpill"); pp.style.left = (item.pillX || 0) + "px"; pp.innerHTML = item.pill; pp.title = item.title || ""; tr.appendChild(pp);
        } else if (item.start && item.end) {
          const left = xpx(pd(item.start)); const w = Math.max(xpx(pd(item.end)) - left, 4);
          const stc = item.statusCat ? " st-" + ({ todo: "todo", inprogress: "prog", done: "done" }[item.statusCat] || "todo") : "";
          const bar = el("div", "gbar " + level + "-bar" + stc);
          bar.style.left = left + "px"; bar.style.width = w + "px";
          if (item.color && item.progressPct != null) {
            bar.style.background = "color-mix(in srgb, " + item.color + " 20%, #ffffff)";
            bar.style.borderColor = "color-mix(in srgb, " + item.color + " 55%, #ffffff)";
          }
          if (item.progressPct != null) {
            const fill = el("div", "gfill"); fill.style.width = item.progressPct + "%";
            if (item.color) fill.style.background = item.color;
            bar.appendChild(fill);
          }
          const hasPct = item.progressPct != null;
          const fillRight = hasPct ? left + w * (Math.max(0, Math.min(100, item.progressPct)) / 100) : (left + w);
          const nameRoom = (hasPct ? (fillRight - left) : w) - 12;
          if (nameRoom >= 34) {
            const lab = el("div", "blabel", "<span class='bn'>" + item.name + "</span>");
            lab.style.maxWidth = nameRoom + "px"; bar.appendChild(lab);
          }
          bar.title = item.title || "";
          tr.appendChild(bar);
          if (hasPct) {
            const pc = el("div", "gpct", "<span class='mk'>◀</span>" + pct(item.progressPct));
            pc.style.left = (fillRight + 2) + "px"; tr.appendChild(pc);
          }
        }
        trows.appendChild(tr);
      }
      const emit = (level, l, b) => { labelRow(level, l); barRow(level, b); };
      function msgRow(level, text, o) {
        o = o || {};
        const lr = el("div", "lrow " + level + " msg"); lr.style.height = H[level] + "px";
        const nm = el("div", "lname");
        nm.innerHTML = guideHTML({ anc: o.anc || [], isLast: true, hasChildren: false }) + "<span class='lc'><span class='nm'>" + text + "</span></span>";
        lr.appendChild(nm);
        lr.appendChild(el("div", "lpct", "")); schedCells(lr, null);
        glabels.appendChild(lr);
        const tr = el("div", "trow " + level); tr.style.height = H[level] + "px";
        treeConn(tr, o.conn); trows.appendChild(tr);
      }

      D.modules.forEach((mod, mi) => {
        const m = moduleRollup[mod.id];
        const cvar = "var(--c" + (mi % 7 + 1) + ")";
        // 모듈 진척률 = 소속 WBS Task 진척률의 단순 평균
        const mpct = m.wbsIds.length
          ? m.wbsIds.reduce((a, wid) => a + (wbsById[wid] ? wbsById[wid].progressPct : 0), 0) / m.wbsIds.length : 0;
        const ml = el("div", "lrow mod"); ml.style.height = H.mod + "px";
        ml.appendChild(el("div", "lname",
          "<span class='lc'><span class='mdot' style='background:" + cvar + "'></span>" +
          "<span class='nm'>" + mod.name + "</span><span class='mpct'>" + pct(mpct) + "</span>" +
          "<span class='mc'>" + m.wbsIds.length + " WBS</span></span>"));
        ml.appendChild(el("div", "lpct", "")); schedCells(ml, null);
        glabels.appendChild(ml);
        const mtr = el("div", "trow mod"); mtr.style.height = H.mod + "px"; trows.appendChild(mtr);

        const nw = m.wbsIds.length;
        m.wbsIds.forEach((wid, wi) => {
          const w = wbsById[wid];
          const wOpen = !!expanded[wid];
          const lastW = (wi === nw - 1);
          const wbsX = xpx(pd(w.start));
          emit("wbs", {
            isRoot: true, anc: [], isLast: lastW, hasChildren: w.epics.length > 0, isOpen: wOpen,
            html: "<span class='tag'>" + w.id + "</span><span class='nm'>" + w.name + "</span>",
            pct: pct(w.progressPct), dates: { s: w.start, e: w.end },
            onToggle: () => { expanded[wid] = !expanded[wid]; self.renderGantt(); },
          }, {
            name: w.name, start: w.start, end: w.end, progressPct: w.progressPct, color: cvar,
            title: w.name + "  " + w.start + " ~ " + w.end + "  (" + pct(w.progressPct) + ")",
            conn: (wOpen && w.epics.length) ? { rootDown: wbsX } : null,
          });
          if (!wOpen) return;

          const ne = w.epics.length;
          w.epics.forEach((p, ei) => {
            const ep = D.epics[p.epicKey];
            const epath = wid + "/" + p.epicKey;
            const eOpen = !!expanded[epath];
            const lastE = (ei === ne - 1);
            const epicX = xpx(pd(p.start));
            const shared = epicWbsCount[p.epicKey] > 1 ? "<span class='shared'>×" + epicWbsCount[p.epicKey] + "</span>" : "";
            emit("epic", {
              anc: [], isLast: lastE, hasChildren: true, isOpen: eOpen,
              html: badge("Epic") + "<span class='wt'>" + p.weight + "</span><span class='nm'>" + ep.name + "</span>" + shared,
              pct: pct(ep.progressPct), dates: { s: p.start, e: p.end },
              onToggle: () => { expanded[epath] = !expanded[epath]; if (expanded[epath]) self.loadEpicTree(p.epicKey); self.renderGantt(); },
            }, {
              name: ep.name, start: p.start, end: p.end, progressPct: ep.progressPct, color: cvar,
              title: ep.name + "  " + ep.doneSp + "/" + ep.totalSp + " SP (" + pct(ep.progressPct) + ")" + (ep.mockSp > 0 ? ", mock " + ep.mockSp : ""),
              conn: { parentX: wbsX, ancX: [], isLast: lastE, childX: epicX },
            });
            if (!eOpen) return;

            const eConn = { parentX: epicX, ancX: (lastE ? [] : [wbsX]), isLast: true, childX: epicX + 16 };
            const tree = epicTree[p.epicKey];
            if (tree === undefined) { self.loadEpicTree(p.epicKey); msgRow("task", "· 불러오는 중…", { anc: [!lastE], conn: eConn }); return; }
            if (tree === "loading") { msgRow("task", "· 불러오는 중…", { anc: [!lastE], conn: eConn }); return; }
            if (!tree.length) { msgRow("task", "· 하위 티켓 없음", { anc: [!lastE], conn: eConn }); return; }
            const vtree = tree.filter(keep);
            if (!vtree.length) { msgRow("task", "· 표시할 하위 티켓 없음 (Bug/VoC 숨김)", { anc: [!lastE], conn: eConn }); return; }

            const nt = vtree.length;
            vtree.forEach((t, ti) => {
              const tpath = epath + "/" + t.key;
              const subs = (t.children || []).filter(keep);
              const tOpen = !!expanded[tpath];
              const lastT = (ti === nt - 1);
              const taskX = xpx(pd(t.start));
              emit("task", {
                anc: [!lastE], isLast: lastT, hasChildren: subs.length > 0, isOpen: tOpen,
                html: badge(t.type) + "<span class='nm'>" + t.summary + "</span><span class='tag'>" + t.key + "</span>",
                dates: { s: t.start, e: t.end },
                onToggle: () => { expanded[tpath] = !expanded[tpath]; self.renderGantt(); },
              }, {
                name: t.summary, start: t.start, end: t.end, statusCat: t.statusCat, statusText: t.statusName,
                title: t.key + "  " + t.summary + "  [" + t.statusName + "]",
                conn: { parentX: epicX, ancX: (lastE ? [] : [wbsX]), isLast: lastT, childX: taskX },
              });
              if (!tOpen) return;

              const ns = subs.length;
              const subAncX = []; if (!lastE) subAncX.push(wbsX); if (!lastT) subAncX.push(epicX);
              subs.forEach((s, si) => {
                emit("sub", {
                  anc: [!lastE, !lastT], isLast: (si === ns - 1), hasChildren: false,
                  html: badge(s.type) + "<span class='nm'>" + s.summary + "</span><span class='tag'>" + s.key + "</span>",
                  dates: null,
                }, {
                  pill: spill(s.statusCat, s.statusName), pillX: taskX + 16,
                  title: s.key + "  " + s.summary + "  [" + s.statusName + "]",
                  conn: { parentX: taskX, ancX: subAncX, isLast: (si === ns - 1), childX: taskX + 16 },
                });
              });
            });
          });
        });
      });
    },
  },
  template: `
  <div>
    <div v-if="err" class="err">데이터를 불러오지 못했습니다: {{ err }}</div>
    <template v-else-if="D">
      <p class="note">{{ scopeText }}</p>
      <div class="pmo">
        <div class="gauge">
          <svg viewBox="0 0 120 120" aria-hidden="true">
            <circle cx="60" cy="60" r="52" fill="none" stroke="var(--track)" stroke-width="14"/>
            <circle cx="60" cy="60" r="52" fill="none" stroke="var(--done)" stroke-width="14" stroke-linecap="round"
                    transform="rotate(-90 60 60)" :stroke-dasharray="gaugeArc.arr" :stroke-dashoffset="gaugeArc.off"/>
          </svg>
          <div class="pct">{{ pctStr(pmo.progressPct) }}</div>
          <div class="cap">전체 PMO 진척률<br>(가중 롤업)</div>
        </div>
        <div class="kpis">
          <div v-for="(x, i) in kpis" :key="i" class="kpi"><div class="v" :class="{ mock: x.mock }">{{ x.v }}</div><div class="k">{{ x.k }}</div></div>
        </div>
      </div>

      <div class="bar-head">
        <div><h2>진척률 간트차트</h2><div class="hint">▸ 클릭으로 WBS → Epic → Task → Sub-Task 단계별 펼침. WBS·Epic 은 진척률 바, Task 는 상태색 바, Sub-Task 는 상태만.</div></div>
        <div class="controls">
          <div class="seg"><button v-for="u in ['day','week','month']" :key="u" :class="{ on: unit === u }" @click="setUnit(u)">{{ unitLabel(u) }}</button></div>
          <button class="btn" @click="expandAll">전체 Epic 펼치기</button>
          <button class="btn" @click="collapseAll">전체 접기</button>
          <button class="btn" @click="refresh">↻ 새로고침</button>
        </div>
      </div>

      <div class="gantt">
        <div class="g-labels" ref="glabels"><div class="l-head"><span class="lh-name">Module / WBS / Epic / Task</span><span class="lh-pct">진척</span><span class="lh-sd">시작</span><span class="lh-til">~</span><span class="lh-ed">종료</span></div></div>
        <div class="g-scroll" ref="gscroll"><div class="g-time" ref="gtime"><div class="t-axis" ref="taxis"></div><div class="t-grid" ref="tgrid"></div><div class="t-rows" ref="trows"></div></div></div>
      </div>

      <div class="legend"><span v-for="(mod, mi) in D.modules" :key="mod.id"><i class="sw" :style="{ background: mcolor(mi) }"></i> {{ mod.name }}</span></div>
      <div class="legend">
        <span>WBS·Epic 바 = 일정, 채움 = 진척률, <b>색 = 소속 모듈</b></span>
        <span><i class="tline"></i> 오늘</span>
        <span>Epic = 점선 바 · Task = 상태색 바 · Sub-Task = 상태 pill</span>
        <span><i class="tb bEpic">Epic</i> <i class="tb bStory">Story</i> <i class="tb bTask">Task</i> <i class="tb bBug">Bug</i> <i class="tb bSub">Sub</i> 이슈타입</span>
      </div>
      <div class="footer">완료 판정 = <code>statusCategory == "done"</code>. Bug/Ops·SP=0 은 진척률 제외. mock 라벨 SP 는 분모에만 포함(reconcile 는 사람 판단). 매핑/일정 = <code>config/wbs_config.yaml</code>.</div>
      <div class="fab">
        <button class="fab-btn" :class="{ on: hideBugVoc }" @click="toggleBugVoc" title="Bug·VoC 티켓을 트리에서 숨김 (진척률 계산은 원래 제외)">{{ hideBugVoc ? '☑' : '☐' }} Bug/VoC 숨김</button>
      </div>
    </template>
    <div v-else class="loading">불러오는 중…</div>
  </div>`,
};
