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
import { api } from "../../lib/api.js";
import Avatar from "./Avatar.js";
import { createTypeahead } from "../../lib/typeahead.js";

export default {
  name: "TransitionDialog",
  components: { Avatar },
  props: { ticket: { type: String, required: true }, transition: { type: Object, required: true } },
  emits: ["close", "done"],
  data() {
    return {
      days: 0, hours: 0, minutes: 0, resolution: "", comment: "",
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
      if (this.has.worklog && !(this.days || this.hours || this.minutes)) out.push("소요시간");
      if (this.has.assignee && !this.user) out.push("담당자");
      if (this.has.resolution && !this.resolution) out.push("처리 방법");
      // 코멘트는 Jira 기준 선택이지만 우리는 필수로 둔다(위 주석 참고).
      if (this.has.comment && !this.comment.trim()) out.push("코멘트");
      return out;
    },
  },
  mounted() {
    if (this.resolutions.length) this.resolution = this.resolutions[0].name;
    api.timetracking().then((t) => { this.tt = t; }).catch(() => {});
    this._ta = createTypeahead((q) => api.mentionUsers(q, this.ticket), { minLen: 0 });
    this.searchWho("");
    api.me().then((m) => {                       // 대개 자기 자신이다 — 기본값으로 채운다
      if (m && m.id && !this.user) {
        this.user = { id: m.id, name: m.name || m.id, display: m.name || m.id,
                      avatar: "/api/avatar/" + encodeURIComponent(m.id) };
      }
    }).catch(() => {});
  },
  methods: {
    searchWho(q) {
      this.hi = 0;
      this._ta.run(q).then((r) => { if (r) this.who = r.slice(0, 8); }).catch(() => {});
    },
    pickWho(u) { this.user = u; this.whoOpen = false; this.q = ""; this.who = []; },
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
    async submit() {
      if (this.problems.length || this.busy) return;
      this.busy = true; this.err = "";
      try {
        const r = await api.doTransition(this.ticket, {
          id: this.transition.id, days: Number(this.days) || 0,
          hours: Number(this.hours) || 0,
          minutes: Number(this.minutes) || 0,
          assignee: (this.user && this.user.id) || "",
          resolution: this.resolution,
          commentHtml: this.comment ? "<p>" + this.comment.replace(/[<&]/g, (c) =>
            (c === "<" ? "&lt;" : "&amp;")).replace(/\n/g, "<br>") + "</p>" : "",
        });
        if (r && r.ok === false) { this.err = r.error || "전이에 실패했습니다."; this.busy = false; return; }
        this.$emit("done");
      } catch (e) {
        // Jira 가 거절한 이유를 그대로 보인다 — 삼키면 무엇을 고쳐야 할지 알 수 없다.
        this.err = (e && e.message) || "전이에 실패했습니다.";
        this.busy = false;
      }
    },
  },
  template: `
  <div class="trx-ov" @click.self="$emit('close')">
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
          <span class="trx-l">소요시간 <i>필수</i></span>
          <span class="trx-time">
            <input type="number" min="0" max="99" v-model.number="days"><em :title="dayNote">일</em>
            <input type="number" min="0" max="999" v-model.number="hours"><em>시간</em>
            <input type="number" min="0" max="59" step="5" v-model.number="minutes"><em>분</em>
            <b class="trx-prev">{{ timeText }}</b>
          </span>
          <span class="trx-hint">실제 이 업무만을 위해 소요한 시간 기준으로 입력<i v-if="days"> · {{ dayNote }}</i></span>
        </label>

        <div v-if="has.assignee" class="trx-f">
          <span class="trx-l">담당자 <i>필수</i></span>
          <!-- 고른 사람은 **노드(칩)** 로 남는다 — 입력창에 글자로 남겨 두면 화면의 글자와
               실제 값이 어긋날 수 있고, 다 치고 못 고른 채 제출하는 사고가 난다. -->
          <span v-if="user" class="trx-chip">
            <Avatar :user="user.id" :name="user.display || user.name" :size="22" />
            <b>{{ user.name || user.display }}</b>
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
                <span>{{ u.name || u.display }}</span><em>{{ u.id }}</em>
              </button>
            </div>
            <span v-else-if="whoOpen" class="trx-hint">검색 결과가 없습니다.</span>
          </span>
        </div>

        <label v-if="has.resolution" class="trx-f">
          <span class="trx-l">처리 방법 <i>필수</i></span>
          <select v-model="resolution">
            <option v-for="r in resolutions" :key="r.id" :value="r.name">{{ r.name }}</option>
          </select>
        </label>

        <label v-if="has.comment" class="trx-f">
          <span class="trx-l">코멘트 <i>필수</i></span>
          <textarea v-model="comment" rows="3" placeholder="무엇을 했는지 한 줄이라도 남겨 주세요"></textarea>
          <span class="trx-hint">Jira 에선 선택이지만, 기록 없이 닫힌 티켓은 나중에 해석할 수 없어 이 앱에서는 받습니다.</span>
        </label>
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
