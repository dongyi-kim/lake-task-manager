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
import TypeBadge from "./TypeBadge.js";
import { createTypeahead } from "../../lib/typeahead.js";
import { createUserTypeahead, defaultUserSuggestions, rememberUser } from "../../lib/userSuggestions.js";
import { categoryColor } from "../../lib/colors.js";

const KO = {
  priority: "우선순위", assignee: "담당자", reporter: "보고자",
  duedate: "작업 기한", labels: "라벨", components: "컴포넌트", epic: "소속 Epic",
  issuetype: "티켓 타입",
};

export default {
  name: "FieldEdit",
  components: { Avatar, PriIcon, TypeBadge },
  props: {
    ticket: { type: String, required: true },
    field: { type: String, required: true },      // priority | assignee | reporter | duedate | labels | components
    meta: { type: Object, default: null },        // editmeta[field] — 없으면 편집 불가
    value: { default: null },                     // 현재 값(문자열 또는 배열)
    display: { type: String, default: "" },       // 화면 표기(값과 다를 때: 담당자 본명 등)
    userId: { type: String, default: "" },        // 담당자/보고자 아바타용
    // **아직 서버에 없는 티켓**(새로 만드는 줄)도 같은 팝업으로 고르게 한다. 저장은 하지 않고
    // 고른 값을 부모에게 넘긴다 — 새 티켓만 다른 입력기를 쓰면, 만들 때와 고칠 때 조작이 달라진다.
    local: { type: Boolean, default: false },
    // local 일 때의 선택지(우선순위·타입). 이름을 opts 로 두면 **data 의 opts 와 충돌**해
    // 목록이 늘 빈 채로 뜬다(실제로 그랬다) — 프롭과 상태는 이름을 겹치면 안 된다.
    choices: { type: Array, default: null },
  },
  emits: ["saved", "pick"],
  data() {
    return { open: false, busy: false, err: "", q: "", opts: [], hi: 0, lookupSeq: 0,
             draft: null, who: [], dateDraft: "",   // 날짜 텍스트 입력(자동 YYYY-MM-DD 포맷)
             // 팝업을 body 로 teleport 해 fixed 로 띄운다 — 안 그러면 스크롤되는 다이얼로그
             // (overflow:auto) 안에 갇혀 긴 목록이 잘린다. popStyle 은 트리거 기준 위치.
             popStyle: {} };
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
    window.removeEventListener("scroll", this._place, true);      // 열린 채 언마운트돼도 새지 않게
    window.removeEventListener("resize", this._place);
  },
  computed: {
    editable() { return this.local || !!this.meta; },
    isUser() { return this.field === "assignee" || this.field === "reporter"; },
    isMulti() { return this.field === "labels" || this.field === "components"; },
    isDate() { return this.field === "duedate"; },
    isEpic() { return this.field === "epic"; },
    isType() { return this.field === "issuetype"; },
    // Jira 가 선택 해제를 허용하는 필드는 검색 결과와 별개로 `없음`을 항상 고를 수 있어야 한다.
    // 서버 결과 안에 가짜 옵션으로 섞으면 검색어에 걸러지거나 늦은 응답과 함께 사라지므로 UI에 고정한다.
    hasNoneOption() {
      if (!this.isUser && !this.isEpic && !this.isDate && !this.isMulti) return false;
      return this.local || !this.meta || this.meta.required !== true;
    },
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
    epicColor(key) { return categoryColor(key); },   // Epic 시그니처 컬러(전 화면 공통)
    start() {
      if (!this.editable || this.busy) return;
      window.dispatchEvent(new CustomEvent("fe-open", { detail: this._id() }));
      this.open = true; this.err = ""; this.q = ""; this.hi = 0;
      this.lookupSeq += 1;                         // 이전에 닫힌 팝업의 늦은 응답 무효화
      // 트리거 기준으로 팝업을 놓고, 스크롤/리사이즈 때 따라오게 한다(닫히면 뗀다).
      this.$nextTick(() => this._place());
      window.addEventListener("scroll", this._place, true);
      window.addEventListener("resize", this._place);
      this.draft = this.isMulti ? (this.value || []).slice() : this.value;
      if (this.isDate) this.dateDraft = this.value || "";
      if (this.local) {
        // 선택지는 부모가 준다(아직 티켓이 없어 editmeta 가 없다). 사용자 검색만 평소와 같다.
        // 목록형(컴포넌트)은 화면이 opts 를 그리므로 거기에도 넣어 준다.
        this.opts = (this.choices || []).slice();
        // 사용자·날짜·Epic·다중(라벨/컴포넌트)은 아래에서 검색기/목록을 붙인다(local 이어도 필요) —
        // 그 외(우선순위·타입)만 choices 로 끝내고 바로 포커스한다.
        if (!this.isUser && !this.isDate && !this.isEpic && !this.isMulti) return this._focus();
      }
      if (this.field === "priority") {
        this.opts = (this.meta.allowedValues || []).map((v) => v.name);
      } else if (this.field === "components") {
        // local choices와 editmeta는 네트워크와 무관한 기본 목록이다. 먼저 그려 둔 뒤, 둘 다 없을
        // 때만 서버 목록을 보충한다. 이전에는 local choices를 빈 meta로 덮어써 목록이 사라졌다.
        const localBase = (this.local ? (this.choices || []) : []).map((v) => typeof v === "string" ? v : v.name).filter(Boolean);
        const metaBase = ((this.meta && this.meta.allowedValues) || []).map((v) => v.name);
        const base = localBase.concat(metaBase);
        this.opts = this._prepRecentStr(base);
        if (!base.length) {
          const token = ++this.lookupSeq;
          api.options("components").then((r) => {
            if (this._lookupCurrent(token, "")) this.opts = this._prepRecentStr((r || []).map((x) => x.name));
          }).catch(() => {});                       // 즉시 표시한 최근 목록은 실패해도 유지
        }
      } else if (this.field === "labels") {
        this.suggest("");
      } else if (this.isEpic) {
        this.searchEpics("");
      } else if (this.isUser) {
        const localUsers = (this.local ? (this.choices || []) : []).filter((u) => u && typeof u === "object");
        this._ta = this._ta || createUserTypeahead(this.ticket, localUsers);
        this.searchWho("");
      }
      this._focus();
    },
    _focus() {
      // preventScroll — 팝업 입력에 포커스를 줄 때 브라우저가 그 자리로 **페이지를 스크롤**하는 걸
      // 막는다(퀵필터 picker 를 누르면 화면이 아래로 튀던 버그).
      this.$nextTick(() => { const el = this.$refs.inp; if (el) el.focus({ preventScroll: true }); });
    },
    // 팝업(teleport)을 트리거 버튼 기준으로 fixed 배치 — 아래가 좁으면 위로 뒤집는다.
    _place() {
      const t = this.$refs.fev;
      if (!t) return;
      const r = t.getBoundingClientRect();
      const vw = window.innerWidth, vh = window.innerHeight;
      const pop = this.$refs.pop;
      const ph = (pop && pop.offsetHeight) || 240;                 // 렌더 전 근사치
      const style = { position: "fixed", left: Math.max(8, Math.min(r.left, vw - 360)) + "px", zIndex: 9700 };
      if (r.bottom + 6 + ph > vh - 8 && r.top - 6 > vh - r.bottom) {   // 아래가 모자라고 위가 더 넓으면 위로
        style.bottom = (vh - r.top + 6) + "px"; style.top = "auto";
      } else {
        style.top = (r.bottom + 6) + "px"; style.bottom = "auto";
      }
      this.popStyle = style;
    },
    close() {
      this.open = false; this.q = ""; this.err = "";
      this.lookupSeq += 1;
      for (const ta of [this._ta, this._taLbl, this._taEp]) if (ta) ta.cancel();
      window.removeEventListener("scroll", this._place, true);
      window.removeEventListener("resize", this._place);
    },
    _lookupCurrent(token, q) {
      return this.open && token === this.lookupSeq && this.q === q;
    },
    suggest(q) {
      q = String(q || "");
      const token = ++this.lookupSeq;
      if (!q) {
        // 최근값/local choices는 서버 로딩과 무관한 오프라인 기본 추천이다. 요청 전에 즉시 그린다.
        const base = (this.local ? (this.choices || []) : []).map((v) => typeof v === "string" ? v : v.name).filter(Boolean);
        this.opts = this._prepRecentStr(base);
        if (this._taLbl) this._taLbl.cancel();   // 대기 중이던 타이핑 응답 취소(빈 목록을 덮지 않게)
        api.options("labels", "").then((r) => {
          if (this._lookupCurrent(token, q)) this.opts = this._prepRecentStr(base.concat(r || []));
        }).catch(() => {});                       // 오프라인 추천을 빈 배열로 되돌리지 않는다
        return;
      }
      // 타이핑 — **디바운스 + 응답 역전 방어**(typeahead). 늦게 온 옛 질의 결과는 버려 **마지막
      // 글자 결과만** 반영된다(전엔 매 키마다 직접 호출이라 옛 응답이 새 목록을 덮을 수 있었다).
      this._taLbl = this._taLbl || createTypeahead((x) => api.options("labels", x), { minLen: 1 });
      this._taLbl.run(q).then((r) => {
        if (r != null && this._lookupCurrent(token, q)) this.opts = r || [];
      }).catch(() => {});
    },
    searchEpics(q) {
      q = String(q || "");
      const token = ++this.lookupSeq;
      if (!q) {
        const base = (this.local ? (this.choices || []) : []).filter((e) => e && typeof e === "object");
        this.opts = this._prepRecent(base, (e) => e.key || e.id);
        if (this._taEp) this._taEp.cancel();
        api.options("epics", "").then((r) => {
          if (this._lookupCurrent(token, q)) this.opts = this._prepRecent(base.concat(r || []), (e) => e.key || e.id);
        }).catch(() => {});
        return;
      }
      this._taEp = this._taEp || createTypeahead((x) => api.options("epics", x), { minLen: 1 });
      this._taEp.run(q).then((r) => {
        if (r != null && this._lookupCurrent(token, q)) this.opts = r || [];
      }).catch(() => {});
    },
    searchWho(q) {
      q = String(q || "");
      const token = ++this.lookupSeq;
      if (!q) {
        const base = (this.local ? (this.choices || []) : []).filter((u) => u && typeof u === "object");
        this.who = defaultUserSuggestions([], base);
      }
      this._ta.run(q).then((r) => {
        if (!r || !this._lookupCurrent(token, q)) return;
        this.who = r;
      }).catch(() => {});
    },
    // ── 최근 사용값(이 필드에서 내가 고른 값) — 기본 목록 상단에 우선 노출 ──
    _rkey() { return "fe.recent." + this.field; },
    _recent() {
      try { return JSON.parse(localStorage.getItem(this._rkey()) || "[]") || []; } catch (e) { return []; }
    },
    _pushRecent(item) {
      if (!item || !item.id) return;
      const cur = this._recent().filter((x) => x.id !== item.id);
      cur.unshift(item);
      try { localStorage.setItem(this._rkey(), JSON.stringify(cur.slice(0, 6))); } catch (e) { /* 사파리 등 */ }
    },
    _recItem(v, extra) {
      if (this.isUser && extra) return { id: extra.id || v, name: extra.name, display: extra.display };
      if (this.isEpic && extra) return { id: v, key: v, name: extra.name, summary: extra.summary };
      return { id: String(v), label: String(v) };
    },
    /** 객체 목록 앞에 최근값(같은 shape)을 끼워 넣고 중복 제거. */
    _prepRecent(list, idOf) {
      const out = [], seen = new Set();
      for (const item of this._recent().concat(list || [])) {
        const id = item && (item.id || idOf(item));
        if (!id || seen.has(String(id))) continue;
        seen.add(String(id)); out.push(item);
      }
      return out;
    },
    /** 문자열 목록(라벨·컴포넌트) 앞에 최근값을 끼워 넣고 중복 제거. */
    _prepRecentStr(list) {
      const out = [], seen = new Set();
      for (const value of this._recent().map((x) => x.id).concat(list || [])) {
        const text = String(value || "");
        if (!text || seen.has(text)) continue;
        seen.add(text); out.push(text);
      }
      return out;
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
    async save(v, extra) {
      if (v && !Array.isArray(v)) {
        const recent = this._recItem(v, extra);
        if (this.isUser) rememberUser(recent); else this._pushRecent(recent);  // 사용자는 멘션과 공용
      }
      if (this.local) {
        // 아직 티켓이 없다 — 서버에 보낼 것이 없으므로 고른 값만 넘긴다.
        this.$emit("pick", v, extra || null);
        this.close();
        return;
      }
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
    saveMulti() {
      for (const val of this.draft) this._pushRecent({ id: String(val), label: String(val) });
      this.save(this.draft.slice());
    },
    clearMulti() { this.draft = []; },
    clearUser() { this.save(""); },
    // ── 날짜 입력(자유 타이핑) ──
    // 네이티브 <input type=date> 는 세그먼트 편집이라 '20260417' 을 심리스하게 못 치고, 중간
    // 백스페이스에서 무너진다. **텍스트 입력**으로 받아 숫자만 뽑아 YYYY-MM-DD 로 자동 포맷한다.
    _fmtDate(s) {
      const d = String(s || "").replace(/\D/g, "").slice(0, 8);   // YYYYMMDD
      if (d.length <= 4) return d;
      if (d.length <= 6) return d.slice(0, 4) + "-" + d.slice(4);
      return d.slice(0, 4) + "-" + d.slice(4, 6) + "-" + d.slice(6);
    },
    _validDate(s) {
      const m = /^(\d{4})-(\d{2})-(\d{2})$/.exec(s || "");
      if (!m) return false;
      const y = +m[1], mo = +m[2], da = +m[3];
      if (mo < 1 || mo > 12 || da < 1 || da > 31) return false;
      const dt = new Date(y, mo - 1, da);           // 존재하는 날짜인지(2월 30일 등 거름)
      return dt.getFullYear() === y && dt.getMonth() === mo - 1 && dt.getDate() === da;
    },
    onDateInput(e) {
      this.err = "";
      const f = this._fmtDate(e.target.value);
      this.dateDraft = f;
      e.target.value = f;                            // 대시 자동삽입을 즉시 화면에 반영
    },
    commitDate() {
      const s = (this.dateDraft || "").trim();
      if (!s) { this.save(""); return; }             // 비우면 기한 해제
      if (this._validDate(s)) this.save(s);
      else this.err = "날짜는 YYYY-MM-DD (예: 2026-04-17) 형식이어야 합니다.";
    },
    openNative() {
      const el = this.$refs.nat;
      if (!el) return;
      try { el.showPicker ? el.showPicker() : el.click(); } catch (e) { el.click(); }
    },
    onNative(e) {
      const v = e.target.value || "";
      this.dateDraft = v;
      if (v) this.save(v); else this.save("");
    },
  },
  template: `
  <span class="fe" :class="{ ro: !editable }">
    <button v-if="editable" ref="fev" class="fe-v" :class="{ on: open }" @click.stop="start"
            :title="label + ' 수정'"><slot>{{ display || value || '—' }}</slot></button>
    <span v-else class="fe-ro" :title="roHint"><slot>{{ display || value || '—' }}</slot></span>

    <Teleport to="body">
    <span v-if="open" ref="pop" class="fe-pop fe-pop-fixed" :class="{ wide: isEpic, users: isUser }"
          :style="popStyle" @click.stop>
      <!-- 우선순위 / 컴포넌트: 정해진 값 중에서만 -->
      <template v-if="field === 'priority'">
        <!-- '내 Task' 와 같은 아이콘·같은 등급 표 — 화면마다 다른 그림이면 같은 티켓이 달라 보인다 -->
        <button v-for="o in (local ? (choices || []) : opts)" :key="o" class="fe-i"
                :class="{ cur: o === value }"
                @click="save(o)"><PriIcon :rank="rankOf(o)" :name="o" />{{ o }}</button>
      </template>

      <!-- 티켓 타입 — 새로 만드는 줄에서만 쓴다(기존 티켓의 타입 변경은 워크플로가 걸린다) -->
      <template v-else-if="isType">
        <button v-for="o in (choices || [])" :key="o" class="fe-i" :class="{ cur: o === value }"
                @click="save(o)"><TypeBadge :type="o" />{{ o }}</button>
      </template>

      <!-- 담당자 / 보고자 -->
      <template v-else-if="isUser">
        <input ref="inp" :value="q" @input="q = $event.target.value; searchWho($event.target.value)"
               placeholder="이름 또는 사번">
        <div class="fe-list">
          <button v-if="hasNoneOption" class="fe-i fe-empty" :class="{ cur: !userId }" @click="clearUser">
            <span class="fe-empty-mark">—</span><span>없음</span>
          </button>
          <button v-for="u in who" :key="u.id" class="fe-i" :class="{ cur: u.id === userId }"
                  @click="save(u.id, u)">
            <Avatar :user="u.id" :name="u.display || u.name" :size="20" />
            <span>{{ u.display || u.name }}</span><em>{{ u.id }}</em>
          </button>
          <div v-if="!who.length" class="fe-none">결과가 없습니다.</div>
        </div>
      </template>

      <!-- 소속 Epic -->
      <template v-else-if="isEpic">
        <input ref="inp" :value="q" @input="q = $event.target.value; searchEpics($event.target.value)"
               placeholder="Epic 이름·제목 또는 키">
        <div class="fe-list">
          <button v-if="hasNoneOption" class="fe-i fe-empty" :class="{ cur: !value }" @click="save('')">
            <span class="fe-empty-mark">—</span><span>없음</span>
          </button>
          <!-- Epic 은 이름이 둘이다: **단축어(Epic Name)** 와 요약. 사람들은 단축어로 부르지만
               비슷한 단축어끼리는 요약을 봐야 구별된다 — 그래서 둘 다 보인다. -->
          <!-- Task 생성 시 상위 Epic 피커(NewChildDialog .nk-cand-epic)와 **같은 포맷**:
               [키] [Epic Summary] [시그니처색 뱃지=Epic Name(우측)] -->
          <button v-for="e in opts" :key="e.key" class="fe-i epic" :class="{ cur: e.key === value }"
                  @click="save(e.key, e)">
            <b class="fe-epic-k">{{ e.key }}</b>
            <span class="fe-epic-s">{{ e.summary || e.name }}</span>
            <span class="fe-epic-badge" :style="{ '--ec': epicColor(e.key) }">{{ e.name }}</span>
          </button>
          <div v-if="!opts.length" class="fe-none">Epic 이 없습니다.</div>
        </div>
      </template>

      <!-- 작업 기한 — 자유 타이핑(YYYYMMDD 자동 포맷) + 달력 버튼 -->
      <template v-else-if="isDate">
        <div class="fe-date">
          <input ref="inp" class="fe-date-t" :value="dateDraft" @input="onDateInput"
                 @keydown.enter.prevent="commitDate" @blur="commitDate"
                 placeholder="YYYY-MM-DD" inputmode="numeric" maxlength="10" autocomplete="off">
          <button type="button" class="fe-date-cal" @mousedown.prevent="openNative" title="달력에서 고르기"
                  aria-label="달력">
            <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round">
              <rect x="3" y="4.5" width="18" height="17" rx="2"/><path d="M3 9h18M8 2.5v4M16 2.5v4"/></svg>
          </button>
          <input ref="nat" type="date" class="fe-date-native" :value="value || ''" @change="onNative" tabindex="-1">
        </div>
        <div v-if="err" class="fe-err">{{ err }}</div>
        <button v-if="hasNoneOption" class="fe-clear" @mousedown.prevent @click="save('')">없음</button>
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
          <button v-if="hasNoneOption" class="fe-i fe-empty" :class="{ cur: !draft.length }" @click="clearMulti">
            <span class="fe-empty-mark">—</span><span>없음</span><em v-if="!draft.length">선택됨</em>
          </button>
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
    <span v-if="open" class="fe-back fe-back-fixed" @click.stop="close"></span>
    </Teleport>
  </span>`,
};
