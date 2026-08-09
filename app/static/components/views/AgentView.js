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
import { TYPE_BG, typeLabel } from "../../lib/colors.js";
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
    return { authNote: "",
      ready: null,            // null=확인 전 · true=쓸 수 있음 · false=설치/설정 안 됨
      reason: "",             // 못 쓰는 이유(설치 누락 등)
      status: null,           // provider·모델 — 지금 무엇으로 도는지 화면에 보인다
      text: "",
      threadId: "",
      turns: [],              // [{who:"user"|"agent", text, trace, evidence, docs, questions,
                              //   assignments, review, pending, result}]
      busy: false,
      plan: [],               // 진행 체크리스트(스트리밍 중에만) — [{id,label,status,t0,dur,note,details}]
                              // status: pending → run → done, 안 지난 단계는 skip.
                              // 최상위는 **플랜 단계**이고 도구 호출 같은 세부 행위는 details 로
                              // 그 밑에 중첩된다(사용자 피드백: 상위는 플랜별, 세부는 하위로).
      abort: null,
      approving: false,
      settingsOpen: false,
      answers: {},            // 되묻기 폼의 답(qi → 값)
      customOn: {},           // 객관식 질문에서 '직접 입력'을 고른 상태(qi → bool). 우선순위엔 없다
      qDone: {},              // 답을 확정한 질문(qi → bool) — 접혀서 선택만 보인다
      stepsOpen: false,       // 진행 표시 펼침 — 기본은 접힘(현재 단계만)
      previewOn: {},          // 초안 항목별 티켓 미리보기 토글(i → bool)
      epicTrees: {},          // 생성 카드의 계보 컨텍스트(epicKey → children[])
      priorities: [],
      evOpen: {},             // 근거 목록 펼침(턴 ti → bool). 기본 접힘 — 검증할 때만 편다
      sideDraft: -1,          // 우측 패널에 미리보는 **초안 항목 번호**(-1=닫힘). 초안 전용
      convos: [],             // 최근 대화(localStorage) — 좌측 사이드바
      pickedAssignee: {},     // 승인 카드에서 고른 담당자(항목 i → uid)
      cardCustom: {},         // 카드에서 '직접 입력'을 고른 상태(i → bool)
      cardEdit: {},           // 카드 인라인 편집 열림(i → bool) — 제목·본문·라벨·마감 직접 수정
      editBuf: {},            // 편집 버퍼(i → {summary,labels,duedate,priority,epic})
      childBuf: {},           // 자식 편집 버퍼("i-j" → {summary,assignee})
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
    // 카드에서 뭔가 고쳤나 — 승인 버튼 라벨이 "수정한 내용으로 생성"으로 바뀐다.
    hasCardEdits() {
      return Object.keys(this.editBuf).length > 0 || Object.keys(this.childBuf).length > 0;
    },
    // 접힌 헤더에 보이는 요약 — 지금 도는 단계 이름(병렬이면 여럿).
    planHead() {
      const run = this.plan.filter((p) => p.status === "run").map((p) => p.label);
      if (run.length) return run.join(" · ");
      const done = this.plan.filter((p) => p.status === "done").length;
      return done ? `${done}/${this.plan.length} 단계` : "시작 중";
    },
  },
  mounted() {
    this.convos = this.loadConvos();
    // 홈 입력창에서 넘어온 첫 질문 — 새 대화로 바로 시작한다(한 번만 소비).
    try {
      const seed = sessionStorage.getItem("agent:seed");
      const note = sessionStorage.getItem("agent:authNote");
      sessionStorage.removeItem("agent:seed");
      sessionStorage.removeItem("agent:authNote");
      if (note) this.authNote = note;
      if (seed) this.$nextTick(() => { this.reset(); this.text = seed; this.send(); });
    } catch (e) { /* noop */ }
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
  updated() {
    // v-html 재렌더 때마다 — data-filled 마커로 멱등. 뱃지 채움은 비동기라 훅에서 돈다.
    this.augmentBadges();
  },
  unmounted() {
    if (this.abort) this.abort();      // 화면을 떠났는데 서버가 계속 일할 이유가 없다
  },
  methods: {
    md(t, people) { return renderMarkdown(t, people); },
    /** [n] 참조 마커 클릭 — 같은 답변의 참조 칸을 열고 그 항목으로 점프 + 하이라이트. */
    mdClick(e) {
      const mark = e.target.closest && e.target.closest(".ref-mark");
      if (!mark) return;
      e.preventDefault();
      const md = mark.closest(".agent-md");
      if (!md) return;
      const det = md.querySelector("details.agent-refs");
      if (det) det.open = true;
      const item = md.querySelector(`.agent-ref-item[data-ref="${mark.dataset.ref}"]`);
      if (!item) return;
      item.scrollIntoView({ behavior: "smooth", block: "center" });
      item.classList.remove("flash");
      void item.offsetWidth;               // 재트리거 — 같은 항목을 연속 클릭해도 깜빡인다
      item.classList.add("flash");
    },
    /** 답변 속 뱃지 스켈레톤을 실물로 채운다 — 티켓 뱃지는 타입·제목·상태(본문 렌더와 같은
     *  구조), Confluence 뱃지는 URL 슬러그 제목(없으면 서버 og:title). 렌더는 동기, 채움은
     *  비동기 — updated() 훅에서 매번 돌지만 data-filled 마커로 한 번만 손댄다. */
    augmentBadges() {
      const root = this.$el;
      if (!root || !root.querySelectorAll) return;
      root.querySelectorAll(".agent-md a.jira-badge[data-key]:not([data-filled])").forEach((a) => {
        a.dataset.filled = "1";
        const key = a.getAttribute("data-key");
        api.ticketBadge(key).then((b) => {
          if (!b || !a.isConnected) return;
          const tb = a.querySelector(".jb-type"), nm = a.querySelector(".jb-name"),
                mt = a.querySelector(".jb-meta");
          if (!tb || !nm || !mt) return;
          tb.textContent = typeLabel(b.type || "");
          tb.style.setProperty("--tc", TYPE_BG[b.type] || "var(--ty-task)");
          nm.textContent = b.summary || "";
          mt.textContent = b.status || "";
          mt.className = "jb-meta st-" + (b.statusCategory || "todo");
          a.title = key + " " + (b.summary || "");
        }).catch(() => { /* 조회 실패 — 키만 보여도 클릭은 된다 */ });
      });
      root.querySelectorAll(".agent-md a.conf-link[data-conf]:not([data-filled])").forEach((a) => {
        a.dataset.filled = "1";
        const href = a.getAttribute("href") || "";
        const t = a.querySelector(".conf-title");
        if (!t) return;
        // 제목이 URL 그대로면(맨 URL 이었단 뜻) 슬러그 → 서버 제목 순으로 사람 말로 바꾼다.
        if ((t.textContent || "").trim() === href) {
          const m = href.match(/\/pages\/\d+\/([^/?#]+)\/?$/) || href.match(/\/display\/[^/]+\/([^/?#]+)\/?$/);
          if (m) t.textContent = decodeURIComponent(m[1].replace(/\+/g, " "));
          else api.linkTitle(href).then((r) => {
            if (r && r.title && a.isConnected) t.textContent = r.title;
          }).catch(() => { /* noop */ });
        }
      });
    },
    /** 단계 밑에 보여줄 세부 행위 — 접힘: 진행 중 단계의 마지막 한 줄만 / 펼침: 전부. */
    visibleDetails(s) {
      if (this.stepsOpen) return s.details;
      if (s.status === "run" && s.details.length) return [s.details[s.details.length - 1]];
      return [];
    },
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
      if (this.busy && text.trim()) {
        pushToast({ kind: "info", key: "agent-busy",
                    title: "다른 응답이 진행 중입니다 — 완료되면 보낼 수 있습니다" });
        return;
      }
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
      if (this.busy && text) {
        pushToast({ kind: "info", key: "agent-busy",
                    title: "다른 응답이 진행 중입니다 — 완료되면 보낼 수 있습니다" });
        return;
      }
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
      // ── 스트리밍 중에도 다른 대화를 볼 수 있다(사용자 요청 — 막을 이유가 없다).
      // 이 턴의 결과는 **이 배열**(myTurns)에 쌓이고, 화면이 다른 대화로 가 있으면
      // UI 갱신만 건너뛴다. active() = 지금 보고 있는 배열이 이 스트림의 배열인가.
      const myTurns = this.turns;
      let myTid = this.threadId;
      this._live = this._live || {};
      if (myTid) this._live[myTid] = myTurns;
      const active = () => this.turns === myTurns;
      this._abortTid = myTid;      // reset()이 "보고 있는 대화의 스트림만" 끊게
      this.busy = true;
      // 플랜은 planner 가 의도를 정하면 서버가 내려준다 — 그때까지는 첫 단계 하나만.
      this.plan = [{ id: "planner", label: "요청 파악", status: "run",
                     t0: Date.now(), dur: null, note: "", details: [] }];
      this.$nextTick(this.scroll);

      this.abort = agentApi.stream(
        { text, threadId: this.threadId },
        (ev) => {
          if (ev.type === "start") {
            myTid = ev.thread_id || myTid;
            this._live[myTid] = myTurns;
            this._abortTid = myTid;
            if (active()) this.threadId = myTid;
            this.saveConvo(myTid, myTurns);   // 첫 전송 즉시 사이드바에 뜬다
          }
          else if (ev.type === "plan") {
            if (!active()) return;    // 다른 대화를 보는 중 — 진행 표시는 그 대화 것만
            // 의도가 정해졌다 — 앞으로 지날 단계의 체크리스트. 이미 지난 단계(planner)의
            // 상태·소요시간은 보존한다.
            const old = this.plan;
            this.plan = (ev.steps || []).map((st) => {
              const prev = old.find((p) => p.id === st.id);
              return prev || { id: st.id, label: st.label, status: "pending",
                               t0: 0, dur: null, note: "", details: [] };
            });
            this.$nextTick(this.scroll);
          }
          else if (ev.type === "node") {
            if (!active()) return;
            // 단계 하나가 끝났다 — [✓] 로 접고, 건너뛴 단계는 흐리게, 다음 단계를 연다.
            const now = Date.now();
            let i = this.plan.findIndex((p) => p.id === ev.node);
            if (i < 0) {
              this.plan.push({ id: ev.node, label: ev.label, status: "run",
                               t0: now, dur: null, note: "", details: [] });
              i = this.plan.length - 1;
            }
            const s = this.plan[i];
            s.status = "done";
            s.dur = s.t0 ? ((now - s.t0) / 1000).toFixed(1) : null;
            if (ev.note) s.note = ev.note;
            this.plan.forEach((p, j) => { if (j < i && p.status === "pending") p.status = "skip"; });
            const nxt = this.plan.find((p) => p.status === "pending");
            if (nxt) { nxt.status = "run"; nxt.t0 = now; }
            this.$nextTick(this.scroll);
          }
          else if (ev.type === "step") {
            if (!active()) return;
            // 세부 행위(도구 호출·결과) — 소속 단계 밑에 중첩. "웹 검색 — <검색어>" 가
            // 실행 줄이고, 결과가 오면 같은 줄이 "… 완료 — <얻은 것>" 으로 바뀐다.
            const now = Date.now();
            let s = this.plan.find((p) => p.id === (ev.parent || ""));
            if (!s) s = this.plan.find((p) => p.status === "run");
            if (!s) return;
            if (s.status !== "run") { s.status = "run"; s.t0 = s.t0 || now; }
            const d = { text: ev.label + (ev.note ? " — " + ev.note : ""), done: !!ev.done };
            const last = s.details[s.details.length - 1];
            if (ev.done && last && !last.done) s.details.splice(s.details.length - 1, 1, d);
            else s.details.push(d);
            if (s.details.length > 40) s.details.shift();
            this.$nextTick(this.scroll);
          } else if (ev.type === "token") {
            // 최종 답이 만들어지는 **동안** 그 대화의 턴 객체에 자란다 — 다른 대화를 보고
            // 있어도 데이터는 쌓이고, 화면 갱신(스크롤·진행 표시)만 건너뛴다.
            turn.text = (turn.text || "") + (ev.text || "");
            if (!active()) return;
            const r = this.plan.find((p) => p.id === "responder");
            if (r && r.status !== "done" && r.status !== "run") { r.status = "run"; r.t0 = Date.now(); }
            this.$nextTick(this.scroll);
          } else if (ev.type === "error") {
            turn.text = "문제가 생겼습니다 — " + (ev.message || "알 수 없는 오류");
            this.busy = false;
            this.saveConvo(myTid, myTurns);
            if (myTid && this._live) delete this._live[myTid];
          } else if (ev.type === "final") {
            Object.assign(turn, {
              text: ev.reply || "(답변이 비어 있습니다)",
              trace: ev.trace || [], evidence: ev.evidence || [],
              docs: ev.related_docs || [], questions: ev.questions || [],
              assignments: ev.assignments || [], review: ev.review || {},
              pending: ev.pending || null, result: ev.result || null,
              usage: ev.usage || null, people: ev.people || {},
              draftItems: ev.draft_items || [],
            });
            this.busy = false;
            this.saveConvo(myTid, myTurns);
            if (myTid && this._live) delete this._live[myTid];
            if (ev.error) pushToast({ kind: "error", title: ev.error, key: "agent-err" });
            if (!active()) {
              // 다른 대화를 보는 중에 끝났다 — 데이터는 저장됐고, 알림으로만 알린다.
              pushToast({ kind: "success", key: "agent-done-bg",
                          title: "이전 대화의 응답이 완료됐습니다 — 사이드바에서 확인하세요" });
              return;
            }
            if (ev.pending && ev.pending.items) this.loadEpicTree(ev.pending);
            this.pickedAssignee = {}; this.cardCustom = {}; this.previewOn = {};
            this.customOn = {}; this.qDone = {};
            this.cardEdit = {}; this.editBuf = {}; this.childBuf = {};
            // 초안(승인 대기 또는 작성 중)이 오면 우측 미리보기를 **자동으로** 연다 —
            // 생성 컨텍스트 내내 옆에서 자라는 것을 본다.
            const nItems = ((ev.pending && ev.pending.items) || ev.draft_items || []).length;
            this.sideDraft = nItems ? Math.min(this.sideDraft < 0 ? 0 : this.sideDraft, nItems - 1) : -1;
            this.plan = [];
            this.$nextTick(this.scroll);
          }
        });
    },

    // ── 카드 인라인 편집 ────────────────────────────────────────
    toggleEdit(i, it) {
      if (this.cardEdit[i]) { this.cardEdit[i] = false; return; }
      // 버퍼는 열 때 원본으로 채운다 — 취소(다시 닫기)하면 버퍼가 무시된다.
      this.editBuf[i] = {
        summary: it.summary || "", labels: (it.labels || []).join(", "),
        duedate: it.duedate || "", priority: it.priority || "", epic: it.epic || "",
      };
      const t = this.turns[this.turns.length - 1];
      this.childrenFor(t, i).forEach((c, j) => {
        this.childBuf[i + "-" + j] = { summary: c.summary || "", assignee: c.assignee || "" };
      });
      this.cardEdit[i] = true;
    },
    childrenFor(t, i) {
      return ((t.pending && t.pending.children) || []).filter((c) => (c.parent_index || 0) === i);
    },
    /** 편집 중이 아니면 원본, 편집을 닫았어도 버퍼가 있으면 버퍼 값(수정 유지 표시). */
    liveVal(i, f, it) {
      const b = this.editBuf[i];
      return b && b[f] != null && String(b[f]).trim() ? b[f] : it[f];
    },
    childVal(i, j, f, c) {
      const b = this.childBuf[i + "-" + j];
      return b && b[f] != null && String(b[f]).trim() ? b[f] : c[f];
    },
    noopSubmit() { return Promise.resolve(); },   // 카드 본문 에디터 — 저장은 승인 때 모아서
    /** 편집 버퍼 → 서버 overrides. 원본과 달라진 것만 보낸다. */
    editOverrides(t) {
      const items = {}, children = {};
      Object.keys(this.editBuf).forEach((i) => {
        const b = this.editBuf[i] || {};
        const orig = (t.pending.items || [])[i] || {};
        const patch = {};
        if (b.summary.trim() && b.summary !== (orig.summary || "")) patch.summary = b.summary.trim();
        if (b.labels !== (orig.labels || []).join(", ")) patch.labels = b.labels;
        if (b.duedate !== (orig.duedate || "")) patch.duedate = b.duedate;
        if (b.priority !== (orig.priority || "")) patch.priority = b.priority;
        if (b.epic !== (orig.epic || "")) patch.epic = b.epic.trim();
        // 본문 — 카드 안 CommentEditor 의 현재 HTML(에디터를 연 적이 있을 때만 존재)
        const ref = this.$refs["ded" + i];
        const inst = Array.isArray(ref) ? ref[0] : ref;
        if (inst && inst._ed) {
          const html = inst._ed.getHTML();
          if (html && html !== (orig.description || "")) patch.description = html;
        }
        if (Object.keys(patch).length) items[i] = patch;
      });
      Object.keys(this.childBuf).forEach((k) => {
        const [i, j] = k.split("-").map(Number);
        const orig = this.childrenFor(t, i)[j] || {};
        const b = this.childBuf[k] || {};
        const patch = {};
        if ((b.summary || "").trim() && b.summary !== (orig.summary || "")) patch.summary = b.summary.trim();
        if ((b.assignee || "") !== (orig.assignee || "")) patch.assignee = b.assignee;
        if (Object.keys(patch).length) children[k] = patch;
      });
      return { items, children };
    },

    async approve() {
      const p = this.pending;
      if (!p || this.approving) return;
      this.approving = true;
      try {
        const last = this.turns[this.turns.length - 1];
        const ov = Object.assign({}, last ? this.assigneeOverrides(last) : null);
        const ed = last ? this.editOverrides(last) : { items: {}, children: {} };
        if (Object.keys(ed.items).length) ov.items = ed.items;
        if (Object.keys(ed.children).length) ov.children = ed.children;
        const r = await agentApi.approve(this.threadId, p.token,
                                         Object.keys(ov).length ? ov : null);
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
      // 새 대화 — 지금 **보고 있는** 대화의 스트림만 끊는다. 다른 대화가 백그라운드로
      // 응답 중이면 그대로 이어 간다(완료되면 자기 대화에 저장 + 토스트).
      if (this.busy && this.abort && this._abortTid === this.threadId) {
        this.abort(); this.abort = null; this.busy = false;
      }
      this.threadId = ""; this.turns = []; this.plan = [];
      this.sideDraft = -1;
    },
    /** 우측 패널이 미리보는 초안 턴 — 승인 대기(pending) 또는 **작성 중**(draftItems).
     *  생성 컨텍스트가 시작되면 패널이 뜨고, 되묻기에 답할 때마다 내용이 갱신된다. */
    draftTurn() {
      const last = this.turns[this.turns.length - 1];
      if (!last || last.who !== "agent") return null;
      const items = (last.pending && last.pending.items) || last.draftItems || [];
      return items.length ? last : null;
    },
    sideItems() {
      const t = this.draftTurn();
      return t ? ((t.pending && t.pending.items) || t.draftItems || []) : [];
    },
    sideItem() { return this.sideItems()[this.sideDraft] || {}; },
    sidePendingReady() {
      const t = this.draftTurn();
      return !!(t && t.pending && (t.pending.items || []).length);
    },

    // ── 최근 대화(좌측 사이드바) — localStorage 보관. 서버 체크포인터는 재시작하면
    // 사라지므로 **표시용 기록**은 브라우저가 갖는다(이어서 질문하면 서버 컨텍스트가
    // 살아 있는 동안은 그대로 이어진다).
    loadConvos() {
      try { return JSON.parse(localStorage.getItem("agentConvos") || "[]"); }
      catch (e) { return []; }
    },
    saveConvo(tid, arr) {
      // 기본은 지금 보는 대화 — 백그라운드 스트림은 자기 tid·배열을 명시해 저장한다
      // (다른 대화를 보는 중에 this.turns 로 저장하면 남의 대화에 덮어쓴다).
      tid = tid || this.threadId;
      arr = arr || this.turns;
      if (!tid || !arr.length) return;
      const first = arr.find((t) => t.who === "user");
      const title = ((first && first.text) || "새 대화").slice(0, 42);
      const rest = this.convos.filter((c) => c.id !== tid);
      // 직렬화 가능한 것만 — 함수·프록시 없음. 30개 초과는 오래된 것부터 버린다.
      this.convos = [{ id: tid, title, at: Date.now(),
                       turns: JSON.parse(JSON.stringify(arr)) }, ...rest].slice(0, 30);
      try { localStorage.setItem("agentConvos", JSON.stringify(this.convos)); } catch (e) {}
    },
    openConvo(c) {
      // 스트리밍 중에도 다른 대화를 볼 수 있다(사용자 요청). 진행 중인 대화로 돌아오면
      // **라이브 배열**을 다시 잡아 실시간 갱신이 이어진다(_live 에 스트림이 등록해 둔다).
      const live = this._live && this._live[c.id];
      this.threadId = c.id;
      this.turns = live || JSON.parse(JSON.stringify(c.turns || []));
      this.plan = []; this.sideKey = "";
      this.$nextTick(this.scroll);
    },
    removeConvo(c) {
      this.convos = this.convos.filter((x) => x.id !== c.id);
      try { localStorage.setItem("agentConvos", JSON.stringify(this.convos)); } catch (e) {}
      if (this.threadId === c.id) this.reset();
    },

    isTicketKey(k) { return /^[A-Z][A-Z0-9]*-[0-9]+$/.test(String(k || "")); },
    /** 대화 제목 — 첫 사용자 발화. 사이드바 목록과 같은 규칙이라 헷갈리지 않는다. */
    convoTitle() {
      const first = this.turns.find((t) => t.who === "user");
      return ((first && first.text) || "새 대화").slice(0, 60);
    },

    /** 대화 전체를 마크다운으로 클립보드에 — 피드백 전달용(사용자 요청). */
    async exportChat() {
      const L = [];
      this.turns.forEach((t) => {
        if (t.who === "user") { L.push(`Q: ${t.text}`); return; }
        L.push(`A: ${t.text || "(본문 없음)"}`);
        (t.questions || []).forEach((q) => L.push(`  [질문:${q.kind}${q.field ? "/" + q.field : ""}] ${q.question}`
          + ((q.options || []).length ? ` — 보기: ${q.options.join(" | ")}` : "")));
        const items = (t.pending && t.pending.items) || t.draftItems || [];
        items.forEach((it) => L.push(`  [초안] ${it.type} ${it.summary}`
          + (it.epic ? ` (상위 ${it.epic})` : "") + (it.assignee ? ` 담당 ${it.assignee}` : "")));
        (t.assignments || []).forEach((a) => L.push(`  [담당추천] ${a.user} — ${(a.reasons || []).join("; ")}`));
        if (t.result && (t.result.created || []).length)
          L.push(`  [생성됨] ` + t.result.created.map((c) => c.key).join(", "));
        if (t.result && (t.result.failed || []).length)
          L.push(`  [실패] ` + t.result.failed.map((f) => `${f.summary}: ${f.error}`).join(" / "));
        if (t.usage) L.push(`  [사용량] ${t.usage.calls || "?"}회 · ${t.usage.totalTokens || 0}tok · $${t.usage.costUsd || 0}`);
        L.push("");
      });
      const text = `# LTM Agent 대화 (${new Date().toLocaleString()})\n\n` + L.join("\n");
      try {
        await navigator.clipboard.writeText(text);
        pushToast({ kind: "success", key: "agent-export", title: "대화를 클립보드에 복사했습니다" });
      } catch (e) {
        pushToast({ kind: "error", key: "agent-export", title: "복사 실패 — 브라우저 권한을 확인하세요" });
      }
    },

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
    /** 지금 펼쳐 보일 질문 — 아직 확정 안 된 첫 번째. 순차 등장의 축이다. */
    qActive(turn) {
      const qs = turn.questions || [];
      for (let i = 0; i < qs.length; i++) if (!this.qDone[i]) return i;
      return -1;
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
    /** 다중선택 — ' | ' 로 이어 붙인다(백엔드는 자연어 답으로 그대로 읽는다). */
    toggleMulti(qi, opt) {
      const k = this.qKey(qi);
      const cur = (this.answers[k] || "").split(" | ").filter(Boolean);
      const i = cur.indexOf(opt);
      if (i >= 0) cur.splice(i, 1); else cur.push(opt);
      this.answers[k] = cur.join(" | ");
    },
    isPicked(qi, q, opt) {
      const a = this.answers[this.qKey(qi)] || "";
      if (q.kind === "multi") return a.split(" | ").includes(opt);
      return !this.customOn[qi] && a === opt;
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

      <!-- 홈에서 넘어올 때 인증이 안 됐으면 그 사실을 대화 위에 계속 보여 준다 -->
      <div v-if="authNote" class="agent-authnote">
        <span>⚠ {{ authNote }}</span>
        <button class="an-x" @click="authNote = ''" title="닫기">✕</button>
      </div>

      <!-- 대화 헤더 — 제목(첫 질문) + 우상단 액션(내보내기). 빈 화면에는 없다 -->
      <div v-if="turns.length" class="agent-chat-h">
        <b class="agent-chat-title" :title="convoTitle()">{{ convoTitle() }}</b>
        <div class="agent-chat-acts">
          <button @click="exportChat" title="대화 전체를 마크다운으로 클립보드에 복사">📋 대화 복사</button>
        </div>
      </div>

      <div class="agent-scroll" ref="scroller" @click="mdClick">
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
              <!-- 클로드식 순차 폼: 질문은 한 번에 하나씩, 답한 질문은 접혀 선택만 보인다
                   (세로 카드형 보기가 스크롤을 먹는 것의 절충 — 사용자 요청). -->
              <div v-for="(q, qi) in t.questions" :key="qi" class="aq"
                   v-show="qDone[qi] || qi === qActive(t)">

                <!-- 접힌 질문 — 질문 한 줄 + 선택한 답. 누르면 다시 편다 -->
                <button v-if="qDone[qi] && qActive(t) !== qi" class="aq-folded"
                        @click="qDone[qi] = false; answers[qKey(qi)] = answers[qKey(qi)] || ''">
                  <span class="aq-fq">{{ q.question || q }}</span>
                  <b>{{ answers[qKey(qi)] }}</b><em>수정</em>
                </button>

                <template v-else>
                  <div class="aq-q">{{ q.question || q }}
                    <span class="aq-step">{{ qi + 1 }}/{{ t.questions.length }}</span></div>

                  <!-- 세로 카드형 보기 (추천 맨 위) + '직접 입력' 카드(인라인 즉시 입력).
                       kind=multi 는 토글 다중선택 + [선택 완료] -->
                  <div v-if="optionsFor(q).length" class="aq-opts">
                    <button v-for="(opt, oi) in optionsFor(q)" :key="opt" class="aq-card"
                            :class="{ on: isPicked(qi, q, opt), rec: oi === 0, multi: q.kind === 'multi' }"
                            @click="customOn[qi] = false;
                                    q.kind === 'multi' ? toggleMulti(qi, opt)
                                                       : (pickOpt(qi, opt), qDone[qi] = true)">
                      <i v-if="q.kind === 'multi'" class="aq-chk">{{ isPicked(qi, q, opt) ? '☑' : '☐' }}</i>
                      <span>{{ opt }}</span><em v-if="oi === 0">추천</em></button>
                    <button v-if="q.kind === 'multi'" class="aq-card aq-multi-done"
                            :disabled="!(answers[qKey(qi)] || '').trim()"
                            @click="qDone[qi] = true">
                      선택 완료 ({{ (answers[qKey(qi)] || '').split(' | ').filter(Boolean).length }}개)</button>
                    <div v-if="q.field !== 'priority'" class="aq-card aq-custom"
                         :class="{ on: customOn[qi] }" @click="customOn[qi] = true">
                      <span v-if="!customOn[qi]">직접 입력…</span>
                      <template v-else>
                        <FieldEdit v-if="fieldOf(q)" class="aq-fe" ticket="__agent__"
                                   :field="fieldOf(q)" local :value="answers[qKey(qi)] || ''"
                                   @pick="(v, x) => { setAns(qi, v, x); qDone[qi] = true; }">
                          {{ answers[qKey(qi)] || feHint(q) }}</FieldEdit>
                        <input v-else class="aq-in" :value="answers[qKey(qi)] || ''"
                               placeholder="답을 입력하고 Enter" autofocus
                               @input="setAns(qi, $event.target.value)"
                               @keydown.enter.stop.prevent="answers[qKey(qi)] && (qDone[qi] = true)"
                               @click.stop>
                      </template>
                    </div>
                  </div>

                  <!-- 보기 없는 질문: 날짜·담당자·Epic 은 FieldEdit, 그 외 자유 서술 -->
                  <div v-else-if="fieldOf(q)">
                    <FieldEdit class="aq-fe" ticket="__agent__" :field="fieldOf(q)" local
                               :value="answers[qKey(qi)] || ''"
                               @pick="(v, x) => { setAns(qi, v, x); qDone[qi] = true; }">
                      {{ answers[qKey(qi)] || feHint(q) }}</FieldEdit>
                  </div>
                  <input v-else class="aq-in" :value="answers[qKey(qi)] || ''"
                         placeholder="답을 입력하고 Enter" @input="setAns(qi, $event.target.value)"
                         @keydown.enter.stop.prevent="answers[qKey(qi)] && (qDone[qi] = true)">
                </template>
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
              <template v-if="t.pending.action === 'update_ticket' || t.pending.action === 'update_tickets'">
                <div class="agent-card-h">
                  <b v-if="t.pending.keys">일괄 변경 {{ t.pending.keys.length }}건</b>
                  <b v-else><a href="#" class="tkt" :data-key="t.pending.key">{{ t.pending.key }}</a> 변경</b>
                  <em>아직 바뀌지 않았습니다 — 확인 후 승인하세요</em>
                </div>
                <!-- 일괄 대상 — 전부 보여야 승인이 의미 있다(각 키 클릭 검증 가능) -->
                <div v-if="t.pending.keys" class="agent-chg-keys">
                  <a v-for="k in t.pending.keys" :key="k" href="#" class="tkt" :data-key="k">{{ k }}</a>
                </div>
                <div v-if="t.pending.rationale" class="agent-card-why">{{ t.pending.rationale }}</div>
                <div class="agent-chg">
                  <div v-for="(v, k) in t.pending.changes" :key="k" class="agent-chg-row">
                    <span class="chg-k">{{ ({assignee:'담당자', duedate:'마감일', priority:'우선순위',
                                            summary:'제목', labels:'라벨', status:'상태 전이', link:'링크'})[k] || k }}</span>
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
                    <span v-if="!cardEdit[i]" class="ai-sum">{{ liveVal(i, 'summary', it) }}</span>
                    <input v-else class="ai-edit-sum" v-model="editBuf[i].summary"
                           placeholder="제목" />
                    <button class="ai-edit-btn" :class="{ on: cardEdit[i] }"
                            @click="toggleEdit(i, it)" title="이 항목을 카드에서 직접 수정">
                      {{ cardEdit[i] ? '수정 중' : '✎ 수정' }}</button>
                  </div>
                  <!-- 인라인 편집 — 승인 전에 제목·본문·라벨·마감·우선순위·Epic 을 카드에서
                       직접 고친다(사용자 요청: 수정 루프). 서버가 같은 규칙으로 재검증한다. -->
                  <div v-if="cardEdit[i]" class="ai-edit">
                    <label>라벨 <input v-model="editBuf[i].labels" placeholder="쉼표로 구분" /></label>
                    <label>마감 <input v-model="editBuf[i].duedate" type="date" /></label>
                    <label>우선순위
                      <select v-model="editBuf[i].priority">
                        <option value="">(없음)</option>
                        <option v-for="p in priorities" :key="p" :value="p">{{ p }}</option>
                      </select></label>
                    <label>Epic <input v-model="editBuf[i].epic" placeholder="DL-123 (비우면 최상위)" /></label>
                    <div class="ai-edit-desc">
                      <div class="ai-edit-desc-h">본문 (저장은 [이대로 생성] 때 함께)</div>
                      <CommentEditor :ref="'ded' + i" ticket-key="" kind="description"
                                     :initial="it.description || ''" :hide-footer="true"
                                     :submit-fn="noopSubmit" />
                    </div>
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
                  <!-- 함께 만들어질 Sub-Task — 안 보이면 부모 하나만 승인한 줄 안다 -->
                  <div v-if="childrenFor(t, i).length" class="ai-kids">
                    <div class="ai-kids-h">함께 만들 Sub-Task {{ childrenFor(t, i).length }}건</div>
                    <div v-for="(c, j) in childrenFor(t, i)" :key="j" class="ai-kid">
                      └ <template v-if="!cardEdit[i]"><b>{{ childVal(i, j, 'summary', c) }}</b>
                        <span v-if="childVal(i, j, 'assignee', c)" class="ai-who">
                          <Avatar :user="childVal(i, j, 'assignee', c)"
                                  :name="personName(t, childVal(i, j, 'assignee', c))" :size="14" />
                          {{ personName(t, childVal(i, j, 'assignee', c)) || childVal(i, j, 'assignee', c) }}
                        </span></template>
                      <template v-else>
                        <input class="ai-kid-sum" v-model="childBuf[i + '-' + j].summary" />
                        <FieldEdit class="aq-fe" ticket="__agent__" field="assignee" local
                                   :value="childBuf[i + '-' + j].assignee || ''"
                                   @pick="(v) => { childBuf[i + '-' + j].assignee = v; }">
                          {{ childBuf[i + '-' + j].assignee || '담당…' }}</FieldEdit>
                      </template>
                    </div>
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
                  {{ approving ? '만드는 중…' : (hasCardEdits ? '수정한 내용으로 생성' : '이대로 생성') }}
                </button>
                <button class="ag-cancel" :disabled="approving" @click="cancelPending">취소하고 수정 요청</button>
                <em class="agent-card-hint">✎ 수정으로 카드에서 직접 고치거나, 채팅에 수정 요청을
                  적으면 초안을 고쳐 다시 보여 드립니다.</em>
              </div>
              </template>
            </div>
          </div>
        </div>

        <!-- 진행 상황: 최상위는 플랜 단계 체크리스트([✓]/[▸]/[ ]), 세부 행위(도구 호출)는
             각 단계 밑에 중첩. 기본은 진행 중 단계의 마지막 행위 한 줄만 — 펼치면 전부. -->
        <div v-if="busy && plan.length" class="agent-steps">
          <button class="agent-steps-h" @click="stepsOpen = !stepsOpen">
            {{ stepsOpen ? '▾' : '▸' }} 진행 — {{ planHead }}</button>
          <template v-for="s in plan" :key="s.id">
            <div class="agent-step"
                 :class="{ now: s.status === 'run', ok: s.status === 'done', skip: s.status === 'skip' }">
              <span class="smark">{{ s.status === 'done' ? '✓' : s.status === 'run' ? '▸'
                                     : s.status === 'skip' ? '–' : '○' }}</span>
              <b>{{ s.label }}</b>
              <em v-if="s.note && (stepsOpen || s.status !== 'pending')">{{ s.note }}</em>
              <span class="sdur">{{ s.status === 'done' && s.dur ? s.dur + 's'
                                    : s.status === 'run' ? '…' : '' }}</span>
            </div>
            <div v-for="(d, j) in visibleDetails(s)" :key="s.id + '-' + j"
                 class="agent-substep" :class="{ run: !d.done }">ㄴ {{ d.text }}</div>
          </template>
        </div>
      </div>

      <!-- 입력 — 클로드식 미니멀 채팅 박스. 밑은 코멘트 에디터지만(멘션·/jira·/confluence
           팝업과 뱃지 렌더 재사용) 툴바 등 크롬은 CSS 로 걷어냈다 — 채팅에 서식 메뉴는
           과하다(사용자 지적). 하단 아이콘 줄이 세 기능의 입구다. -->
      <!-- LLM 연결값이 없으면 입력창 대신 안내+[설정] — 눌러 보고 나서야 에러로 아는 것보다
           먼저 말해 주는 것이 낫다. 설정을 닫으면 상태를 다시 확인해 입력창이 살아난다. -->
      <div v-if="status && status.llmReady === false" class="agent-input agent-llmoff">
        <span class="agent-llmoff-msg">⚠ AI 를 쓸 수 없습니다 — {{ status.llmReason || 'LLM 연결이 설정되지 않았습니다.' }}
          <b>연결 확인된 LLM API 가 하나 이상 필요합니다.</b></span>
        <button class="agent-llmoff-btn" @click="settingsOpen = true">설정</button>
      </div>
      <div v-else class="agent-input agent-input-rich" @keydown.capture="onRichKey">
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
          <b>{{ sidePendingReady() ? '만들 티켓 미리보기' : '티켓 초안 (작성 중)' }}</b>
          <span v-if="sideItems().length > 1" class="agent-side-nav">
            <button v-for="(x, xi) in sideItems()" :key="xi"
                    :class="{ on: sideDraft === xi }" @click="sideDraft = xi">{{ xi + 1 }}</button>
          </span>
          <button class="agent-reset" @click="sideDraft = -1" title="닫기">✕</button>
        </div>
        <div class="agent-side-body" v-if="sideItem().summary">
          <div class="ai-ticketview side">
            <div class="tv-head">
              <span class="ai-type">{{ sideItem().type }}</span>
              <b>{{ sideItem().summary }}</b>
            </div>
            <div class="tv-meta">
              <span v-if="sideItem().epic">상위
                <a href="#" class="tkt" :data-key="sideItem().epic">{{ sideItem().epic }}</a></span>
              <span v-if="(sideItem().components || []).length">
                모듈 {{ sideItem().components.join(', ') }}</span>
              <span v-for="lb in (sideItem().labels || [])" :key="lb" class="tv-label">{{ lb }}</span>
              <span v-if="sideItem().priority">{{ sideItem().priority }}</span>
              <span v-if="sideItem().duedate">마감 {{ sideItem().duedate }}</span>
              <span v-if="pickFor(draftTurn(), sideDraft, sideItem())">담당
                <Avatar :user="pickFor(draftTurn(), sideDraft, sideItem())"
                        :name="personName(draftTurn(), pickFor(draftTurn(), sideDraft, sideItem()))"
                        :size="14" />
                {{ personName(draftTurn(), pickFor(draftTurn(), sideDraft, sideItem()))
                   || pickFor(draftTurn(), sideDraft, sideItem()) }}</span>
            </div>
            <div class="ai-desc-html" v-html="descPreview(sideItem().description)"></div>
            <div class="tv-hint">{{ sidePendingReady()
              ? '담당자 변경·승인은 왼쪽 카드에서 합니다 — 선택하면 여기 즉시 반영됩니다.'
              : '아직 작성 중인 초안입니다 — 질문에 답하거나 피드백을 주면 이 내용이 바뀝니다.' }}</div>
          </div>
        </div>
      </div>
    </template>
  </div>`,
};
