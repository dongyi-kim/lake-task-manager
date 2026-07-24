// VitView.js — 기능2 현안(PMO_VIT). 컬럼: 티켓(상태·타입·담당자/번호·이름/Started·Due(D-day)) ·
//   직계 하위 티켓(표: Sub Task·상태·시작일·종료일·담당자) + [자세히] 트리/코멘트.
//   현안/하위 티켓은 행 전체가 클릭 대상(.tkt[data-key]) → 인앱 티켓 다이얼로그.
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
  data() { return { d: null, err: "", detail: {}, detailOpen: {}, hideDone: false,
                    mods: {}, modErr: {}, modPartial: {}, busy: false }; },
  // 모듈별 병렬 로딩: 골격(shell)을 먼저 그리고 각 모듈을 동시에 요청해 **도착하는 대로** 채운다.
  // (전부 모일 때까지 기다리지 않음 — 느린 모듈이 나머지를 막지 않는다)
  async mounted() { await this.load(); },
  methods: {
    /** 골격(모듈 목록)을 먼저 받고, 모듈별 본문을 병렬로 채운다.
     *  ★ 이 로직이 예전엔 mounted() 안에 인라인이라, hardRefresh 가 this.load() 를 부르면
     *    'this.load is not a function' 이었다(새로고침 버튼이 그래서 죽었다). 메서드로 뺀다. */
    async load() {
      this.err = "";
      try {
        this.d = await api.vitShell();
        this.d.modules.forEach((m) => {
          api.vitModule(m.module)
            .then((r) => {
              this.mods[m.module] = r.issues || [];
              if (r && r.partial) this.modPartial[m.module] = r.missing || 1;
            })
            .catch((e) => { this.modErr[m.module] = e.message; this.mods[m.module] = []; });
        });
      } catch (e) { this.err = e.message; }
    },
    /** 캐시를 비우고 전부 다시 받는다. 화면도 '모른다' 상태로 되돌린 뒤 새로 채운다 —
     *  옛 값을 남겨 두면 무엇이 새로 온 값인지 알 수 없다. */
    async hardRefresh() {
      if (this.busy) return;
      this.busy = true;
      try {
        await api.refresh();
        this.d = null; this.mods = {}; this.modErr = {}; this.modPartial = {};
        this.detail = {}; this.detailOpen = {};
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
        + `<span class='tbadge v-solid' style='--tc:${TYPE_BG[n.type] || "var(--ty-task)"}'>${typeLabel(n.type)}</span>`
        + `<span class='ky'>${esc(n.key)}</span>`
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
          <span class="sw" :style="{ background: mcolor(i) }"></span> {{ m.module }} <b>{{ m.count }}</b>
        </div>
      </div>
      <div class="note" v-if="d.summary.skippedDup">상위가 이미 PMO_VIT 인 자손 현안 {{ d.summary.skippedDup }}건은 중복으로 숨김</div>

      <div class="vtools">
        <!-- 캐시를 비우고 처음부터 다시 받는다. 낡은 값으로 화면을 지키는 구조라(오프라인 대비)
             '뭔가 이상하다' 싶을 때 사람이 직접 끊어 줄 수단이 필요하다. -->
        <button class="btn" :disabled="busy" @click="hardRefresh">
          {{ busy ? '다시 받는 중…' : '↻ 강제 새로고침' }}</button>
      </div>

      <div v-for="(m, i) in d.modules" :key="m.module" class="vgroup">
        <div class="vg-head"><span class="dot" :style="{ background: mcolor(i) }"></span><b>{{ m.module }}</b><span class="c">{{ m.count }} 현안</span></div>
        <div v-if="modErr[m.module]" class="err">· 불러오지 못했습니다: {{ modErr[m.module] }}</div>
        <div v-else-if="!mods[m.module]" class="loading">· 현안과 하위 티켓을 불러오는 중…</div>
        <div v-else-if="!mods[m.module].length" class="empty">· 현안 없음</div>
        <!-- 일부만 왔다 — 목록은 보여 주되 '이게 전부' 라고 말하지 않는다 -->
        <div v-else-if="modPartial[m.module]" class="err">
          · 일부를 불러오지 못했습니다({{ modPartial[m.module] }}건) — 새로고침하세요
        </div>
        <div v-else class="tbl">
          <div class="vhead"><div>티켓</div><div class="ch-head"><span>Sub Task</span><span>상태</span><span>시작일</span><span>종료일</span><span>담당자</span></div><div></div></div>
          <template v-for="it in mods[m.module]" :key="it.key">
            <div class="vrow">
              <div class="c-info">
                <div class="l1">
                  <StatusPill :cat="it.statusCategory" :label="it.status" />
                  <TypeBadge :type="it.type" />
                  <span class="who">{{ it.assignee || "미지정" }}</span>
                </div>
                <div class="l2 tkt" :data-key="it.key" role="button" tabindex="0"
                     :title="it.key + ' · ' + it.summary">
                  <span class="key">{{ it.key }}</span><span class="summ">{{ it.summary }}</span>
                </div>
                <div class="l3">
                  <span class="dt"><span class="dl">Started</span>{{ fy(startedAt(it)) || "—" }}</span>
                  <span class="dt"><span class="dl">Due</span><span v-if="it.due" :class="{ overdue: dueOverdue(it.due) }">{{ fy(it.due) }} ({{ dd(it.due) }})</span><span v-else>—</span></span>
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
                    <div class="ct-tkt"><TypeBadge :type="c.type" /><span class="sm">{{ c.summary }}</span></div>
                    <div><StatusPill :cat="c.statusCategory" :label="c.status" /></div>
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
              <div v-if="!detail[it.key]" class="loading">· 불러오는 중…</div>
              <div v-else class="dcols">
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
    <div v-else class="loading page">불러오는 중…</div>
  </div>`,
};
