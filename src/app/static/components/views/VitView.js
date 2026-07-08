// VitView.js — 기능2 현안(PMO_VIT). 요약 목록 + [자세히] 지연로딩(자손 트리·코멘트).
// 트리: 안내선 + 상태정렬(진행중→ToDo→완료) + 타입/번호/제목/담당자/상태 컬럼. updated: 2026-07-08
import { api } from "../../lib/api.js";
import { moduleColor, STATUS_ORDER, STATUS_VAR, typeLabel } from "../../lib/colors.js";
import { esc, mdISO, ymd, ymdhm, tkt } from "../../lib/fmt.js";
import TypeBadge from "../ui/TypeBadge.js";
import StatusPill from "../ui/StatusPill.js";

const KLAB = { created: "생성됨", done: "완료됨", resolved: "해결됨" };

export default {
  name: "VitView",
  components: { TypeBadge, StatusPill },
  data() { return { d: null, err: "", detail: {}, detailOpen: {} }; },
  async mounted() { try { this.d = await api.vit(); } catch (e) { this.err = e.message; } },
  methods: {
    mcolor(i) { return moduleColor(i); },
    md(s) { return mdISO(s); },
    fy(s) { return ymd(s); },
    tk(key) { return tkt(key, this.d && this.d.jiraBase); },
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
        + `<span class='tbadge v-plain'>${typeLabel(n.type)}</span>`
        + `<span class='ky'>${tkt(n.key, this.d.jiraBase)}</span>`
        + `<span class='sm'>${esc(n.summary || "")}</span>`
        + (n.assignee ? `<span class='asg'>${esc(n.assignee)}</span>` : "")
        + `<span class='pill' style='color:${col};border-color:${col}'>${esc(n.status || n.statusCategory)}</span>`
        + `</span>`
        + `<span class='pg'>${prog}</span>`;
    },
    cmtText(x) { return esc((x.text || "").replace(/\s*[\r\n]+\s*/g, " ").trim()); },
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
          <div class="vhead"><div>티켓</div><div>작업 일정</div><div>진척률 (전체 하위 티켓 기준)</div><div>하위 티켓 수</div><div>최근 하위 소식</div><div></div></div>
          <template v-for="it in m.issues" :key="it.key">
            <div class="vrow">
              <div class="c-info">
                <div class="l1">
                  <TypeBadge :type="it.type" /><span class="key" v-html="tk(it.key)"></span>
                  <StatusPill :cat="it.statusCategory" :label="it.status" />
                </div>
                <div class="summ">{{ it.summary }}</div>
                <div class="who">{{ it.assignee || "미지정" }}</div>
              </div>
              <div class="c-date">
                <span class="dl">Created</span>{{ fy(it.created) || "?" }}<br>
                <span class="dl">Started</span>{{ it.started ? fy(it.started) : "—" }}<br>
                <span class="dl">Updated</span>{{ it.updated ? fy(it.updated) : "?" }}
              </div>
              <div class="c-prog">
                <div class="bar"><i :style="{ width: prog(it).pct + '%', background: mcolor(i) }"></i></div>
                <div class="n"><b>{{ prog(it).done }}/{{ prog(it).total }}</b> ({{ prog(it).pct }}%)</div>
              </div>
              <div class="c-subs">
                <div class="scnt"><span class="lbl"><StatusPill cat="todo" label="Open" /></span><b>{{ (it.statusCounts||{}).open || 0 }}</b></div>
                <div class="scnt"><span class="lbl"><StatusPill cat="inprogress" label="In Progress" /></span><b>{{ (it.statusCounts||{}).inprogress || 0 }}</b></div>
                <div class="scnt"><span class="lbl"><StatusPill cat="done" label="Done" /></span><b>{{ (it.statusCounts||{}).done || 0 }}</b></div>
              </div>
              <div class="c-news">
                <div v-for="(ev, k) in (it.news || []).slice(0, 3)" :key="k" class="mini" v-html="newsHtml(ev)"></div>
                <div v-if="!(it.news || []).length" class="mini muted">-</div>
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
                    <div v-for="(x, k) in detail[it.key].comments" :key="k" class="cmt">
                      <div class="h"><b>{{ x.author || "?" }}</b> · {{ md(x.date) }}</div>
                      <div class="txt" v-html="cmtText(x)"></div>
                    </div>
                  </template>
                  <div v-else class="mini muted">코멘트 없음</div>
                </div>
              </div>
            </div></div>
          </template>
        </div>
      </div>
    </template>
    <div v-else class="loading">불러오는 중…</div>
  </div>`,
};
