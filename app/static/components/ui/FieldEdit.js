// FieldEdit.js — 티켓 필드 인라인 편집 한 벌.
//
// **무엇을 고칠 수 있는지는 우리가 정하지 않는다.** 서버가 준 editmeta(= Jira 가 "지금 이
// 사용자가 이 이슈에서 편집 가능" 이라고 답한 목록)에 있는 필드만 편집 UI 를 연다. 추측해서
// 열어 두면 사용자가 다 입력한 뒤 저장에서 거절당한다 — 그건 기능이 아니라 함정이다.
//
// 편집 가능하면 값이 버튼처럼 눌리고, 아니면 그냥 글자다. 연필 아이콘을 따로 붙이지 않는 이유:
// 아이콘이 늘어나면 화면이 도구 모음처럼 보이고, 정작 '지금 무엇이 담겨 있나' 가 안 읽힌다.
// 대신 마우스를 올리면 배경이 들어와 "여기 누를 수 있다" 를 알린다.
import { api } from "../../lib/api.js";
import Avatar from "./Avatar.js";
import PriIcon, { priRankOf } from "./PriIcon.js";
import { createTypeahead } from "../../lib/typeahead.js";

const KO = {
  priority: "우선순위", assignee: "담당자", reporter: "보고자",
  duedate: "작업 기한", labels: "라벨", components: "컴포넌트",
};

export default {
  name: "FieldEdit",
  components: { Avatar, PriIcon },
  props: {
    ticket: { type: String, required: true },
    field: { type: String, required: true },      // priority | assignee | reporter | duedate | labels | components
    meta: { type: Object, default: null },        // editmeta[field] — 없으면 편집 불가
    value: { default: null },                     // 현재 값(문자열 또는 배열)
    display: { type: String, default: "" },       // 화면 표기(값과 다를 때: 담당자 본명 등)
    userId: { type: String, default: "" },        // 담당자/보고자 아바타용
  },
  emits: ["saved"],
  data() {
    return { open: false, busy: false, err: "", q: "", opts: [], hi: 0,
             draft: null, who: [] };
  },
  mounted() {
    // 다른 필드를 열면 이건 닫는다 — 여러 개가 동시에 떠 있으면 어느 것을 고치는 중인지
    // 알 수 없고, 뒤에 가린 팝업이 화면을 어지럽힌다.
    window.addEventListener("fe-open", this._onOther = (e) => {
      if (e.detail !== this._id()) this.open = false;
    });
    // Esc 로 닫힌다 — 팝업을 여는 UI 라면 당연히 있어야 한다(없으면 바깥을 찾아 눌러야 한다).
    window.addEventListener("keydown", this._onEsc = (e) => {
      if (e.key === "Escape" && this.open) { e.stopPropagation(); this.close(); }
    });
  },
  unmounted() {
    window.removeEventListener("fe-open", this._onOther);
    window.removeEventListener("keydown", this._onEsc);
  },
  computed: {
    editable() { return !!this.meta; },
    isUser() { return this.field === "assignee" || this.field === "reporter"; },
    isMulti() { return this.field === "labels" || this.field === "components"; },
    isDate() { return this.field === "duedate"; },
    // 라벨만 **새 값 생성**을 허용한다. 컴포넌트는 프로젝트 설정에 있는 것만 유효해
    // 아무거나 만들면 저장에서 거절된다(Jira 가 모르는 컴포넌트다).
    canCreate() { return this.field === "labels"; },
    // 화면 이름은 우리가 정한다 — Jira 가 준 이름(Priority/Assignee)은 편집 가능할 때만 오고,
    // 불가일 땐 메타 자체가 없어 필드 id 가 그대로 노출된다("priority — 수정 권한이 없습니다").
    label() { return KO[this.field] || (this.meta && this.meta.name) || this.field; },
    /** 편집 불가일 때 왜 안 되는지 알려 준다 — 아무 반응이 없으면 '고장' 으로 읽힌다. */
    roHint() { return this.label + " — 수정 권한이 없습니다"; },
  },
  methods: {
    _id() { return this.ticket + ":" + this.field; },
    rankOf: priRankOf,
    start() {
      if (!this.editable || this.busy) return;
      window.dispatchEvent(new CustomEvent("fe-open", { detail: this._id() }));
      this.open = true; this.err = ""; this.q = ""; this.hi = 0;
      this.draft = this.isMulti ? (this.value || []).slice() : this.value;
      if (this.field === "priority") {
        this.opts = (this.meta.allowedValues || []).map((v) => v.name);
      } else if (this.field === "components") {
        this.opts = (this.meta.allowedValues || []).map((v) => v.name);
        if (!this.opts.length) api.options("components").then((r) => { this.opts = (r || []).map((x) => x.name); });
      } else if (this.field === "labels") {
        this.suggest("");
      } else if (this.isUser) {
        this._ta = this._ta || createTypeahead((q) => api.mentionUsers(q, this.ticket),
                                               { minLen: 1, allowEmpty: true });
        this.searchWho("");
      }
      this.$nextTick(() => { const el = this.$refs.inp; if (el) el.focus(); });
    },
    close() { this.open = false; this.q = ""; this.err = ""; },
    suggest(q) {
      api.options("labels", q).then((r) => { this.opts = r || []; }).catch(() => { this.opts = []; });
    },
    searchWho(q) {
      this._ta.run(q).then((r) => { if (r) this.who = r.slice(0, 8); }).catch(() => {});
    },
    toggle(v) {
      const i = this.draft.indexOf(v);
      if (i >= 0) this.draft.splice(i, 1); else this.draft.push(v);
    },
    addNew() {
      const v = (this.q || "").trim();
      if (!v || this.draft.indexOf(v) >= 0) return;
      this.draft.push(v); this.q = ""; this.suggest("");
    },
    async save(v) {
      this.busy = true; this.err = "";
      const body = {};
      body[this.field === "duedate" ? "duedate" : this.field] = v;
      try {
        const r = await api.updateFields(this.ticket, body);
        if (r && r.ok === false) { this.err = r.error || "저장 실패"; this.busy = false; return; }
        this.close();
        this.$emit("saved");
      } catch (e) {
        // 거절 사유를 그대로 보인다 — 삼키면 무엇이 문제인지 알 수 없다.
        this.err = (e && e.message) || "저장 실패";
      } finally { this.busy = false; }
    },
    saveMulti() { this.save(this.draft.slice()); },
    clearUser() { this.save(""); },
  },
  template: `
  <span class="fe" :class="{ ro: !editable }">
    <button v-if="editable" class="fe-v" :class="{ on: open }" @click.stop="start"
            :title="label + ' 수정'"><slot>{{ display || value || '—' }}</slot></button>
    <span v-else class="fe-ro" :title="roHint"><slot>{{ display || value || '—' }}</slot></span>

    <span v-if="open" class="fe-pop" @click.stop>
      <!-- 우선순위 / 컴포넌트: 정해진 값 중에서만 -->
      <template v-if="field === 'priority'">
        <!-- '내 Task' 와 같은 아이콘·같은 등급 표 — 화면마다 다른 그림이면 같은 티켓이 달라 보인다 -->
        <button v-for="o in opts" :key="o" class="fe-i" :class="{ cur: o === value }"
                @click="save(o)"><PriIcon :rank="rankOf(o)" :name="o" />{{ o }}</button>
      </template>

      <!-- 담당자 / 보고자 -->
      <template v-else-if="isUser">
        <input ref="inp" :value="q" @input="q = $event.target.value; searchWho($event.target.value)"
               placeholder="이름 또는 사번">
        <div class="fe-list">
          <button v-for="u in who" :key="u.id" class="fe-i" :class="{ cur: u.id === userId }"
                  @click="save(u.id)">
            <Avatar :user="u.id" :name="u.display || u.name" :size="20" />
            <span>{{ u.display || u.name }}</span><em>{{ u.id }}</em>
          </button>
          <div v-if="!who.length" class="fe-none">결과가 없습니다.</div>
        </div>
        <button v-if="field === 'assignee' && userId" class="fe-clear" @click="clearUser">담당자 해제</button>
      </template>

      <!-- 작업 기한 -->
      <template v-else-if="isDate">
        <input ref="inp" type="date" :value="value || ''" @change="save($event.target.value)">
        <button v-if="value" class="fe-clear" @click="save('')">기한 지우기</button>
      </template>

      <!-- 라벨 / 컴포넌트 — 뱃지 담기 -->
      <template v-else-if="isMulti">
        <div class="fe-chips">
          <button v-for="v in draft" :key="v" class="fe-chip" @click="toggle(v)"
                  title="빼기">{{ v }}<i>×</i></button>
          <span v-if="!draft.length" class="fe-none">없음</span>
        </div>
        <input ref="inp" :value="q" @input="q = $event.target.value; canCreate && suggest($event.target.value)"
               @keydown.enter.prevent="canCreate ? addNew() : null"
               :placeholder="canCreate ? '검색 또는 새로 입력 후 Enter' : '검색'">
        <div class="fe-list">
          <button v-for="o in opts.filter(x => x.toLowerCase().includes(q.toLowerCase()))" :key="o"
                  class="fe-i" :class="{ cur: draft.includes(o) }" @click="toggle(o)">
            {{ o }}<em v-if="draft.includes(o)">담김</em>
          </button>
          <div v-if="!opts.length" class="fe-none">
            {{ canCreate ? '입력 후 Enter 로 새로 추가' : '선택지가 없습니다' }}
          </div>
        </div>
        <div class="fe-foot">
          <span v-if="err" class="fe-err">{{ err }}</span>
          <button class="fe-cancel" @click="close">취소</button>
          <button class="fe-ok" :disabled="busy" @click="saveMulti">{{ busy ? '저장 중…' : '저장' }}</button>
        </div>
      </template>

      <div v-if="err && !isMulti" class="fe-err">{{ err }}</div>
    </span>
    <span v-if="open" class="fe-back" @click.stop="close"></span>
  </span>`,
};
