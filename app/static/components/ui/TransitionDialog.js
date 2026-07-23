// TransitionDialog.js — 상태 전이 화면.
//
// Jira 의 전이 화면을 우리가 대신 그린다. 무엇을 물어야 하는지는 서버가 준 필드 목록이
// 정한다(?expand=transitions.fields) — 워크플로마다 다르므로 화면에 박아 두면 안 된다.
//
// 두 가지를 일부러 Jira 와 다르게 한다:
//  1) **소요시간을 시/분 숫자로 받는다.** Jira 는 "1d 5h" 같은 문자열을 직접 치게 하는데
//     오타가 잦다(5h 를 5 로만 쓰거나, 공백을 빠뜨리거나). 숫자 두 칸으로 받아 서버에서
//     조립한다. 일(d) 단위는 안 쓴다 — 하루가 8시간인지 24시간인지가 인스턴스 설정에 달려
//     있어 같은 '1d' 가 다른 값이 된다.
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
      hours: 0, minutes: 0, assignee: "", assigneeName: "", resolution: "",
      comment: "", who: [], whoOpen: false, busy: false, err: "",
    };
  },
  computed: {
    fields() { return (this.transition.fields && this.transition.fields.fields) || []; },
    unsupported() { return (this.transition.fields && this.transition.fields.unsupported) || []; },
    has() { const m = {}; for (const f of this.fields) m[f.id] = f; return m; },
    resolutions() { return (this.has.resolution && this.has.resolution.allowedValues) || []; },
    timeText() {
      const p = [];
      if (this.hours) p.push(this.hours + "h");
      if (this.minutes) p.push(this.minutes + "m");
      return p.join(" ") || "—";
    },
    problems() {
      const out = [];
      if (this.has.worklog && !(this.hours || this.minutes)) out.push("소요시간");
      if (this.has.assignee && !this.assignee) out.push("담당자");
      if (this.has.resolution && !this.resolution) out.push("처리 방법");
      // 코멘트는 Jira 기준 선택이지만 우리는 필수로 둔다(위 주석 참고).
      if (this.has.comment && !this.comment.trim()) out.push("코멘트");
      return out;
    },
  },
  mounted() {
    if (this.resolutions.length) this.resolution = this.resolutions[0].name;
    this._ta = createTypeahead((q) => api.mentionUsers(q, this.ticket), { minLen: 0 });
    this.searchWho("");
    api.me().then((m) => {                       // 대개 자기 자신이다 — 기본값으로 채운다
      if (m && m.id && !this.assignee) { this.assignee = m.id; this.assigneeName = m.name || m.id; }
    }).catch(() => {});
  },
  methods: {
    searchWho(q) {
      this._ta.run(q).then((r) => { if (r) this.who = r.slice(0, 8); }).catch(() => {});
    },
    pickWho(u) {
      this.assignee = u.id || u.name; this.assigneeName = u.display || u.name || u.id;
      this.whoOpen = false;
    },
    async submit() {
      if (this.problems.length || this.busy) return;
      this.busy = true; this.err = "";
      try {
        const r = await api.doTransition(this.ticket, {
          id: this.transition.id, hours: Number(this.hours) || 0,
          minutes: Number(this.minutes) || 0, assignee: this.assignee,
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
            <input type="number" min="0" max="999" v-model.number="hours"><em>시간</em>
            <input type="number" min="0" max="59" step="5" v-model.number="minutes"><em>분</em>
            <b class="trx-prev">{{ timeText }}</b>
          </span>
          <span class="trx-hint">Jira 에는 {{ timeText }} 형식으로 기록됩니다.</span>
        </label>

        <label v-if="has.assignee" class="trx-f">
          <span class="trx-l">담당자 <i>필수</i></span>
          <span class="trx-who">
            <input :value="assigneeName" @focus="whoOpen = true"
                   @input="assigneeName = $event.target.value; assignee = ''; whoOpen = true; searchWho($event.target.value)"
                   placeholder="이름 또는 사번">
            <div v-if="whoOpen && who.length" class="trx-drop">
              <button v-for="u in who" :key="u.id || u.name" @click.prevent="pickWho(u)">
                <Avatar :user="u.id || u.name" :name="u.display || u.name" :size="18" />
                <span>{{ u.display || u.name }}</span><em>{{ u.id || u.name }}</em>
              </button>
            </div>
          </span>
        </label>

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
