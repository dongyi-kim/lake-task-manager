// VitView.js — 기능2 현안(PMO_VIT). 컬럼: 티켓(상태·타입·담당자/번호·이름/Started·Due(D-day)) ·
//   하위 티켓 수 · 직계 하위 티켓(표: 티켓·상태·시작일·종료일·담당자, 행 클릭→Jira) + [자세히] 트리/코멘트.
// '완료 작업 안 보기' 토글로 직계 완료 티켓 숨김. updated: 2026-07-09
import { api } from "../../lib/api.js";
import { moduleColor, STATUS_ORDER, STATUS_VAR, typeLabel, TYPE_BG } from "../../lib/colors.js";
import { esc, mdISO, ymd, ymdhm, mdhm, tkt, dday } from "../../lib/fmt.js";
import TypeBadge from "../ui/TypeBadge.js";
import StatusPill from "../ui/StatusPill.js";

const KLAB = { created: "생성됨", done: "완료됨", resolved: "해결됨" };

export default {
  name: "VitView",
  components: { TypeBadge, StatusPill },
  data() { return { d: null, err: "", detail: {}, detailOpen: {}, hideDone: false }; },
  async mounted() { try { this.d = await api.vit(); } catch (e) { this.err = e.message; } },
  methods: {
    kids(it) {   // 직계 하위 티켓 — '완료 작업 안 보기' 시 done 제외
      const ch = it.children || [];
      return this.hideDone ? ch.filter((c) => c.statusCategory !== "done") : ch;
    },
    mcolor(i) { return moduleColor(i); },
    md(s) { return mdISO(s); },
    fy(s) { return ymd(s); },
    fdt(s) { return ymdhm(s); },
    dd(s) { return dday(s); },
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
    newsHtml(ev) {
      return `<span class='d'>${ymdhm(ev.date)}</span><span class='act ${ev.kind}'>${KLAB[ev.kind] || ev.kind}</span>`
        + `${tkt(ev.key, this.d.jiraBase)} <span class='sm'>${esc(ev.title || "")}</span>`;
    },
    async toggleDetail(it) {
      this.detailOpen[it.key] = !this.detailOpen[it.key];
      if (this.detailOpen[it.key] && !this.detail[it.key]) {
        try { this.detail[it.key] = await api.vitDetail(it.key); }
        catch (e) { this.detail[it.key] = { tree: [], comments: [], error: e.message }; }
      }
    },
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
        + `<span class='tbadge v-solid' style='background:${TYPE_BG[n.type] || "#3568c4"}'>${typeLabel(n.type)}</span>`
        + `<span class='ky'>${tkt(n.key, this.d.jiraBase)}</span>`
        + `<span class='sm'>${esc(n.summary || "")}</span>`
        + (n.assignee ? `<span class='asg'>${esc(n.assignee)}</span>` : "")
        + `<span class='pill' style='color:${col};border-color:${col}'>${esc(n.status || n.statusCategory)}</span>`
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
  <div>
    <div v-if="err" class="err">현안 데이터를 불러오지 못했습니다: {{ err }}</div>
    <template v-else-if="d">
      <div class="chips">
        <div class="chip" style="background:var(--accent);color:#fff;border-color:transparent"><b>{{ d.summary.total }}</b> 현안 (PMO_VIT)</div>
        <div v-for="(m, i) in d.modules" :key="m.module" class="chip">
          <span class="sw" :style="{ background: mcolor(i) }"></span> {{ m.module }} <b>{{ m.issues.length }}</b>
        </div>
      </div>
      <div class="note" v-if="d.summary.skippedDup">상위가 이미 PMO_VIT 인 자손 현안 {{ d.summary.skippedDup }}건은 중복으로 숨김</div>

      <div v-for="(m, i) in d.modules" :key="m.module" class="vgroup">
        <div class="vg-head"><span class="dot" :style="{ background: mcolor(i) }"></span><b>{{ m.module }}</b><span class="c">{{ m.issues.length }} 현안</span></div>
        <div v-if="!m.issues.length" class="empty">· 현안 없음</div>
        <div v-else class="tbl">
          <div class="vhead"><div>티켓</div><div>하위 티켓 수</div><div class="ch-head"><span>티켓</span><span>상태</span><span>시작일</span><span>종료일</span><span>담당자</span></div><div></div></div>
          <template v-for="it in m.issues" :key="it.key">
            <div class="vrow">
              <div class="c-info">
                <div class="l1">
                  <StatusPill :cat="it.statusCategory" :label="it.status" />
                  <TypeBadge :type="it.type" />
                  <span class="who">{{ it.assignee || "미지정" }}</span>
                </div>
                <div class="l2">
                  <span class="key" v-html="tk(it.key)"></span><span class="summ">{{ it.summary }}</span>
                </div>
                <div class="l3">
                  <span class="dt"><span class="dl">Started</span>{{ fy(startedAt(it)) || "—" }}</span>
                  <span class="dt"><span class="dl">Due</span><span v-if="it.due" :class="{ overdue: dueOverdue(it.due) }">{{ fy(it.due) }} ({{ dd(it.due) }})</span><span v-else>—</span></span>
                </div>
              </div>
              <div class="c-subs">
                <div class="scnt"><span class="lbl"><StatusPill cat="todo" label="Open" /></span><b>{{ (it.statusCounts||{}).open || 0 }}</b></div>
                <div class="scnt"><span class="lbl"><StatusPill cat="inprogress" label="In Progress" /></span><b>{{ (it.statusCounts||{}).inprogress || 0 }}</b></div>
                <div class="scnt"><span class="lbl"><StatusPill cat="done" label="Done" /></span><b>{{ (it.statusCounts||{}).done || 0 }}</b></div>
              </div>
              <div class="c-children">
                <a v-for="c in kids(it)" :key="c.key" class="ctr" :href="jiraUrl(c.key)" target="_blank" rel="noopener" :title="c.key">
                  <div class="ct-tkt"><TypeBadge :type="c.type" /><span class="sm">{{ c.summary }}</span></div>
                  <div><StatusPill :cat="c.statusCategory" :label="c.status" /></div>
                  <div class="dt">{{ fy(c.created) || "—" }}</div>
                  <div class="dt">{{ c.resolved ? fy(c.resolved) : "—" }}</div>
                  <div class="asg">{{ c.assignee || "—" }}</div>
                </a>
                <div v-if="!kids(it).length" class="mini muted">{{ (it.children || []).length ? '표시할 하위 티켓 없음 (완료 숨김)' : '직계 하위 티켓 없음' }}</div>
              </div>
              <div class="c-x"><button class="xbtn" @click="toggleDetail(it)">{{ detailOpen[it.key] ? "접기 ▴" : "자세히 ▾" }}</button></div>
            </div>
            <div v-if="detailOpen[it.key]" class="vrow"><div class="detail">
              <div v-if="!detail[it.key]" class="loading">· 불러오는 중…</div>
              <div v-else class="dcols">
                <div>
                  <div class="sec-t">소속 티켓 트리 ({{ flatTree(detail[it.key].tree).length }}개 · 상태·최근 진척)</div>
                  <div v-for="(r, k) in flatTree(detail[it.key].tree)" :key="k" class="tnode" v-html="treeRowHtml(r)"></div>
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
    <div v-else class="loading">불러오는 중…</div>
  </div>`,
};
