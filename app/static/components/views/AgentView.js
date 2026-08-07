// AgentView.js — 메인 페이지(#/home). **업무 착수 어시스턴트와 대화하는 화면.**
//
// 이 화면이 지켜야 하는 것 셋:
//
//  1) **기다리는 동안 무슨 일이 일어나는지 보인다.** 조사에 십수 초가 걸린다. 스피너만 돌면
//     사용자는 멈춘 줄 알고 새로고침한다 — 그러면 진짜로 처음부터다. SSE 로 역할별 진행
//     (요청 파악 → 과거 이력 조사 → …)을 흘려 보여 준다.
//
//  2) **근거를 눌러서 확인할 수 있다.** "DL-118 에서 이미 검토했습니다"라는 문장은 그 티켓을
//     열어 보기 전엔 믿을 수 없다. 답변 안의 티켓 키와 근거 목록이 전부 클릭 가능하다.
//
//  3) **승인 전에는 아무것도 안 만들어진다는 것이 화면에서도 분명하다.** 초안 카드는
//     "아직 만들어지지 않았음"을 제목에 달고, 만들 것을 전부 펼쳐 보인 뒤 [생성]을 받는다.
//     여기서 [생성]을 눌러야만 서버가 쓰기를 시작한다(토큰은 이 카드의 내용에 묶여 있다).
import AgentSettingsDialog from "../ui/AgentSettingsDialog.js";
import Avatar from "../ui/Avatar.js";
import CommentEditor from "../ui/CommentEditor.js";
import FieldEdit from "../ui/FieldEdit.js";
import { agentApi } from "../../lib/agentApi.js";
import { renderMarkdown } from "../../lib/agentMd.js";
import { api } from "../../lib/api.js";
import { pushToast } from "../../lib/toast.js";

// 빈 화면에 예시를 둔다 — 무엇을 할 수 있는 도구인지 설명하는 가장 빠른 방법이고,
// 사용자가 첫 문장을 어떻게 쓸지 몰라 멈추는 것을 막는다.
const EXAMPLES = [
  "실시간 수집 파이프라인에 CDC 방식을 도입해야 한다",
  "적재 배치가 어젯밤부터 계속 실패한다 — Workbench 에서 쿼리 결과가 안 나와",
  "나 오늘 뭐 해야 할까",
  "ETL 모듈 진척률 어떻게 되고 있어?",
  "skcc.x1042 최근 3일간 어떤 업무들을 했어?",
];

// 역할 선택 UI 는 없다 — 매니저 여부는 선택이 아니라 사실이라, 서버가 로그인 사용자로 판별한다.

export default {
  name: "AgentView",
  components: { AgentSettingsDialog, Avatar, CommentEditor, FieldEdit },
  data() {
    return {
      ready: null,            // null=확인 전 · true=쓸 수 있음 · false=설치/설정 안 됨
      reason: "",             // 못 쓰는 이유(설치 누락 등)
      status: null,           // provider·모델 — 지금 무엇으로 도는지 화면에 보인다
      text: "",
      threadId: "",
      turns: [],              // [{who:"user"|"agent", text, trace, evidence, docs, questions,
                              //   assignments, review, pending, result}]
      busy: false,
      steps: [],              // 지금 굴러가는 진행(스트리밍 중에만)
      abort: null,
      approving: false,
      settingsOpen: false,
      answers: {},            // 되묻기 폼의 답(qi → 값)
      customOn: {},           // 객관식 질문에서 '직접 입력'을 고른 상태(qi → bool). 우선순위엔 없다
      previewOn: {},          // 초안 항목별 티켓 미리보기 토글(i → bool)
      epicTrees: {},          // 생성 카드의 계보 컨텍스트(epicKey → children[])
      priorities: [],
      evOpen: {},             // 근거 목록 펼침(턴 ti → bool). 기본 접힘 — 검증할 때만 편다
      sideDraft: -1,          // 우측 패널에 미리보는 **초안 항목 번호**(-1=닫힘). 초안 전용
      convos: [],             // 최근 대화(localStorage) — 좌측 사이드바
      pickedAssignee: {},     // 승인 카드에서 고른 담당자(항목 i → uid)
      cardCustom: {},         // 카드에서 '직접 입력'을 고른 상태(i → bool)
    };
  },
  computed: {
    examples() { return EXAMPLES; },
    empty() { return this.turns.length === 0; },
    // 승인 대기는 **마지막 턴에만** 유효하다. 지난 카드가 계속 눌리면 사용자가 옛 초안을 만든다.
    pending() {
      const last = this.turns[this.turns.length - 1];
      return last && last.who === "agent" && last.pending ? last.pending : null;
    },
  },
  mounted() {
    this.convos = this.loadConvos();
    this.loadPriorities().then((p) => { this.priorities = p; });
    api.prefs()
      .then((p) => {
        this.ready = !!p.agentEnabled;
        this.reason = p.agentReason || "";
        if (this.ready) return agentApi.status().then((s) => { this.status = s; });
      })
      .catch((e) => { this.ready = false; this.reason = (e && e.message) || "확인 실패"; });
    // 답변 안의 티켓 키(`.tkt[data-key]`)는 앱 전역 위임 처리기가 잡는다(기존 모달) —
    // 우측 패널은 **생성 중인 초안**의 미리보기 전용이다(사용자 정정).
  },
  unmounted() {
    if (this.abort) this.abort();      // 화면을 떠났는데 서버가 계속 일할 이유가 없다
  },
  methods: {
    md(t, people) { return renderMarkdown(t, people); },
    use(ex) {
      // 예시를 에디터에 채워 준다 — 바로 보내지 않고 사용자가 고쳐 쓸 수 있게.
      const ed = this.$refs.richEd;
      if (ed && ed._ed) { ed._ed.commands.setContent("<p>" + ex + "</p>"); ed._ed.chain().focus().run(); }
      else this.text = ex;
    },

    onKey(e) {
      // Enter=보내기 / Shift+Enter=줄바꿈. 업무 설명은 여러 줄이 되기 쉬워 줄바꿈을 남겨 둔다.
      if (e.key === "Enter" && !e.shiftKey && !e.isComposing) { e.preventDefault(); this.send(); }
    },

    // ── 리치 입력(코멘트 에디터 재사용) ────────────────────────────
    submitRich() {
      const ed = this.$refs.richEd;
      if (ed && ed.submit) ed.submit();          // submitFn(=sendRich) 로 최종 HTML 이 온다
    },
    async sendRich(html) {
      const text = this.richToText(html);
      if (!text.trim() || this.busy) return;
      const ed = this.$refs.richEd;
      if (ed && ed._ed) ed._ed.commands.clearContent(true);
      this.dispatch(text, html);
    },
    /** 에디터 HTML → 모델에 보낼 텍스트. 멘션은 이름(사번), 뱃지는 제목 텍스트로 푼다 —
     *  모델은 HTML 이 아니라 글을 읽는다. 사번이 남아야 activity·담당 지정이 정확하다. */
    richToText(html) {
      const doc = new DOMParser().parseFromString(String(html || ""), "text/html");
      doc.querySelectorAll("[data-type='mention'],[data-id]").forEach((el) => {
        const id = el.getAttribute("data-id");
        if (id) el.replaceWith((el.textContent || "").replace(/^@?/, "@") + "(" + id + ")");
      });
      doc.querySelectorAll("a").forEach((a) => a.replaceWith(a.getAttribute("title") || a.textContent || ""));
      doc.querySelectorAll("p,li,h1,h2,h3,blockquote").forEach((el) => el.append("\n"));
      doc.querySelectorAll("br").forEach((el) => el.replaceWith("\n"));
      return (doc.body.textContent || "").replace(/\n{3,}/g, "\n\n").trim();
    },
    onRichKey(e) {
      if (e.key === "Enter" && (e.ctrlKey || e.metaKey)) { e.preventDefault(); this.submitRich(); }
    },
    edMention() {
      const ed = this.$refs.richEd;
      if (ed && ed._ed) ed._ed.chain().focus().insertContent("@").run();   // 멘션 팝업 트리거
    },
    edPick(kind) {
      const ed = this.$refs.richEd;
      if (ed && ed.openPick) ed.openPick(kind);
    },

    send() {
      // 예시 버튼 등 텍스트 경로. 에디터 입력은 sendRich 가 이 아래 dispatch 로 합류한다.
      const text = (this.text || "").trim();
      if (!text || this.busy) return;
      this.text = "";
      this.dispatch(text, null);
    },

    dispatch(text, html) {
      this.turns.push({ who: "user", text, html: html || "" });
      const turn = { who: "agent", text: "", trace: [], evidence: [], docs: [],
                     questions: [], assignments: [], review: {}, pending: null, result: null,
                     usage: null };
      this.turns.push(turn);
      this.busy = true;
      this.steps = [];
      this.$nextTick(this.scroll);

      this.abort = agentApi.stream(
        { text, threadId: this.threadId },
        (ev) => {
          if (ev.type === "start") {
            this.threadId = ev.thread_id || this.threadId;
            this.saveConvo();          // 첫 전송 즉시 사이드바에 뜬다 — 답변까지 기다리지 않는다
          }
          else if (ev.type === "node" || ev.type === "step") {
            // 같은 라벨이 연달아 오면 한 줄로 묶는다(도구를 여러 번 부르면 step 이 쏟아진다).
            const last = this.steps[this.steps.length - 1];
            if (last && last.label === ev.label) last.note = ev.note || last.note;
            else this.steps.push({ label: ev.label, note: ev.note || "" });
            this.$nextTick(this.scroll);
          } else if (ev.type === "error") {
            turn.text = "문제가 생겼습니다 — " + (ev.message || "알 수 없는 오류");
            this.busy = false;
          } else if (ev.type === "final") {
            Object.assign(turn, {
              text: ev.reply || "(답변이 비어 있습니다)",
              trace: ev.trace || [], evidence: ev.evidence || [],
              docs: ev.related_docs || [], questions: ev.questions || [],
              assignments: ev.assignments || [], review: ev.review || {},
              pending: ev.pending || null, result: ev.result || null,
              usage: ev.usage || null, people: ev.people || {},
            });
            if (ev.pending && ev.pending.items) this.loadEpicTree(ev.pending);
            this.pickedAssignee = {}; this.cardCustom = {}; this.previewOn = {};
            this.customOn = {};
            // 초안이 오면 우측 미리보기를 **자동으로** 연다 — 만들 실물을 옆에 두고 승인한다.
            this.sideDraft = (ev.pending && (ev.pending.items || []).length) ? 0 : -1;
            this.saveConvo();
                if (ev.error) pushToast({ kind: "error", title: ev.error, key: "agent-err" });
            this.busy = false;
            this.steps = [];
            this.$nextTick(this.scroll);
          }
        });
    },

    async approve() {
      const p = this.pending;
      if (!p || this.approving) return;
      this.approving = true;
      try {
        const last = this.turns[this.turns.length - 1];
        const r = await agentApi.approve(this.threadId, p.token,
                                         last ? this.assigneeOverrides(last) : null);
        if (r && r.ok === false && r.error) {       // 담당자 검증 실패 등 — 카드는 살아 있다
          pushToast({ kind: "error", key: "agent-made", title: r.error });
          return;
        }
        last.pending = null;                        // 카드를 닫는다 — 두 번 눌리면 안 된다
        this.turns.push({ who: "agent", text: r.reply || "", trace: r.trace || [],
                          evidence: [], docs: [], questions: [], assignments: [],
                          review: {}, pending: null, result: r.result || null });
        const made = ((r.result || {}).created || []).length + ((r.result || {}).updated || []).length;
        const bad = ((r.result || {}).failed || []).length;
        if (made) {
          pushToast({ kind: bad ? "error" : "success", key: "agent-made",
                      title: `${made}건 반영했습니다` + (bad ? ` · 실패 ${bad}건` : "") });
        } else {
          pushToast({ kind: "error", key: "agent-made",
                      title: r.error || "만들어진 티켓이 없습니다" });
        }
      } catch (e) {
        pushToast({ kind: "error", key: "agent-made",
                    title: (e && e.message) || "생성에 실패했습니다" });
      } finally {
        this.approving = false;
        this.$nextTick(this.scroll);
      }
    },

    async cancelPending() {
      const p = this.pending;
      if (!p) return;
      await agentApi.cancel(this.threadId, p.token).catch(() => {});
      this.turns[this.turns.length - 1].pending = null;
      this.text = "";
      this.$refs.input && this.$refs.input.focus();
    },

    reset() {
      if (this.abort) this.abort();
      this.threadId = ""; this.turns = []; this.steps = []; this.busy = false;
      this.sideDraft = -1;
    },
    /** 우측 패널이 미리보는 초안 턴 — 승인 대기는 마지막 턴에만 유효하다. */
    draftTurn() {
      const last = this.turns[this.turns.length - 1];
      return last && last.pending && (last.pending.items || []).length ? last : null;
    },

    // ── 최근 대화(좌측 사이드바) — localStorage 보관. 서버 체크포인터는 재시작하면
    // 사라지므로 **표시용 기록**은 브라우저가 갖는다(이어서 질문하면 서버 컨텍스트가
    // 살아 있는 동안은 그대로 이어진다).
    loadConvos() {
      try { return JSON.parse(localStorage.getItem("agentConvos") || "[]"); }
      catch (e) { return []; }
    },
    saveConvo() {
      if (!this.threadId || !this.turns.length) return;
      const first = this.turns.find((t) => t.who === "user");
      const title = ((first && first.text) || "새 대화").slice(0, 42);
      const rest = this.convos.filter((c) => c.id !== this.threadId);
      // 직렬화 가능한 것만 — 함수·프록시 없음. 30개 초과는 오래된 것부터 버린다.
      this.convos = [{ id: this.threadId, title, at: Date.now(),
                       turns: JSON.parse(JSON.stringify(this.turns)) }, ...rest].slice(0, 30);
      try { localStorage.setItem("agentConvos", JSON.stringify(this.convos)); } catch (e) {}
    },
    openConvo(c) {
      if (this.busy) return;
      this.threadId = c.id; this.turns = JSON.parse(JSON.stringify(c.turns || []));
      this.steps = []; this.sideKey = "";
      this.$nextTick(this.scroll);
    },
    removeConvo(c) {
      this.convos = this.convos.filter((x) => x.id !== c.id);
      try { localStorage.setItem("agentConvos", JSON.stringify(this.convos)); } catch (e) {}
      if (this.threadId === c.id) this.reset();
    },

    isTicketKey(k) { return /^[A-Z][A-Z0-9]*-[0-9]+$/.test(String(k || "")); },
    /** 실존 티켓은 기존처럼 전역 모달(TicketDialog)로. 우측 패널은 초안 미리보기 전용. */
    openTicket(key) {
      if (key) window.dispatchEvent(new CustomEvent("lake-open-ticket", { detail: { key } }));
    },
    scroll() { const el = this.$refs.scroller; if (el) el.scrollTop = el.scrollHeight; },
    itemOf(p, i) { return (p.items || [])[i] || {}; },
    /** 초안 description(HTML) → 카드용 읽기 표시. v-html 로 남의 HTML 을 실행하지 않기 위해
     *  구조 표식(제목 ■, 체크박스 ☐, 표 |)만 텍스트로 살리고 태그는 벗긴다. */
    descText(html) {
      let s = String(html || "");
      s = s.replace(/<h3[^>]*>(.*?)<\/h3>/gi, "\n■ $1\n")
           .replace(/<li[^>]*data-checked[^>]*>(.*?)<\/li>/gi, "☐ $1\n")
           .replace(/<li[^>]*>(.*?)<\/li>/gi, "· $1\n")
           .replace(/<tr[^>]*>/gi, "\n| ").replace(/<\/t[dh]>/gi, " | ")
           .replace(/<a[^>]*href="([^"]*)"[^>]*>(.*?)<\/a>/gi, "$2 ($1)")
           .replace(/<\/p>|<br\s*\/?>/gi, "\n")
           .replace(/<[^>]+>/g, "")
           .replace(/&nbsp;/g, " ").replace(/&amp;/g, "&").replace(/&lt;/g, "<").replace(/&gt;/g, ">");
      return s.replace(/\n{3,}/g, "\n\n").trim();
    },
    /** 본문 HTML 미리보기 — v-html 로 넣기 전에 **화이트리스트로 정화**한다.
     *  초안 HTML 은 LLM 산출물이라 script/이벤트 핸들러가 섞일 가능성을 0 으로 못 박는다. */
    descPreview(html) {
      const doc = new DOMParser().parseFromString(String(html || ""), "text/html");
      const ALLOW = new Set(["H3", "P", "UL", "OL", "LI", "TABLE", "THEAD", "TBODY",
                             "TR", "TH", "TD", "A", "B", "STRONG", "EM", "CODE", "BR", "INPUT"]);
      const walk = (node) => {
        for (const el of [...node.children]) {
          // 실행류 태그는 **내용째** 버린다 — 언랩하면 코드 텍스트가 본문처럼 남는다.
          if (["SCRIPT", "STYLE", "IFRAME", "OBJECT", "EMBED", "FORM"].includes(el.tagName)) {
            el.remove(); continue;
          }
          walk(el);
          if (!ALLOW.has(el.tagName)) { el.replaceWith(...el.childNodes); continue; }
          for (const a of [...el.attributes]) {
            const keep = (el.tagName === "A" && a.name === "href" && /^https?:/.test(a.value))
              || (el.tagName === "LI" && ["data-type", "data-checked"].includes(a.name))
              || (el.tagName === "UL" && a.name === "data-type")
              || (el.tagName === "INPUT" && a.name === "type" && a.value === "checkbox");
            if (!keep) el.removeAttribute(a.name);
          }
          if (el.tagName === "A") { el.setAttribute("target", "_blank"); el.setAttribute("rel", "noopener"); }
          // taskList 항목은 체크박스로 보이게
          if (el.tagName === "LI" && el.hasAttribute("data-checked")) {
            const cb = doc.createElement("input");
            cb.type = "checkbox"; cb.disabled = true;
            if (el.getAttribute("data-checked") === "true") cb.checked = true;
            el.prepend(cb);
          }
        }
      };
      walk(doc.body);
      return doc.body.innerHTML;
    },
    togglePreview(i) { this.previewOn[i] = !this.previewOn[i]; },

    /** 생성 카드의 계보 컨텍스트 — 초안이 매달릴 Epic 의 기존 자식들을 불러와
     *  "어디에 어떤 형제들 옆에 붙는지"를 트리로 보여 준다. */
    async loadEpicTree(p) {
      const epics = [...new Set((p.items || []).map((it) => it.epic).filter(Boolean))];
      for (const ek of epics) {
        if (this.epicTrees[ek]) continue;
        try {
          const rows = await fetch("/api/ticket/" + encodeURIComponent(ek) + "/children")
            .then((r) => r.json());
          this.epicTrees[ek] = (rows || []).slice(0, 12).map((c) => ({
            key: c.key, summary: c.summary, done: c.statusCategory === "done",
            type: c.type }));
        } catch (e) { this.epicTrees[ek] = []; }
      }
    },
    treeFor(p, it) { return it.epic ? this.epicTrees[it.epic] : null; },

    reasonsFor(turn, i) {
      const a = (turn.assignments || []).find((x) => x.index === i);
      return a ? a : null;
    },

    /** 사번 → 본명. 지도에 없으면 사번 그대로(이름은 장식이지 조건이 아니다). */
    personName(turn, uid) { return ((turn && turn.people) || {})[uid] || ""; },

    // ── 승인 카드의 담당자 선택 — 추천을 그대로 받는 게 아니라 후보 중 고른다 ──
    pickFor(turn, i, it) {
      if (this.pickedAssignee[i] !== undefined) return this.pickedAssignee[i];
      const a = this.reasonsFor(turn, i);
      return (a && a.user) || it.assignee || "";
    },
    setPick(i, uid) {
      this.pickedAssignee[i] = uid;
      this.cardCustom[i] = false;
    },
    pickCustom(i) { this.cardCustom[i] = true; this.pickedAssignee[i] = ""; },
    /** 승인 시 서버에 넘길 담당자 변경분 — 카드에 보였던 값과 다른 것만. */
    assigneeOverrides(turn) {
      const out = {};
      (turn.pending && turn.pending.items || []).forEach((it, i) => {
        const picked = (this.pickFor(turn, i, it) || "").trim();
        if (picked && picked !== (it.assignee || "")) out[String(i)] = picked;
      });
      return Object.keys(out).length ? { assignees: out } : null;
    },

    // ── 되묻기 폼 ──────────────────────────────────────────────
    // 에이전트의 질문(kind/options/field)을 폼으로 그리고, 답을 모아 **한 문장으로** 보낸다.
    // 백엔드는 자연어 답을 받는 것과 동일 — 폼은 순전히 입력을 쉽게 만드는 층이다.
    qKey(qi) { return "q" + qi; },
    setAns(qi, v, extra) {
      // FieldEdit pick(v, extra) — 사람은 '본명(사번)' 으로 답해 모델도 사람도 읽게 한다.
      let ans = v == null ? "" : String(v);
      if (extra && extra.name && ans) ans = `${extra.name}(${ans})`;
      this.answers[this.qKey(qi)] = ans;
    },
    /** 질문 → FieldEdit 가 다루는 필드명. 아니면 빈 문자열(자유 서술). */
    fieldOf(q) {
      if (q.field === "assignee" || q.field === "epic") return q.field;
      if (q.kind === "date" || q.field === "duedate") return "duedate";
      return "";
    },
    feHint(q) {
      return { assignee: "사람 검색…", epic: "Epic 검색…", duedate: "날짜 선택…" }[this.fieldOf(q)] || "선택…";
    },
    pickOpt(qi, opt) {
      this.answers[this.qKey(qi)] = this.answers[this.qKey(qi)] === opt ? "" : opt;
    },
    async loadPriorities() {
      if (this._pri) return this._pri;
      try {
        const r = await fetch("/api/options/priorities").then((x) => x.json());
        this._pri = (r || []).map((x) => x.name);
      } catch (e) { this._pri = []; }
      return this._pri;
    },
    optionsFor(q) {
      if (q.options && q.options.length) return q.options;
      if (q.field === "priority") return this.priorities;
      return [];
    },
    formReady(turn) {
      // 전부 답할 필요는 없다 — 하나라도 채웠으면 보낼 수 있다("나머지는 알아서" 도 유효한 답).
      return (turn.questions || []).some((q, qi) => (this.answers[this.qKey(qi)] || "").trim());
    },
    submitAnswers(turn) {
      const lines = [];
      (turn.questions || []).forEach((q, qi) => {
        const a = (this.answers[this.qKey(qi)] || "").trim();
        if (a) lines.push((q.question || q) + " → " + a);
      });
      if (!lines.length) return;
      this.answers = {};
      this.text = lines.join("\n");
      this.send();
    },
    skipAnswers() {
      this.answers = {};
      this.text = "나머지는 합리적 기본값으로 알아서 진행해줘";
      this.send();
    },
  },

  template: `
  <div class="agentview">
    <!-- 못 쓰는 상태를 숨기지 않는다 — 왜 안 되는지 알아야 고친다 -->
    <div v-if="ready === false" class="agent-off">
      <h2>AI 에이전트가 켜져 있지 않습니다</h2>
      <p>{{ reason || '설정을 확인하세요.' }}</p>
      <pre>pip install -r requirements-agent.txt</pre>
      <p class="hint">설치 후 앱을 다시 시작하고, 우상단 <b>설정 → AI 에이전트</b> 에서 키를 넣으세요.</p>
    </div>

    <template v-else>
      <!-- 정통 에이전트 레이아웃(사용자 요청): 좌측 사이드바(새 대화·최근 대화·설정) +
           본문. 빈 화면은 중앙 히어로(제목·추천 칩·입력창)로. -->
      <aside class="agent-nav">
        <!-- 모델·설정은 좌상단 — 지금 무엇으로 도는지가 먼저 보인다(사용자 요청) -->
        <div class="an-top">
          <span v-if="status" class="agent-prov" :title="'chat=' + status.chatModel + ' / embed=' + status.embedModel">
            {{ status.provider }}<template v-if="status.chatModel"> · {{ status.chatModel }}</template>
          </span>
          <button class="agent-reset" @click="settingsOpen = true" title="AI 에이전트 설정">⚙ 설정</button>
        </div>
        <button class="an-new" @click="reset">＋ 새 대화</button>
        <div class="an-h" v-if="convos.length">최근 대화</div>
        <div class="an-list">
          <div v-for="c in convos" :key="c.id" class="an-item" :class="{ on: c.id === threadId }">
            <button class="an-open" @click="openConvo(c)" :title="c.title">{{ c.title }}</button>
            <button class="an-del" @click.stop="removeConvo(c)" title="삭제">✕</button>
          </div>
        </div>
      </aside>

      <!-- 이분할: 티켓 패널이 열리면 대화가 좁아지며 나란히 선다 -->
      <div class="agent-main" :class="{ 'is-empty': empty && !busy }">

      <div class="agent-scroll" ref="scroller">
        <!-- 빈 화면: 중앙 히어로 — 제목 + 추천 칩(입력창이 바로 아래 온다) -->
        <div v-if="empty && !busy" class="agent-empty">
          <h1 class="agent-hero">LTM Agent</h1>
          <p class="agent-hero-sub">과거 이력을 찾고, 대화로 구체화해, 승인받아 티켓까지 만듭니다.</p>
          <div class="agent-ex-wrap">
            <button v-for="ex in examples" :key="ex" class="agent-ex" @click="use(ex)">{{ ex }}</button>
          </div>
        </div>

        <div v-for="(t, ti) in turns" :key="ti" class="agent-turn" :class="t.who">
          <!-- 사용자 말풍선 — 에디터로 쓴 턴은 그 HTML 그대로(멘션·티켓 뱃지가 티켓 화면과
               같은 모양으로 보인다). 예시 버튼 등 텍스트 턴은 기존대로. -->
          <div v-if="t.who === 'user' && t.html" class="agent-bubble user rich" v-html="t.html"></div>
          <div v-else-if="t.who === 'user'" class="agent-bubble user">{{ t.text }}</div>

          <div v-else class="agent-bubble agent">
            <div v-if="t.text" class="agent-md" v-html="md(t.text, t.people)"></div>
            <div v-else-if="busy && ti === turns.length - 1" class="agent-thinking">
              <span class="dot"></span><span class="dot"></span><span class="dot"></span>
            </div>

            <!-- 비용 — 질문 하나로 보이지만 안에서 LLM 을 예닐곱 번 부른다.
                 숫자를 봐야 "이건 비싼 질문이었다"를 알고 다음에 다르게 묻는다. -->
            <div v-if="t.usage && t.usage.totalTokens" class="agent-usage"
                 :title="t.usage.model + ' · 입력 ' + t.usage.promptTokens + ' / 출력 ' + t.usage.completionTokens">
              <!-- ★ 이 컴포넌트의 template 은 JS 백틱 문자열이다 — "$" + "{{" 를 붙여 쓰면
                   \`\${\` 로 읽혀 JS 보간이 시작돼 버린다(실제로 파일 전체가 SyntaxError 로 죽었다).
                   달러 기호는 머스태시 안에서 문자열로 만든다. -->
              LLM {{ t.usage.calls }}회 · {{ t.usage.totalTokens.toLocaleString() }} 토큰<template
                v-if="t.usage.costUsd"> · {{ '$' + t.usage.costUsd.toFixed(4) }}</template>
            </div>

            <!-- 근거: 눌러서 확인할 수 있어야 믿을 수 있다. **기본은 접힘**(사용자 요청) —
                 본문이 이미 키+제목을 담고 있어서 근거 목록은 검증하고 싶을 때만 펼친다. -->
            <!-- 근거 — 티켓 키만 클릭 가능. PMO 조회의 근거에는 모듈명("ETL")처럼 티켓이
                 아닌 항목이 섞이는데, 그걸 버튼으로 만들면 눌렀을 때 '없는 티켓'이 뜬다(실측). -->
            <div v-if="t.evidence && t.evidence.length" class="agent-ev">
              <button class="agent-ev-h agent-ev-toggle" @click="evOpen[ti] = !evOpen[ti]">
                {{ evOpen[ti] ? '▾' : '▸' }} 근거 {{ t.evidence.length }}건</button>
              <template v-if="evOpen[ti]">
                <template v-for="e in t.evidence" :key="e.key">
                  <button v-if="isTicketKey(e.key)" class="agent-ev-row"
                          @click="openTicket(e.key)" :title="e.why">
                    <b>{{ e.key }}</b><span>{{ e.title }}</span><em>{{ e.why }}</em>
                  </button>
                  <div v-else class="agent-ev-row plain" :title="e.why">
                    <b>{{ e.key }}</b><span>{{ e.title }}</span><em>{{ e.why }}</em>
                  </div>
                </template>
              </template>
            </div>
            <div v-if="t.docs && t.docs.length" class="agent-docs">
              <div class="agent-ev-h">관련 문서</div>
              <a v-for="d in t.docs" :key="d.url || d.title" :href="d.url || '#'"
                 target="_blank" rel="noopener">{{ d.title }}</a>
            </div>

            <!-- 실행 결과: 실패를 눈에 띄게. 조용히 넘어가면 다 만들어진 줄 안다 -->
            <div v-if="t.result && (t.result.created || []).length" class="agent-made">
              <div class="agent-ev-h">생성됨</div>
              <button v-for="c in t.result.created" :key="c.key" class="agent-ev-row"
                      @click="openTicket(c.key)"><b>{{ c.key }}</b><span>{{ c.summary }}</span></button>
            </div>
            <div v-if="t.result && (t.result.failed || []).length" class="agent-failed">
              <div class="agent-ev-h">실패</div>
              <div v-for="(f, i) in t.result.failed" :key="i">{{ f.summary }} — {{ f.error }}</div>
            </div>

            <!-- 되묻기 폼 — 질문을 타이핑 대신 버튼·자동완성으로 답한다.
                 마지막 턴에만 활성(지난 질문에 답해 봤자 대화는 이미 지나갔다). -->
            <div v-if="t.questions && t.questions.length && ti === turns.length - 1 && !busy"
                 class="agent-qform">
              <div v-for="(q, qi) in t.questions" :key="qi" class="aq">
                <div class="aq-q">{{ q.question || q }}</div>

                <!-- 객관식: 보기 버튼 (추천이 맨 앞) + '직접 입력' 탈출구.
                     ★ 우선순위는 탈출구가 없다 — 허용값이 고정된 필드에 자유 입력을 열면
                     검증에서 튕길 값만 들어온다(사용자 지적: 우선순위는 무조건 객관식).
                     직접 입력 편집기는 티켓 화면과 같은 FieldEdit 를 재사용한다. -->
                <div v-if="optionsFor(q).length" class="aq-opts-wrap">
                  <div class="aq-opts">
                    <button v-for="(opt, oi) in optionsFor(q)" :key="opt"
                            :class="{ on: !customOn[qi] && answers[qKey(qi)] === opt, rec: oi === 0 }"
                            @click="customOn[qi] = false; pickOpt(qi, opt)">{{ opt }}<em v-if="oi === 0">추천</em></button>
                    <button v-if="q.field !== 'priority'" :class="{ on: customOn[qi] }"
                            @click="customOn[qi] = !customOn[qi]; if (customOn[qi]) answers[qKey(qi)] = ''">직접 입력…</button>
                  </div>
                  <template v-if="customOn[qi]">
                    <FieldEdit v-if="fieldOf(q)" class="aq-fe" ticket="__agent__" :field="fieldOf(q)"
                               local :value="answers[qKey(qi)] || ''"
                               @pick="(v, x) => setAns(qi, v, x)">
                      {{ answers[qKey(qi)] || feHint(q) }}</FieldEdit>
                    <input v-else class="aq-in" :value="answers[qKey(qi)] || ''"
                           placeholder="답을 입력하세요" @input="setAns(qi, $event.target.value)">
                  </template>
                </div>

                <!-- 날짜·담당자·Epic — 티켓 화면과 같은 FieldEdit 팝업(규칙·디자인 재사용) -->
                <FieldEdit v-else-if="fieldOf(q)" class="aq-fe" ticket="__agent__" :field="fieldOf(q)"
                           local :value="answers[qKey(qi)] || ''"
                           @pick="(v, x) => setAns(qi, v, x)">
                  {{ answers[qKey(qi)] || feHint(q) }}</FieldEdit>

                <!-- 자유 서술 -->
                <input v-else class="aq-in" :value="answers[qKey(qi)] || ''"
                       placeholder="답을 입력하세요" @input="setAns(qi, $event.target.value)">
              </div>
              <div class="aq-act">
                <button class="ag-ok" :disabled="!formReady(t)" @click="submitAnswers(t)">답변 보내기</button>
                <button class="ag-cancel" @click="skipAnswers()">알아서 진행해줘</button>
              </div>
            </div>

            <!-- ★ HITL 승인 카드 — 여기서 승인을 눌러야만 쓰기가 시작된다.
                 create(티켓 생성)와 update(기존 티켓 변경) 두 모양이 있다. -->
            <div v-if="t.pending && ti === turns.length - 1" class="agent-card">
              <!-- 변경 카드 -->
              <template v-if="t.pending.action === 'update_ticket'">
                <div class="agent-card-h">
                  <b><a href="#" class="tkt" :data-key="t.pending.key">{{ t.pending.key }}</a> 변경</b>
                  <em>아직 바뀌지 않았습니다 — 확인 후 승인하세요</em>
                </div>
                <div v-if="t.pending.rationale" class="agent-card-why">{{ t.pending.rationale }}</div>
                <div class="agent-chg">
                  <div v-for="(v, k) in t.pending.changes" :key="k" class="agent-chg-row">
                    <span class="chg-k">{{ ({assignee:'담당자', duedate:'마감일', priority:'우선순위',
                                            summary:'제목', labels:'라벨'})[k] || k }}</span>
                    <span class="chg-v">{{ Array.isArray(v) ? v.join(', ') : (v || '(비움)') }}</span>
                  </div>
                  <div v-if="t.pending.comment" class="agent-chg-row">
                    <span class="chg-k">코멘트</span><span class="chg-v">{{ t.pending.comment }}</span>
                  </div>
                </div>
                <div class="agent-card-act">
                  <button class="ag-ok" :disabled="approving" @click="approve">
                    {{ approving ? '변경 중…' : '이대로 변경' }}</button>
                  <button class="ag-cancel" :disabled="approving" @click="cancelPending">취소</button>
                </div>
              </template>

              <!-- 생성 카드 -->
              <template v-else>
              <div class="agent-card-h">
                <b>만들 티켓 {{ t.pending.items.length }}건</b>
                <em>아직 만들어지지 않았습니다 — 확인 후 승인하세요</em>
              </div>
              <div v-if="t.pending.rationale" class="agent-card-why">{{ t.pending.rationale }}</div>

              <ol class="agent-items">
                <li v-for="(it, i) in t.pending.items" :key="i">
                  <div class="ai-top">
                    <span class="ai-type">{{ it.type }}</span>
                    <span class="ai-sum">{{ it.summary }}</span>
                  </div>
                  <div class="ai-fields">
                    <span v-if="it.epic">상위 {{ it.epic }}</span>
                    <span v-if="it.parent">부모 {{ it.parent }}</span>
                    <span v-if="it.components">모듈 {{ it.components.join(', ') }}</span>
                    <span v-if="it.labels">라벨 {{ it.labels.join(', ') }}</span>
                    <span v-if="it.duedate">마감 {{ it.duedate }}</span>
                    <span v-if="it.priority">{{ it.priority }}</span>
                    <span v-if="it.assignee" class="ai-who">담당
                      <Avatar :user="it.assignee" :name="personName(t, it.assignee)" :size="15" />
                      {{ personName(t, it.assignee) || it.assignee }}</span>
                  </div>
                  <!-- 본문 요약(구조 텍스트) + 우측 패널 미리보기 열기 — 실물 렌더는
                       우측 채널이 담당한다(사용자 정정: 우측 = 초안 미리보기 공간) -->
                  <div v-if="it.description" class="ai-desc-wrap">
                    <button class="ai-pv-btn" :class="{ on: sideDraft === i }" @click="sideDraft = i">
                      ▸ 우측에 미리보기</button>
                    <div class="ai-desc">{{ descText(it.description) }}</div>
                  </div>
                  <!-- 계보 — 이 초안이 어느 Epic 의 어떤 형제들 옆에 붙는지 -->
                  <div v-if="treeFor(t.pending, it) && treeFor(t.pending, it).length" class="ai-tree">
                    <div class="ai-tree-h">{{ it.epic }} 아래에 붙습니다</div>
                    <div v-for="c in treeFor(t.pending, it)" :key="c.key" class="ai-tree-row"
                         :class="{ done: c.done }">
                      ├ <a href="#" class="tkt" :data-key="c.key">{{ c.key }}</a>
                      <span>{{ c.summary }}</span><em v-if="c.done">완료</em>
                    </div>
                    <div class="ai-tree-row new">└ <b>+ {{ it.summary }}</b> <em>(이번에 생성)</em></div>
                  </div>

                  <!-- 담당자 — 추천을 그대로 받는 게 아니라 **후보 중 고른다**(근거 병기).
                       직접 입력을 고르면 사람 검색 자동완성이 붙는다. -->
                  <div v-if="reasonsFor(t, i)" class="ai-assign">
                    <div class="ai-assign-h">담당자 선택</div>
                    <label class="ai-cand" :class="{ on: !cardCustom[i] && pickFor(t, i, it) === reasonsFor(t, i).user }"
                           @click="setPick(i, reasonsFor(t, i).user)">
                      <span class="ai-cand-who">
                        <Avatar :user="reasonsFor(t, i).user" :name="personName(t, reasonsFor(t, i).user)" :size="22" />
                        <b>{{ personName(t, reasonsFor(t, i).user) || reasonsFor(t, i).user }}</b>
                        <small v-if="personName(t, reasonsFor(t, i).user)">{{ reasonsFor(t, i).user }}</small>
                        <em class="rec">추천</em>
                      </span>
                      <div class="ai-cand-why">
                        <div v-for="(r, ri) in reasonsFor(t, i).reasons" :key="ri">· {{ r }}</div>
                      </div>
                    </label>
                    <label v-for="(alt, ai) in (reasonsFor(t, i).alternates || [])" :key="'a'+ai"
                           class="ai-cand" :class="{ on: !cardCustom[i] && pickFor(t, i, it) === alt.user }"
                           @click="setPick(i, alt.user)">
                      <span class="ai-cand-who">
                        <Avatar :user="alt.user" :name="personName(t, alt.user)" :size="22" />
                        <b>{{ personName(t, alt.user) || alt.user }}</b>
                        <small v-if="personName(t, alt.user)">{{ alt.user }}</small>
                      </span>
                      <div class="ai-cand-why">{{ alt.why }}</div>
                    </label>
                    <label class="ai-cand" :class="{ on: cardCustom[i] }" @click="pickCustom(i)">
                      <b>직접 입력…</b>
                      <!-- 사람 검색은 티켓 화면과 같은 FieldEdit 팝업(규칙·디자인 재사용) -->
                      <div v-if="cardCustom[i]" @click.stop>
                        <FieldEdit class="aq-fe" ticket="__agent__" field="assignee" local
                                   :value="pickedAssignee[i] || ''" :user-id="pickedAssignee[i] || ''"
                                   @pick="(v) => { pickedAssignee[i] = v; }">
                          {{ pickedAssignee[i] || '사람 검색…' }}</FieldEdit>
                      </div>
                    </label>
                  </div>
                </li>
              </ol>

              <div v-if="(t.review.warnings || []).length" class="agent-warn">
                <div v-for="(w, i) in t.review.warnings" :key="i">주의 — {{ w.message }}</div>
              </div>
              <div v-if="(t.review.problems || []).length" class="agent-warn">
                <div v-for="(p, i) in t.review.problems" :key="i">검토 의견 — {{ p.message }}</div>
              </div>

              <div class="agent-card-act">
                <button class="ag-ok" :disabled="approving" @click="approve">
                  {{ approving ? '만드는 중…' : '이대로 생성' }}
                </button>
                <button class="ag-cancel" :disabled="approving" @click="cancelPending">취소하고 수정 요청</button>
              </div>
              </template>
            </div>
          </div>
        </div>

        <!-- 진행 상황: 멈춘 것과 일하는 중을 구분해 준다 -->
        <div v-if="busy && steps.length" class="agent-steps">
          <div v-for="(s, i) in steps" :key="i" class="agent-step" :class="{ now: i === steps.length - 1 }">
            <span class="sdot"></span><b>{{ s.label }}</b><em v-if="s.note">{{ s.note }}</em>
          </div>
        </div>
      </div>

      <!-- 입력 — 클로드식 미니멀 채팅 박스. 밑은 코멘트 에디터지만(멘션·/jira·/confluence
           팝업과 뱃지 렌더 재사용) 툴바 등 크롬은 CSS 로 걷어냈다 — 채팅에 서식 메뉴는
           과하다(사용자 지적). 하단 아이콘 줄이 세 기능의 입구다. -->
      <div class="agent-input agent-input-rich" @keydown.capture="onRichKey">
        <div class="agent-chatbox">
          <CommentEditor ref="richEd" ticketKey="" kind="agentchat" :hideFooter="true"
                         placeholder="하려는 업무를 적어 주세요 — @ 멘션 · '/' 로 티켓·문서"
                         :submitFn="sendRich" />
          <div class="agent-chatbox-bar">
            <button :disabled="busy" @click="edMention" title="사람 멘션 (@)">@</button>
            <button :disabled="busy" @click="edPick('jira')" title="티켓 링크 (/jira)">🎫</button>
            <button :disabled="busy" @click="edPick('confluence')" title="문서 링크 (/confluence)">📄</button>
            <span class="agent-chatbox-space"></span>
            <button class="agent-send-round" :disabled="busy || ready === null"
                    @click="submitRich" title="보내기 (Ctrl+Enter)">{{ busy ? '…' : '↑' }}</button>
          </div>
        </div>
      </div>
      <div class="agent-foot">
        Ctrl+Enter 전송 — <b>승인하기 전에는 아무것도 만들거나 바꾸지 않습니다.</b>
        <a href="#/guide">서비스 안내</a>
      </div>
      </div>
      <AgentSettingsDialog v-if="settingsOpen"
        @close="settingsOpen = false; agentApi.status().then((s) => { status = s; }).catch(() => {})" />

      <!-- 우측 채널 — **생성하려는 초안**의 미리보기 공간(사용자 정정). 만들 실물을 티켓
           모양으로 옆에 두고 카드에서 담당자를 고르며 승인한다. 실존 티켓 클릭은 기존
           전역 모달(TicketDialog)이 그대로 뜬다. -->
      <div v-if="draftTurn() && sideDraft >= 0" class="agent-side">
        <div class="agent-side-h">
          <b>만들 티켓 미리보기</b>
          <span v-if="(draftTurn().pending.items || []).length > 1" class="agent-side-nav">
            <button v-for="(x, xi) in draftTurn().pending.items" :key="xi"
                    :class="{ on: sideDraft === xi }" @click="sideDraft = xi">{{ xi + 1 }}</button>
          </span>
          <button class="agent-reset" @click="sideDraft = -1" title="닫기">✕</button>
        </div>
        <div class="agent-side-body" v-if="itemOf(draftTurn().pending, sideDraft).summary">
          <div class="ai-ticketview side">
            <div class="tv-head">
              <span class="ai-type">{{ itemOf(draftTurn().pending, sideDraft).type }}</span>
              <b>{{ itemOf(draftTurn().pending, sideDraft).summary }}</b>
            </div>
            <div class="tv-meta">
              <span v-if="itemOf(draftTurn().pending, sideDraft).epic">상위
                <a href="#" class="tkt" :data-key="itemOf(draftTurn().pending, sideDraft).epic">
                  {{ itemOf(draftTurn().pending, sideDraft).epic }}</a></span>
              <span v-if="(itemOf(draftTurn().pending, sideDraft).components || []).length">
                모듈 {{ itemOf(draftTurn().pending, sideDraft).components.join(', ') }}</span>
              <span v-for="lb in (itemOf(draftTurn().pending, sideDraft).labels || [])" :key="lb"
                    class="tv-label">{{ lb }}</span>
              <span v-if="itemOf(draftTurn().pending, sideDraft).priority">
                {{ itemOf(draftTurn().pending, sideDraft).priority }}</span>
              <span v-if="itemOf(draftTurn().pending, sideDraft).duedate">
                마감 {{ itemOf(draftTurn().pending, sideDraft).duedate }}</span>
              <span v-if="pickFor(draftTurn(), sideDraft, itemOf(draftTurn().pending, sideDraft))">담당
                <Avatar :user="pickFor(draftTurn(), sideDraft, itemOf(draftTurn().pending, sideDraft))"
                        :name="personName(draftTurn(), pickFor(draftTurn(), sideDraft, itemOf(draftTurn().pending, sideDraft)))"
                        :size="14" />
                {{ personName(draftTurn(), pickFor(draftTurn(), sideDraft, itemOf(draftTurn().pending, sideDraft)))
                   || pickFor(draftTurn(), sideDraft, itemOf(draftTurn().pending, sideDraft)) }}</span>
            </div>
            <div class="ai-desc-html"
                 v-html="descPreview(itemOf(draftTurn().pending, sideDraft).description)"></div>
            <div class="tv-hint">담당자 변경·승인은 왼쪽 카드에서 합니다 — 선택하면 여기 즉시 반영됩니다.</div>
          </div>
        </div>
      </div>
    </template>
  </div>`,
};
