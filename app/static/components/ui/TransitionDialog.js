// TransitionDialog.js — 상태 전이 화면.
//
// Jira 의 전이 화면을 우리가 대신 그린다. 무엇을 물어야 하는지는 서버가 준 필드 목록이
// 정한다(?expand=transitions.fields) — 워크플로마다 다르므로 화면에 박아 두면 안 된다.
//
// 두 가지를 일부러 Jira 와 다르게 한다:
//  1) **소요시간을 일/시/분 숫자로 받는다.** Jira 는 "1d 5h" 같은 문자열을 직접 치게 하는데
//     오타가 잦다(5h 를 5 로만 쓰거나, 공백을 빠뜨리거나). 숫자 칸으로 받아 서버에서 조립한다.
//     ★ '1d' 가 몇 시간인지는 **우리가 정하지 않는다** — Jira 인스턴스 설정
//       (workingHoursPerDay, DC 기본 8시간)이 정한다. 그 값을 읽어와 화면에 그대로 알린다.
//       안 알리면 사용자는 하루를 24시간으로 여기고 적는데 Jira 는 8시간으로 기록한다.
//  2) **코멘트를 필수로 받는다.** Jira 에선 선택이지만, 무엇을 했는지 한 줄도 없이 닫힌
//     티켓은 나중에 아무도 해석하지 못한다. 이 앱을 통해 닫는 것에는 기록을 남긴다.
//     입력은 **댓글과 같은 에디터**를 쓴다 — 표·코드·이미지 붙여넣기·멘션이 그대로 되고,
//     여기만 맨 textarea 면 "왜 여기선 안 되지" 가 된다. 제출은 이 화면이 소유하고
//     (버튼이 둘이면 안 된다) 에디터의 submit() 을 ref 로 부른다 — 이미지 업로드·초안 정리가
//     그 안에 들어 있어, 밖에서 다시 짜면 반드시 어긋난다.
import { api } from "../../lib/api.js";
import Avatar from "./Avatar.js";
import CommentEditor from "./CommentEditor.js";
import { fromBackdrop } from "../../lib/backdrop.js";
import { createUserTypeahead, defaultUserSuggestions, rememberUser } from "../../lib/userSuggestions.js";

export default {
  name: "TransitionDialog",
  components: { Avatar, CommentEditor },
  props: { ticket: { type: String, required: true }, transition: { type: Object, required: true } },
  emits: ["close", "done"],
  data() {
    return {
      days: 0, hours: 0, minutes: 0, resolution: "",
      tt: null,   // 시간 추적 설정(하루 = 몇 시간)
      // 담당자는 **선택된 사람 자체**를 들고 있는다(문자열이 아니라 노드). 문자열이면 화면에
      // 남은 글자와 실제 값이 어긋날 수 있다 — 다 치고 못 고른 채 제출하는 사고가 난다.
      user: null, q: "", who: [], whoOpen: false, hi: 0,
      busy: false, err: "",
    };
  },
  computed: {
    fields() { return (this.transition.fields && this.transition.fields.fields) || []; },
    unsupported() { return (this.transition.fields && this.transition.fields.unsupported) || []; },
    has() { const m = {}; for (const f of this.fields) m[f.id] = f; return m; },
    resolutions() { return (this.has.resolution && this.has.resolution.allowedValues) || []; },
    timeText() {
      const p = [];
      if (this.days) p.push(this.days + "d");
      if (this.hours) p.push(this.hours + "h");
      if (this.minutes) p.push(this.minutes + "m");
      return p.join(" ") || "—";
    },
    hoursPerDay() { return (this.tt && this.tt.hoursPerDay) || 8; },
    dayNote() { return "이 Jira 에서 1일 = " + this.hoursPerDay + "시간"; },
    problems() {
      const out = [];
      // 무엇이 필수인지는 **서버가 준 화면 정의**가 정한다(전이마다 다르다).
      const need = (id) => this.has[id] && this.has[id].required;
      if (need("worklog") && !(this.days || this.hours || this.minutes)) out.push("소요시간");
      if (need("assignee") && !this.user) out.push("담당자");
      if (need("resolution") && !this.resolution) out.push("처리 방법");
      // 코멘트 내용 유무는 에디터가 판정한다(빈 본문이면 제출 시 스스로 막는다) —
      // 여기서 HTML 을 들여다보며 다시 판정하면 두 규칙이 갈린다.
      return out;
    },
  },
  mounted() {
    if (this.resolutions.length) this.resolution = this.resolutions[0].name;
    api.timetracking().then((t) => { this.tt = t; }).catch(() => {});
    // ★ allowEmpty 가 없으면 **빈 검색어에서 무조건 빈 배열**이라 칸을 눌러도 아무것도 안 뜬다
    //   — 사용자에겐 "검색이 동작 안 한다" 로 보인다. 빈 검색어는 이 티켓 관련자를 먼저 주므로
    //   (서버가 key 로 판단) 오히려 가장 쓸모 있는 첫 화면이다.
    this._ta = createUserTypeahead(this.ticket, []);
    this.who = defaultUserSuggestions([], []);
    this.searchWho("");
    api.me().then((m) => {                       // 대개 자기 자신이다 — 기본값으로 채운다
      if (m && m.id && !this.user) {
        this.user = { id: m.id, name: m.name || m.id, display: m.display || m.name || m.id,
                      avatar: "/api/avatar/" + encodeURIComponent(m.id) };
      }
    }).catch(() => {});
  },
  methods: {
    // 드래그가 창 밖에서 끝났을 뿐인데 닫히지 않게 — lib/backdrop.js 참고
    fromBackdrop,
    searchWho(q) {
      this.hi = 0;
      if (!String(q || "").trim()) this.who = defaultUserSuggestions([], []);
      this._ta.run(q).then((r) => { if (r) this.who = r.slice(0, 8); }).catch(() => {});
    },
    pickWho(u) { rememberUser(u); this.user = u; this.whoOpen = false; this.q = ""; this.who = []; },
    clearWho() {
      this.user = null; this.q = ""; this.whoOpen = true; this.searchWho("");
      this.$nextTick(() => { const el = this.$refs.who; if (el) el.focus(); });
    },
    onWhoKey(e) {
      if (!this.whoOpen || !this.who.length) return;
      if (e.key === "ArrowDown") { e.preventDefault(); this.hi = (this.hi + 1) % this.who.length; }
      else if (e.key === "ArrowUp") { e.preventDefault(); this.hi = (this.hi + this.who.length - 1) % this.who.length; }
      else if (e.key === "Enter") { e.preventDefault(); this.pickWho(this.who[this.hi]); }
    },
    /** 제출 버튼 → 에디터에게 넘긴다. 에디터가 이미지 업로드·본문 검사를 마친 뒤
     *  sendTransition(html) 을 호출한다. 코멘트 필드가 없는 전이면 바로 보낸다. */
    async submit() {
      if (this.problems.length || this.busy) return;
      this.err = "";
      if (this.has.comment && this.$refs.ed) { this.$refs.ed.submit(); return; }
      this.busy = true;
      await this.sendTransition("");
    },
    /** 실제 전송. 에디터가 부르는 경로라 실패는 **던져야** 한다 — 에디터가 그걸 받아
     *  올린 이미지를 되돌리고 사용자에게 알린다(조용히 삼키면 첨부만 남는다). */
    async sendTransition(html) {
      this.busy = true;
      let r;
      try {
        r = await api.doTransition(this.ticket, {
          id: this.transition.id, days: Number(this.days) || 0,
          hours: Number(this.hours) || 0,
          minutes: Number(this.minutes) || 0,
          assignee: (this.user && this.user.id) || "",
          resolution: this.resolution,
          commentHtml: html || "",
        });
        if (r && r.ok === false) throw new Error(r.error || "전이에 실패했습니다.");
      } catch (e) {
        this.busy = false;
        // Jira 가 거절한 이유를 그대로 보인다 — 삼키면 무엇을 고쳐야 할지 알 수 없다.
        this.err = (e && e.message) || "전이에 실패했습니다.";
        throw e;
      }
      this.$emit("done");
      // 후처리: 이 전이가 부모 상태 규칙을 촉발하면(하위 완료/진행중/재열림) 상위도 바꿀지 물어본다.
      if (r && r.cascade) window.dispatchEvent(new CustomEvent("cascade-prompt", { detail: r.cascade }));
    },
  },
  template: `
  <div class="trx-ov" @click.self="fromBackdrop($event) && $emit('close')">
    <div class="trx">
      <div class="trx-h">
        <b>{{ transition.name || ('→ ' + transition.to) }}</b>
        <span class="trx-key">{{ ticket }}</span>
        <button class="trx-x" @click="$emit('close')" title="닫기">×</button>
      </div>

      <div v-if="unsupported.length" class="trx-block">
        이 전이는 앱에서 처리할 수 없는 필수 항목이 있습니다 — <b>{{ unsupported.join(', ') }}</b>.
        Jira 에서 진행해 주세요.
      </div>

      <div v-else class="trx-b">
        <label v-if="has.worklog" class="trx-f">
          <span class="trx-l">소요시간 <i v-if="has.worklog.required">필수</i></span>
          <span class="trx-time">
            <input type="number" min="0" max="99" v-model.number="days"><em :title="dayNote">일</em>
            <input type="number" min="0" max="999" v-model.number="hours"><em>시간</em>
            <input type="number" min="0" max="59" step="5" v-model.number="minutes"><em>분</em>
            <b class="trx-prev">{{ timeText }}</b>
          </span>
          <span class="trx-hint">실제 이 업무만을 위해 소요한 시간 기준으로 입력<i v-if="days"> · {{ dayNote }}</i></span>
        </label>

        <div v-if="has.assignee" class="trx-f">
          <span class="trx-l">담당자 <i v-if="has.assignee.required">필수</i></span>
          <!-- 고른 사람은 **노드(칩)** 로 남는다 — 입력창에 글자로 남겨 두면 화면의 글자와
               실제 값이 어긋날 수 있고, 다 치고 못 고른 채 제출하는 사고가 난다. -->
          <span v-if="user" class="trx-chip">
            <Avatar :user="user.id" :name="user.display || user.name" :size="22" />
            <!-- 소속까지 보이는 전체 표시이름 — 동명이인이 있으면 본명만으론 누구인지 못 고른다 -->
            <b>{{ user.display || user.name }}</b>
            <em>{{ user.id }}</em>
            <button class="trx-chip-x" @click="clearWho" title="다른 사람 고르기">×</button>
          </span>
          <span v-else class="trx-who">
            <input ref="who" :value="q" @focus="whoOpen = true; searchWho(q)"
                   @input="q = $event.target.value; whoOpen = true; searchWho($event.target.value)"
                   @keydown="onWhoKey" placeholder="이름 또는 사번으로 검색">
            <div v-if="whoOpen && who.length" class="trx-drop">
              <button v-for="(u, i) in who" :key="u.id" :class="{ hi: i === hi }"
                      @click.prevent="pickWho(u)" @mouseenter="hi = i">
                <Avatar :user="u.id" :name="u.display || u.name" :size="22" />
                <span>{{ u.display || u.name }}</span><em>{{ u.id }}</em>
              </button>
            </div>
            <span v-else-if="whoOpen" class="trx-hint">검색 결과가 없습니다.</span>
          </span>
        </div>

        <label v-if="has.resolution" class="trx-f">
          <span class="trx-l">처리 방법 <i v-if="has.resolution.required">필수</i></span>
          <select v-model="resolution">
            <option v-for="r in resolutions" :key="r.id" :value="r.name">{{ r.name }}</option>
          </select>
        </label>

        <div v-if="has.comment" class="trx-f">
          <span class="trx-l">코멘트 <i v-if="has.comment.required">필수</i></span>
          <!-- 댓글과 **같은 에디터** — 표·코드·이미지 붙여넣기·멘션이 그대로 된다.
               버튼 줄은 감추고(제출은 아래 한 곳) ref 로 submit() 을 부른다. -->
          <CommentEditor ref="ed" :ticket-key="ticket" hide-footer kind="transition"
                         :submit-fn="sendTransition" @cancel="$emit('close')" />
        </div>
      </div>

      <div class="trx-f2">
        <span v-if="err" class="trx-err">{{ err }}</span>
        <span v-else-if="problems.length" class="trx-need">입력 필요: {{ problems.join(' · ') }}</span>
        <button class="trx-cancel" @click="$emit('close')">취소</button>
        <button class="trx-ok" :disabled="busy || problems.length || unsupported.length" @click="submit">
          {{ busy ? '처리 중…' : (transition.to || '전이') + ' 로 이동' }}
        </button>
      </div>
    </div>
  </div>`,
};
