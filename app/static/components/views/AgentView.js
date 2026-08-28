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
import { mergeEvidenceMarkdown, renderMarkdown } from "../../lib/agentMd.js";
import { api } from "../../lib/api.js";
import { pushToast } from "../../lib/toast.js";
import {
  NAV_W_KEY, NAV_HIDE_KEY, SIDE_W_KEY, SIDE_HIDE_KEY,
  NAV_MIN, NAV_MAX, SIDE_MIN, SIDE_MAX,
  loadW, loadHidden, saveLS,
} from "../agent/panelLayout.js";
import { augmentAgentBadges } from "../agent/badgeHydration.js";
import {
  richEditorToText, draftDescriptionText, sanitizeDraftDescription,
} from "../agent/contentTransforms.js";
import AGENT_VIEW_TEMPLATE from "../agent/agentViewTemplate.js";

// 빈 화면에 예시를 둔다 — 무엇을 할 수 있는 도구인지 설명하는 가장 빠른 방법이고,
// 사용자가 첫 문장을 어떻게 쓸지 몰라 멈추는 것을 막는다.
// ★ 다섯 개는 **다섯 갈래**를 하나씩 연다(사용자 지정): 생성 · 버그 · 내 일 · 조사 · 팀 현황.
//   예전 목록은 구체적인 문장이라 "내 상황과 다르다"로 읽혔다 — 그러면 칩이 예시가 아니라
//   남의 이야기가 된다. 지금 것은 **의도**를 말하고, 구체는 에이전트가 되물어 채운다.
//   추천 칩은 첫 화면의 유일한 행동 유도라 사용 빈도가 압도적이다 — 배터리에서 이 다섯을
//   따로 재는 이유다(tools/agent_scenarios.py CHIP1~5).
const EXAMPLES = [
  "업무 테스크를 생성하고 싶어",
  "버그를 제보하고 싶어",
  "지금 무슨 업무를 시작해야 할까",
  "특정 주제를 조사하고 싶어 (히스토리, 지식 등)",
  "우리 모듈의 최근 7일 업무 내역이 궁금해",
];

// 역할 선택 UI 는 없다 — 매니저 여부는 선택이 아니라 사실이라, 서버가 로그인 사용자로 판별한다.

export default {
  name: "AgentView",
  components: { AgentSettingsDialog, Avatar, CommentEditor, FieldEdit },
  data() {
    return { authNote: "",
      // 좌(대화 목록)·우(초안 미리보기) 패널 — 폭 조절·접기(각자 저장). TicketDialog 와 같은 규칙.
      navW: loadW(NAV_W_KEY, NAV_MIN, NAV_MAX), navHidden: loadHidden(NAV_HIDE_KEY),
      sideW: loadW(SIDE_W_KEY, SIDE_MIN, SIDE_MAX), sideHidden: loadHidden(SIDE_HIDE_KEY),
      refTip: null,             // [n] 마커 호버 상자 {text, style}
      epicTitles: {},           // 상위 Epic 키 → 제목(미리보기 패널용)
      ready: null,            // null=확인 전 · true=쓸 수 있음 · false=설치/설정 안 됨
      reason: "",             // 못 쓰는 이유(설치 누락 등)
      status: null,           // provider·모델 — 지금 무엇으로 도는지 화면에 보인다
      appMeta: null,          // local dev 복사 진단용 {env,rev}. prod에서는 진단을 내보내지 않는다
      text: "",
      threadId: "",
      turns: [],              // [{who:"user"|"agent", text, trace, evidence, docs, questions,
                              //   assignments, review, pending, result}]
      // 응답 중인 대화 — 여러 대화가 동시에 돌 수 있다. 전역 플래그 하나로 두면
      // 다른 대화를 보는 동안에도 입력이 막히고 '…'이 떠서 멈춘 것처럼 보인다(사용자 지적).
      live: {},               // tid → true (응답 중). 사이드바 점·입력창 상태의 근거
      plans: {},              // tid → 진행 체크리스트 [{id,label,status,t0,dur,note,details}]
                              // status: pending → run → done, 안 지난 단계는 skip.
                              // 대화별로 보관해야 다녀와도 진행 표시가 이어진다.
      aborts: {},             // tid → 스트림 중단 함수
      approving: false,
      settingsOpen: false,
      answers: {},            // 되묻기 폼의 답(qi → 값)
      customOn: {},           // 객관식 질문에서 '직접 입력'을 고른 상태(qi → bool). 우선순위엔 없다
      qDone: {},              // 답을 확정한 질문(qi → bool) — 접혀서 선택만 보인다
      stepsOpen: false,       // 진행 표시 펼침 — 기본은 접힘(현재 단계만)
      previewOn: {},          // 초안 항목별 티켓 미리보기 토글(i → bool)
      epicTrees: {},          // 생성 카드의 계보 컨텍스트(epicKey → children[])
      priorities: [],
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
    // 아직 thread_id 를 못 받은 새 대화는 "_new" 자리에 기록해 두고 start 에서 옮긴다.
    tidKey() { return this.threadId || "_new"; },
    // 지금 **보고 있는 대화**가 응답 중인가 — 다른 대화가 도는 것은 여기에 영향이 없다.
    busy() { return !!this.live[this.tidKey]; },
    plan() { return this.plans[this.tidKey] || []; },
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
    api.health().then((h) => { this.appMeta = h || null; }).catch(() => {});
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
    augmentAgentBadges(this.$el);
  },
  unmounted() {
    // 화면을 떠났다 — 진행 중인 스트림은 전부 끊고 서버에도 중단을 알린다
    // (스트림만 끊으면 서버는 끝까지 일한다 = 토큰이 계속 나간다).
    Object.keys(this.aborts || {}).forEach((k) => {
      try { this.aborts[k](); } catch (e) { /* noop */ }
      if (k !== "_new") agentApi.stop(k).catch(() => {});
    });
  },
  methods: {
    // ── 패널 폭 조절·접기 (TicketDialog 의 spine/timeline 과 같은 규칙) ──────
    setNavHidden(v) { this.navHidden = v; saveLS(NAV_HIDE_KEY, v ? "1" : "0"); },
    setSideHidden(v) { this.sideHidden = v; saveLS(SIDE_HIDE_KEY, v ? "1" : "0"); },
    /** 경계선 드래그. `ref` 로 지금 실제 폭을 읽어 시작한다 — 저장값이 없을 때(0=CSS
     *  기본값)도 그 자리에서 자연스럽게 이어지게. `sign` 은 오른쪽 패널이 **왼쪽으로**
     *  끌 때 넓어지기 때문에 부호가 반대인 것을 담는다. */
    _startDrag(e, ref, sign, min, max, apply, done) {
      const el = this.$refs[ref];
      const w0 = (el && el.getBoundingClientRect().width) || min;
      const x0 = e.clientX;
      const onMove = (ev) => apply(Math.max(min, Math.min(max, w0 + sign * (ev.clientX - x0))));
      const onUp = () => {
        window.removeEventListener("mousemove", onMove);
        window.removeEventListener("mouseup", onUp);
        document.body.style.userSelect = "";
        done();
      };
      document.body.style.userSelect = "none";   // 드래그 중 글자 선택 방지
      window.addEventListener("mousemove", onMove);
      window.addEventListener("mouseup", onUp);
    },
    startNavDrag(e) {
      this._startDrag(e, "nav", +1, NAV_MIN, NAV_MAX,
                      (w) => { this.navW = Math.round(w); },
                      () => saveLS(NAV_W_KEY, this.navW));
    },
    startSideDrag(e) {
      this._startDrag(e, "side", -1, SIDE_MIN, SIDE_MAX,
                      (w) => { this.sideW = Math.round(w); },
                      () => saveLS(SIDE_W_KEY, this.sideW));
    },
    /** 더블클릭하면 기본 폭으로 되돌린다 — 끌다 망가뜨렸을 때 되돌릴 길(사용자가 폭을
     *  잘못 잡으면 그 상태가 저장돼 다음에도 그대로 뜬다). */
    resetNavW() { this.navW = 0; saveLS(NAV_W_KEY, 0); },
    resetSideW() { this.sideW = 0; saveLS(SIDE_W_KEY, 0); },
    md(t) { return renderMarkdown(t.text, t.people, t.evidence, t.docs); },
    evidenceText(t) { return mergeEvidenceMarkdown(t.text, t.evidence, t.docs); },
    /** [n] 마커 호버 — **하단 참조 목록과 같은 모양의** 커스텀 상자를 띄운다.
     *
     *  브라우저 기본 툴팁(title)을 쓰지 않는 이유가 셋이다: ①노란 기본 상자가 하단 참조
     *  목록과 생김새가 따로 논다 ②뜨는 데 1초 넘게 걸린다 ③줄바꿈을 못 준다(사용자 지적).
     *
     *  ★ **z 축** — 마커는 표 안에도 들어간다. 표는 가로 스크롤 컨테이너(`overflow-x:auto`)라
     *  거기 붙인 절대 위치 상자는 **셀 밖으로 나가는 순간 잘린다.** 그래서 상자를 마커의
     *  자식으로 두지 않고 **본문 최상위에 fixed 로 띄우고** 좌표만 계산한다 — 어떤 조상이
     *  overflow 를 걸어도 잘리지 않고, 스택 문맥에도 갇히지 않는다. */
    refOver(e) {
      const mark = e.target.closest && e.target.closest(".ref-mark[data-tip]");
      if (!mark) { if (this.refTip) this.refTip = null; return; }
      const r = mark.getBoundingClientRect();
      // 위에 자리가 없으면 아래로 — 첫 줄에 있는 마커가 화면 밖으로 나가지 않게.
      const above = r.top > 120;
      this.refTip = {
        text: mark.dataset.tip || "",
        style: {
          left: Math.min(Math.max(12, r.left - 8), window.innerWidth - 340) + "px",
          [above ? "bottom" : "top"]: (above ? window.innerHeight - r.top + 8
                                             : r.bottom + 8) + "px",
        },
      };
    },
    refOut(e) {
      if (!e.relatedTarget || !e.relatedTarget.closest ||
          !e.relatedTarget.closest(".ref-mark")) this.refTip = null;
    },

    /** [n] 참조 마커 클릭 — 같은 답변의 참조 칸을 열고 그 항목으로 점프 + 하이라이트. */
    mdClick(e) {
      const mark = e.target.closest && e.target.closest(".ref-mark");
      if (!mark) return;
      e.preventDefault();
      const md = mark.closest(".agent-md");
      if (!md) return;
      const det = md.querySelector("details.agent-refs");
      if (det) det.open = true;
      const item = md.querySelector(`.agent-ref-item[data-ref="${mark.dataset.ref}"], ` +
                                    `.ref-observation[data-ref="${mark.dataset.ref}"]`);
      if (!item) return;
      item.scrollIntoView({ behavior: "smooth", block: "center" });
      item.classList.remove("flash");
      void item.offsetWidth;               // 재트리거 — 같은 항목을 연속 클릭해도 깜빡인다
      item.classList.add("flash");
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
    /** 에디터 HTML → 모델에 보낼 텍스트. 멘션은 이름(사번), 링크 뱃지는 [제목](주소)로 푼다.
     *  모델은 HTML 이 아니라 글을 읽는다. 식별자와 참조 주소가 둘 다 남아야 선택한 자료를
     *  실제 근거로 사용할 수 있다. */
    richToText(html) { return richEditorToText(html); },
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

    dispatch(text, html, questionReceipt = null) {
      this.turns.push({ who: "user", text, html: html || "" });
      const turn = { who: "agent", text: "", trace: [], evidence: [], docs: [],
                     questions: [], assignments: [], review: {}, pending: null, result: null,
                     usage: null,
                     debug: { startedAt: new Date().toISOString(), finishedAt: "",
                              tokenChars: 0, events: [], plan: [], error: "" } };
      this.turns.push(turn);
      // ── 스트리밍 중에도 다른 대화를 볼 수 있다(사용자 요청 — 막을 이유가 없다).
      // 이 턴의 결과는 **이 배열**(myTurns)에 쌓이고, 화면이 다른 대화로 가 있으면
      // UI 갱신만 건너뛴다. active() = 지금 보고 있는 배열이 이 스트림의 배열인가.
      const myTurns = this.turns;
      let myTid = this.threadId;
      let myKey = myTid || "_new";          // thread_id 를 받기 전 임시 자리
      this._live = this._live || {};
      this._live[myKey] = myTurns;
      const active = () => this.turns === myTurns;
      this.live[myKey] = true;
      // 플랜은 planner 가 의도를 정하면 서버가 내려준다 — 그때까지는 첫 단계 하나만.
      // 대화별로 보관한다 — 다른 대화를 보고 와도 진행 표시가 그대로 이어져야 한다.
      this.plans[myKey] = [{ id: "planner", label: "요청 파악", status: "run",
                             t0: Date.now(), dur: null, note: "", details: [] }];
      // ★ 반응형 프록시를 잡아서 쓴다 — 원본 배열을 직접 고치면 화면이 안 바뀐다(Vue 3).
      const myPlan = this.plans[myKey];
      const settle = () => {                // 이 스트림의 끝 — 진행 표시·라이브 표식을 거둔다
        turn.debug.finishedAt = new Date().toISOString();
        turn.debug.plan = JSON.parse(JSON.stringify(myPlan || []));
        delete this.live[myKey];
        delete this.plans[myKey];
        delete this.aborts[myKey];
        if (this._live) delete this._live[myKey];
      };
      this.$nextTick(this.scroll);

      this.aborts[myKey] = agentApi.stream(
        { text: questionReceipt ? "" : text, threadId: this.threadId,
          ...(questionReceipt ? { questionReceipt } : {}) },
        (ev) => {
          // local dev에서 복사할 진단 기록. token 원문·비밀값은 보관하지 않고 글자 수만 센다.
          if (ev.type === "token") turn.debug.tokenChars += String(ev.text || "").length;
          else {
            const item = { type: ev.type || "unknown", at: new Date().toISOString() };
            ["node", "label", "parent", "note", "message", "error", "thread_id"].forEach((k) => {
              if (ev[k] !== undefined && ev[k] !== null && ev[k] !== "") item[k] = String(ev[k]).slice(0, 1000);
            });
            if (ev.done !== undefined) item.done = !!ev.done;
            turn.debug.events.push(item);
            if (turn.debug.events.length > 200) turn.debug.events.shift();
            if (ev.type === "error" || ev.error) turn.debug.error = String(ev.message || ev.error || "").slice(0, 2000);
          }
          if (ev.type === "start") {
            myTid = ev.thread_id || myTid;
            // 임시 자리("_new")에 쌓아 둔 것을 진짜 thread_id 로 옮긴다.
            if (myTid && myTid !== myKey) {
              this._live[myTid] = myTurns;
              this.live[myTid] = true;
              this.plans[myTid] = this.plans[myKey];
              this.aborts[myTid] = this.aborts[myKey];
              delete this._live[myKey]; delete this.live[myKey];
              delete this.plans[myKey]; delete this.aborts[myKey];
              myKey = myTid;
            }
            if (active()) this.threadId = myTid;
            this.saveConvo(myTid, myTurns);   // 첫 전송 즉시 사이드바에 뜬다
          }
          else if (ev.type === "plan") {
            // 의도가 정해졌다 — 앞으로 지날 단계의 체크리스트. 이미 지난 단계(planner)의
            // 상태·소요시간은 보존한다. (다른 대화를 보는 중에도 **자기 플랜**은 갱신한다 —
            // 돌아왔을 때 진행 표시가 이어져야 한다.)
            const next = (ev.steps || []).map((st) => {
              const prev = myPlan.find((p) => p.id === st.id);
              return prev || { id: st.id, label: st.label, status: "pending",
                               t0: 0, dur: null, note: "", details: [] };
            });
            myPlan.splice(0, myPlan.length, ...next);
            if (active()) this.$nextTick(this.scroll);
          }
          else if (ev.type === "node") {
            // 단계 하나가 끝났다 — [✓] 로 접고, 건너뛴 단계는 흐리게, 다음 단계를 연다.
            const now = Date.now();
            let i = myPlan.findIndex((p) => p.id === ev.node);
            if (i < 0) {
              myPlan.push({ id: ev.node, label: ev.label, status: "run",
                            t0: now, dur: null, note: "", details: [] });
              i = myPlan.length - 1;
            }
            const s = myPlan[i];
            s.status = "done";
            s.dur = s.t0 ? ((now - s.t0) / 1000).toFixed(1) : null;
            if (ev.note) s.note = ev.note;
            myPlan.forEach((p, j) => { if (j < i && p.status === "pending") p.status = "skip"; });
            const nxt = myPlan.find((p) => p.status === "pending");
            if (nxt) { nxt.status = "run"; nxt.t0 = now; }
            if (active()) this.$nextTick(this.scroll);
          }
          else if (ev.type === "step") {
            // 세부 행위(도구 호출·결과) — 소속 단계 밑에 중첩. "웹 검색 — <검색어>" 가
            // 실행 줄이고, 결과가 오면 같은 줄이 "… 완료 — <얻은 것>" 으로 바뀐다.
            const now = Date.now();
            let s = myPlan.find((p) => p.id === (ev.parent || ""));
            if (!s) s = myPlan.find((p) => p.status === "run");
            if (!s) return;
            if (s.status !== "run") { s.status = "run"; s.t0 = s.t0 || now; }
            const d = { text: ev.label + (ev.note ? " — " + ev.note : ""), done: !!ev.done };
            const last = s.details[s.details.length - 1];
            if (ev.done && last && !last.done) s.details.splice(s.details.length - 1, 1, d);
            else s.details.push(d);
            if (s.details.length > 40) s.details.shift();
            if (active()) this.$nextTick(this.scroll);
          } else if (ev.type === "token") {
            // 최종 답이 만들어지는 **동안** 그 대화의 턴 객체에 자란다 — 다른 대화를 보고
            // 있어도 데이터는 쌓이고, 화면 갱신(스크롤)만 건너뛴다.
            turn.text = (turn.text || "") + (ev.text || "");
            const r = myPlan.find((p) => p.id === "responder");
            if (r && r.status !== "done" && r.status !== "run") { r.status = "run"; r.t0 = Date.now(); }
            if (active()) this.$nextTick(this.scroll);
          } else if (ev.type === "stopped") {
            // 사용자가 ■ 를 눌렀다 — 서버가 노드 경계에서 빠져나왔다.
            turn.text = (turn.text ? turn.text + "\n\n" : "") + "⏹ " + (ev.message || "중단했습니다.");
            turn.stopped = true;
            settle();
            this.saveConvo(myTid, myTurns);
          } else if (ev.type === "error") {
            turn.text = "문제가 생겼습니다 — " + (ev.message || "알 수 없는 오류");
            settle();
            this.saveConvo(myTid, myTurns);
          } else if (ev.type === "final") {
            Object.assign(turn, {
              text: ev.reply || "(답변이 비어 있습니다)",
              trace: ev.trace || [], evidence: ev.evidence || [],
              docs: ev.related_docs || [], questions: ev.questions || [],
              questionReceipt: ev.questionReceipt || null,
              assignments: ev.assignments || [], review: ev.review || {},
              pending: ev.pending || null, result: ev.result || null,
              usage: ev.usage || null, people: ev.people || {},
              draftItems: ev.draft_items || [],
            });
            settle();
            this.saveConvo(myTid, myTurns);
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
            // ★ **새 미리보기가 오면 접혀 있어도 편다**(사용자 요청). 한 번 접어 둔 상태가
            //   저장돼 있으면, 다음 초안이 와도 화면에 아무 변화가 없어 **만들어진 줄 모른다** —
            //   접기는 "지금 이건 안 볼래"이지 "앞으로 영영 안 볼래"가 아니다.
            if (nItems && this.sideHidden) this.setSideHidden(false);
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

    /** ■ 중단 — 지금 보고 있는 대화의 응답을 멈춘다.
     *
     *  스트림만 끊으면 서버는 끝까지 일한다(토큰이 계속 나간다) — 서버에도 알린다.
     *  이미 시작된 LLM 호출 하나는 끝나지만 그다음 단계로 넘어가지 않는다. */
    async stopStream() {
      const key = this.tidKey;
      const ab = this.aborts[key];
      if (key !== "_new") agentApi.stop(key).catch(() => {});
      const last = this.turns[this.turns.length - 1];
      if (last && last.who === "agent" && !last.text) {
        last.text = "⏹ 중단했습니다. 여기까지 진행된 내용은 남아 있어, 이어서 물으면 그 지점부터 계속합니다.";
        last.stopped = true;
      }
      if (ab) { try { ab(); } catch (e) { /* noop */ } }
      if (last && last.debug) {
        last.debug.finishedAt = new Date().toISOString();
        last.debug.plan = JSON.parse(JSON.stringify(this.plans[key] || []));
      }
      delete this.live[key]; delete this.plans[key]; delete this.aborts[key];
      if (this._live) delete this._live[key];
      if (key !== "_new") this.saveConvo(key, this.turns);
    },

    reset() {
      // 새 대화 — 진행 중인 응답은 **끊지 않는다**. 멈추고 싶으면 ■ 버튼이 있고,
      // 새 질문을 시작한다고 앞 대화를 죽일 이유가 없다(사용자 지적: 여러 대화 관리).
      // 완료되면 각자 자기 대화에 저장되고 토스트로 알린다.
      this.threadId = ""; this.turns = [];
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
    sideItem() {
      const it = this.sideItems()[this.sideDraft] || {};
      // 상위 Epic 제목을 한 번만 받아 둔다 — 키만 보여 주면 어느 Epic 인지 모른다.
      const k = it.epic;
      if (k && !(k in this.epicTitles)) {
        this.epicTitles[k] = "";                       // 재요청 방지(빈 값이 '조회 중')
        api.ticketBadge(k).then((b) => {
          if (b && b.summary) this.epicTitles[k] = b.summary;
        }).catch(() => {});
      }
      return it;
    },
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
      this.sideKey = "";
      this.$nextTick(this.scroll);
    },
    removeConvo(c) {
      this.convos = this.convos.filter((x) => x.id !== c.id);
      try { localStorage.setItem("agentConvos", JSON.stringify(this.convos)); } catch (e) {}
      if (this.threadId === c.id) this.reset();
    },

    /** 지금 무엇으로 도는지(provider·모델)를 다시 읽는다 — 설정에서 바꾸면 즉시.
     *  ★ 템플릿에서 직접 agentApi 를 부르면 안 된다(템플릿 스코프에 모듈이 없어
     *  조용히 실행되지 않는다 — 모델을 바꿔도 좌상단이 그대로였던 실측 원인). */
    async refreshStatus() {
      try { this.status = await agentApi.status(); } catch (e) { /* 표시만 못 할 뿐 */ }
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
        L.push(`A: ${this.evidenceText(t) || "(본문 없음)"}`);
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
      // 운영 대화 복사는 사용자 입출력만. local/mock에서는 재현에 필요한 진행·연결 정보를
      // 함께 싣는다. SSE token 원문과 API 키는 애초에 debug에 저장하지 않는다.
      if (this.appMeta && this.appMeta.env !== "prod") {
        const active = this.status && this.status.activeConfig;
        L.push("## Local debug", "",
          `- env: ${this.appMeta.env || "unknown"}`,
          `- rev: ${this.appMeta.rev || "unknown"}`,
          `- threadId: ${this.threadId || "(not assigned)"}`,
          `- config: ${active ? active.name + " / " + active.provider : "(none)"}`,
          `- model: ${(this.status && this.status.chatModel) || "(none)"}`, "");
        this.turns.filter((t) => t.who === "agent" && t.debug).forEach((t, i) => {
          const d = t.debug;
          L.push(`### Turn ${i + 1}`, "", `- startedAt: ${d.startedAt || ""}`,
            `- finishedAt: ${d.finishedAt || ""}`, `- tokenChars: ${d.tokenChars || 0}`);
          (d.plan || []).forEach((p) => {
            L.push(`- [${p.status || "?"}] ${p.label || p.id}` + (p.note ? ` — ${p.note}` : ""));
            (p.details || []).forEach((x) => L.push(`  - ${x.done ? "완료" : "진행"}: ${x.text}`));
          });
          (d.events || []).forEach((x) => {
            const detail = [x.label, x.note, x.message, x.error].filter(Boolean).join(" — ");
            L.push(`- event:${x.type}` + (detail ? ` — ${detail}` : ""));
          });
          if (d.error) L.push(`- error: ${d.error}`);
          L.push("");
        });
      }
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
    descText(html) { return draftDescriptionText(html); },
    /** 본문 HTML 미리보기 — v-html 로 넣기 전에 **화이트리스트로 정화**한다.
     *  초안 HTML 은 LLM 산출물이라 script/이벤트 핸들러가 섞일 가능성을 0 으로 못 박는다. */
    descPreview(html) { return sanitizeDraftDescription(html); },
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
    // 에이전트의 질문(kind/options/field)을 폼으로 그리고, 서버가 준 opaque question_id와
    // 답만 돌려준다. 이전 서버/loose 질문에는 기존 자연어 전송을 그대로 유지한다.
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
    /** 지금 안 정해도 되는 질문인가 — **담당자·일정**(사용자 요청).
     *  이 둘은 승인 카드와 티켓 화면에서 언제든 바꿀 수 있는 값이다. 여기서 멈춰 세우면
     *  초안까지 가는 길만 길어진다 — "나중에 직접 선택"이 실제로 가장 흔한 답이다. */
    deferrable(q) {
      const f = (q && q.field) || "";
      const k = (q && q.kind) || "";
      const txt = String((q && q.question) || "");
      return f === "duedate" || f === "assignee" || k === "date"
             || /마감|기한|일정|담당자|담당 /.test(txt);
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
      const answers = [];
      (turn.questions || []).forEach((q, qi) => {
        const a = (this.answers[this.qKey(qi)] || "").trim();
        if (a) {
          lines.push((q.question || q) + " → " + a);
          answers.push({ question_id: q.question_id,
                         value: q.kind === "multi" ? a.split(" | ").filter(Boolean) : a });
        }
      });
      if (!lines.length) return;
      this.answers = {};
      const challenge = turn.questionReceipt;
      if (challenge && challenge.challenge_id
          && answers.every((row) => row.question_id)) {
        turn.questionReceipt = null; // one-use capability; prevent a second local submit
        this.dispatch(lines.join("\n"), null, {
          contract: "question_answer.receipt.v1",
          challenge_id: challenge.challenge_id,
          answers,
        });
        return;
      }
      this.text = lines.join("\n");
      this.send(); // old server / loose question: preserve the semantic text path
    },
    skipAnswers(turn) {
      this.answers = {};
      if (turn) turn.questionReceipt = null;
      this.text = "나머지는 합리적 기본값으로 알아서 진행해줘";
      this.send();
    },
  },

  template: AGENT_VIEW_TEMPLATE,
};
