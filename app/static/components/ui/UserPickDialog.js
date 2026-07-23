// UserPickDialog.js — 사람 한 명 고르기(담당자 변경).
//
// 전이 화면의 담당자 칸과 **같은 규칙**을 쓴다: 자유 입력이 아니라 목록에서 고르고, 전체
// 표시이름(본명 + 소속)을 보여 준다 — 동명이인이 있으면 본명만으론 누구인지 못 고른다.
// 빈 검색어에서는 이 티켓 관련자가 먼저 뜬다(서버가 티켓 key 로 판단).
import { api } from "../../lib/api.js";
import Avatar from "./Avatar.js";
import { createTypeahead } from "../../lib/typeahead.js";

export default {
  name: "UserPickDialog",
  components: { Avatar },
  props: { ticket: { type: String, required: true }, current: { type: String, default: "" } },
  emits: ["close", "pick"],
  data() { return { q: "", who: [], hi: 0, loading: true }; },
  mounted() {
    // ★ allowEmpty 없이는 빈 검색어에서 무조건 빈 목록이라 창을 열자마자 아무것도 안 보인다.
    this._ta = createTypeahead((q) => api.mentionUsers(q, this.ticket),
                               { minLen: 1, allowEmpty: true });
    this.search("");
    this.$nextTick(() => { const el = this.$refs.q; if (el) el.focus(); });
  },
  methods: {
    search(q) {
      this.loading = true; this.hi = 0;
      this._ta.run(q).then((r) => {
        if (r) { this.who = r.slice(0, 12); this.loading = false; }
      }).catch(() => { this.loading = false; });
    },
    onKey(e) {
      if (e.key === "ArrowDown") { e.preventDefault(); this.hi = (this.hi + 1) % (this.who.length || 1); }
      else if (e.key === "ArrowUp") { e.preventDefault(); this.hi = (this.hi + this.who.length - 1) % (this.who.length || 1); }
      else if (e.key === "Enter" && this.who[this.hi]) { e.preventDefault(); this.$emit("pick", this.who[this.hi]); }
    },
  },
  template: `
  <div class="trx-ov" @click.self="$emit('close')">
    <div class="trx up">
      <div class="trx-h"><b>담당자 변경</b><span class="trx-key">{{ ticket }}</span>
        <button class="trx-x" @click="$emit('close')" title="닫기">×</button></div>
      <div class="up-b">
        <input ref="q" :value="q" @input="q = $event.target.value; search($event.target.value)"
               @keydown="onKey" placeholder="이름 또는 사번으로 검색">
        <div class="up-list">
          <button v-for="(u, i) in who" :key="u.id" class="up-i"
                  :class="{ hi: i === hi, cur: u.id === current }"
                  @click="$emit('pick', u)" @mouseenter="hi = i">
            <Avatar :user="u.id" :name="u.display || u.name" :size="24" />
            <span>{{ u.display || u.name }}</span>
            <em>{{ u.id }}</em>
            <i v-if="u.id === current" title="현재 담당자">현재</i>
          </button>
          <div v-if="!who.length" class="tkm-note">{{ loading ? '찾는 중…' : '결과가 없습니다.' }}</div>
        </div>
      </div>
    </div>
  </div>`,
};
