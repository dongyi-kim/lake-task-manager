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
import { agentApi } from "../../lib/agentApi.js";
import { renderMarkdown } from "../../lib/agentMd.js";
import { api } from "../../lib/api.js";
import { pushToast } from "../../lib/toast.js";

// 빈 화면에 예시를 둔다 — 무엇을 할 수 있는 도구인지 설명하는 가장 빠른 방법이고,
// 사용자가 첫 문장을 어떻게 쓸지 몰라 멈추는 것을 막는다.
const EXAMPLES = [
  "실시간 수집 파이프라인에 CDC 방식을 도입해야 한다",
  "데이터 카탈로그에 품질 규칙 관리 기능을 붙여야 해",
  "Workbench 쿼리 편집기 성능 개선 관련 이력 알려줘",
  "DL-101 지금 어디까지 진행됐나요?",
];

const ROLES = [
  { k: "pm", label: "PM", hint: "전체 진척·리스크·일정을 먼저 봅니다" },
  { k: "lead", label: "모듈 리더", hint: "담당 배분과 팀 부하를 먼저 봅니다" },
  { k: "member", label: "실무자", hint: "내 일의 범위와 다음 행동을 먼저 봅니다" },
];

export default {
  name: "AgentView",
  data() {
    return {
      ready: null,            // null=확인 전 · true=쓸 수 있음 · false=설치/설정 안 됨
      reason: "",             // 못 쓰는 이유(설치 누락 등)
      status: null,           // provider·모델 — 지금 무엇으로 도는지 화면에 보인다
      role: localStorage.getItem("agentRole") || "member",
      text: "",
      threadId: "",
      turns: [],              // [{who:"user"|"agent", text, trace, evidence, docs, questions,
                              //   assignments, review, pending, result}]
      busy: false,
      steps: [],              // 지금 굴러가는 진행(스트리밍 중에만)
      abort: null,
      approving: false,
    };
  },
  computed: {
    examples() { return EXAMPLES; },
    roles() { return ROLES; },
    roleHint() { return (ROLES.find((r) => r.k === this.role) || {}).hint || ""; },
    empty() { return this.turns.length === 0; },
    // 승인 대기는 **마지막 턴에만** 유효하다. 지난 카드가 계속 눌리면 사용자가 옛 초안을 만든다.
    pending() {
      const last = this.turns[this.turns.length - 1];
      return last && last.who === "agent" && last.pending ? last.pending : null;
    },
  },
  mounted() {
    api.prefs()
      .then((p) => {
        this.ready = !!p.agentEnabled;
        this.reason = p.agentReason || "";
        if (this.ready) return agentApi.status().then((s) => { this.status = s; });
      })
      .catch((e) => { this.ready = false; this.reason = (e && e.message) || "확인 실패"; });
    // 답변 안의 티켓 키(`.tkt[data-key]`)는 앱 전역 위임 처리기가 잡는다 — 여기서 또 걸지 않는다.
  },
  unmounted() {
    if (this.abort) this.abort();      // 화면을 떠났는데 서버가 계속 일할 이유가 없다
  },
  methods: {
    md(t) { return renderMarkdown(t); },
    setRole(k) { this.role = k; localStorage.setItem("agentRole", k); },
    use(ex) { this.text = ex; this.$refs.input && this.$refs.input.focus(); },

    onKey(e) {
      // Enter=보내기 / Shift+Enter=줄바꿈. 업무 설명은 여러 줄이 되기 쉬워 줄바꿈을 남겨 둔다.
      if (e.key === "Enter" && !e.shiftKey && !e.isComposing) { e.preventDefault(); this.send(); }
    },

    send() {
      const text = (this.text || "").trim();
      if (!text || this.busy) return;
      this.text = "";
      this.turns.push({ who: "user", text });
      const turn = { who: "agent", text: "", trace: [], evidence: [], docs: [],
                     questions: [], assignments: [], review: {}, pending: null, result: null,
                     usage: null };
      this.turns.push(turn);
      this.busy = true;
      this.steps = [];
      this.$nextTick(this.scroll);

      this.abort = agentApi.stream(
        { text, threadId: this.threadId, role: this.role },
        (ev) => {
          if (ev.type === "start") { this.threadId = ev.thread_id || this.threadId; }
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
              usage: ev.usage || null,
            });
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
        const r = await agentApi.approve(this.threadId, p.token);
        const last = this.turns[this.turns.length - 1];
        last.pending = null;                        // 카드를 닫는다 — 두 번 눌리면 안 된다
        this.turns.push({ who: "agent", text: r.reply || "", trace: r.trace || [],
                          evidence: [], docs: [], questions: [], assignments: [],
                          review: {}, pending: null, result: r.result || null });
        const made = ((r.result || {}).created || []).length;
        const bad = ((r.result || {}).failed || []).length;
        if (made) {
          pushToast({ kind: bad ? "error" : "success", key: "agent-made",
                      title: `${made}건 생성했습니다` + (bad ? ` · 실패 ${bad}건` : "") });
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
    },

    openTicket(key) {
      if (key) window.dispatchEvent(new CustomEvent("lake-open-ticket", { detail: { key } }));
    },
    scroll() { const el = this.$refs.scroller; if (el) el.scrollTop = el.scrollHeight; },
    itemOf(p, i) { return (p.items || [])[i] || {}; },
    reasonsFor(turn, i) {
      const a = (turn.assignments || []).find((x) => x.index === i);
      return a ? a : null;
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
      <div class="agent-head">
        <div class="agent-title">
          <h1>업무 착수 어시스턴트</h1>
          <p>하려는 업무를 말하면 <b>과거 이력을 찾아 현재 상황을 정리</b>하고,
             대화로 구체화해 <b>담당자 제안과 함께 티켓 초안</b>까지 만들어 드립니다.</p>
        </div>
        <div class="agent-meta">
          <div class="agent-roles">
            <button v-for="r in roles" :key="r.k" :class="{ on: role === r.k }"
                    @click="setRole(r.k)" :title="r.hint">{{ r.label }}</button>
          </div>
          <span class="agent-rolehint">{{ roleHint }}</span>
          <span v-if="status" class="agent-prov" :title="'chat=' + status.chatModel + ' / embed=' + status.embedModel">
            {{ status.provider }}<template v-if="status.chatModel"> · {{ status.chatModel }}</template>
          </span>
          <button v-if="turns.length" class="agent-reset" @click="reset">새 대화</button>
        </div>
      </div>

      <div class="agent-scroll" ref="scroller">
        <!-- 빈 화면: 무엇을 할 수 있는지 예시로 보여 준다 -->
        <div v-if="empty && !busy" class="agent-empty">
          <div class="agent-ex-h">이렇게 물어보세요</div>
          <button v-for="ex in examples" :key="ex" class="agent-ex" @click="use(ex)">{{ ex }}</button>
        </div>

        <div v-for="(t, ti) in turns" :key="ti" class="agent-turn" :class="t.who">
          <div v-if="t.who === 'user'" class="agent-bubble user">{{ t.text }}</div>

          <div v-else class="agent-bubble agent">
            <div v-if="t.text" class="agent-md" v-html="md(t.text)"></div>
            <div v-else-if="busy && ti === turns.length - 1" class="agent-thinking">
              <span class="dot"></span><span class="dot"></span><span class="dot"></span>
            </div>

            <!-- 비용 — 질문 하나로 보이지만 안에서 LLM 을 예닐곱 번 부른다.
                 숫자를 봐야 "이건 비싼 질문이었다"를 알고 다음에 다르게 묻는다. -->
            <div v-if="t.usage && t.usage.totalTokens" class="agent-usage"
                 :title="t.usage.model + ' · 입력 ' + t.usage.promptTokens + ' / 출력 ' + t.usage.completionTokens">
              LLM {{ t.usage.calls }}회 · {{ t.usage.totalTokens.toLocaleString() }} 토큰<template
                v-if="t.usage.costUsd"> · ${{ t.usage.costUsd.toFixed(4) }}</template>
            </div>

            <!-- 근거: 눌러서 확인할 수 있어야 믿을 수 있다 -->
            <div v-if="t.evidence && t.evidence.length" class="agent-ev">
              <div class="agent-ev-h">근거</div>
              <button v-for="e in t.evidence" :key="e.key" class="agent-ev-row"
                      @click="openTicket(e.key)" :title="e.why">
                <b>{{ e.key }}</b><span>{{ e.title }}</span><em>{{ e.why }}</em>
              </button>
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

            <!-- ★ HITL 승인 카드 — 여기서 [생성]을 눌러야만 쓰기가 시작된다 -->
            <div v-if="t.pending && ti === turns.length - 1" class="agent-card">
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
                    <span v-if="it.assignee" class="ai-who">담당 {{ it.assignee }}</span>
                  </div>
                  <div v-if="it.description" class="ai-desc">{{ it.description }}</div>
                  <!-- 담당자는 근거와 함께 보인다. 이름만 있으면 리더가 검증할 수 없다 -->
                  <div v-if="reasonsFor(t, i)" class="ai-reasons">
                    <div v-for="(r, ri) in reasonsFor(t, i).reasons" :key="ri">· {{ r }}</div>
                    <div v-for="(alt, ai) in (reasonsFor(t, i).alternates || [])" :key="'a'+ai" class="ai-alt">
                      대안 {{ alt.user }} — {{ alt.why }}
                    </div>
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

      <div class="agent-input">
        <textarea ref="input" v-model="text" rows="2" :disabled="busy || ready === null"
                  placeholder="하려는 업무를 적어 주세요. 예) 실시간 수집 파이프라인에 CDC 방식을 도입해야 한다"
                  @keydown="onKey"></textarea>
        <button class="ag-ok" :disabled="busy || !text.trim()" @click="send">
          {{ busy ? '…' : '보내기' }}
        </button>
      </div>
      <div class="agent-foot">
        Enter 전송 · Shift+Enter 줄바꿈 — <b>승인하기 전에는 아무것도 만들거나 바꾸지 않습니다.</b>
        <a href="#/guide">서비스 안내</a>
      </div>
    </template>
  </div>`,
};
