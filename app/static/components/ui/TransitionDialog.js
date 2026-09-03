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
import { confirmBox } from "../../lib/confirm.js";
import { createUserTypeahead, defaultUserSuggestions, rememberUser } from "../../lib/userSuggestions.js";
import { clearPendingMutation, loadPendingMutation, newMutationId,
         savePendingMutation } from "../../lib/mutationId.js";

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
      transitionMutationId: "", pendingTransitionPayload: null,
    };
  },
  computed: {
    fields() {
      const raw = this.transition.fields;
      if (Array.isArray(raw)) return raw;
      const source = raw && Object.prototype.hasOwnProperty.call(raw, "fields")
        ? raw.fields : raw;
      if (Array.isArray(source)) return source;
      // Tolerate un-normalized Jira DC metadata too (`fields: {comment: {...}}` or the direct
      // field map). Normal API responses use the array form, but a cached older shell/cascade
      // should not make the Resolve editor disappear during a rolling app upgrade.
      if (source && typeof source === "object") {
        return Object.entries(source).filter(([, value]) => value && typeof value === "object")
          .map(([id, value]) => Object.assign({ id }, value));
      }
      return [];
    },
    unsupported() {
      const raw = this.transition.fields;
      return (raw && Array.isArray(raw.unsupported)) ? raw.unsupported : [];
    },
    has() {
      const mapped = {};
      for (const field of this.fields) {
        if (field.id) mapped[field.id] = field;
        // Jira DC may expose a custom field key while schema.system carries the stable semantic
        // name used by the form. Keep the explicit id authoritative and add the system alias.
        if (field.system && !mapped[field.system]) mapped[field.system] = field;
      }
      return mapped;
    },
    // Some Jira DC workflows omit `comment` from transition screen metadata even though the
    // transition REST endpoint accepts update.comment. LTM completion policy still requires a
    // record, so a Done transition always exposes the shared rich comment editor.
    needsComment() { return this.transition.toCategory === "done" || !!this.has.comment; },
    pendingCommentHtml() {
      return (this.pendingTransitionPayload && this.pendingTransitionPayload.commentHtml) || "";
    },
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
  created() {
    // Restore before child components mount. CommentEditor reads `initial` only while creating its
    // TipTap instance, so restoring in mounted() would leave a response-lost comment visually blank.
    const saved = loadPendingMutation(this.pendingScope());
    if (saved && saved.payload) {
      const payload = saved.payload;
      this.transitionMutationId = String(saved.id || payload.clientMutationId || "");
      this.pendingTransitionPayload = payload;
      this.days = Number(payload.days) || 0;
      this.hours = Number(payload.hours) || 0;
      this.minutes = Number(payload.minutes) || 0;
      this.resolution = payload.resolution || "";
      // Empty is also a deliberate value in the exact request; do not let async defaults change
      // a response-lost transition after a renderer/app restart.
      this._userTouched = true;
      this.user = payload.assignee ? {
        id: payload.assignee, name: payload.assignee, display: payload.assignee,
        avatar: "/api/avatar/" + encodeURIComponent(payload.assignee),
      } : null;
      this.err = "이전 전환의 Jira 반영 여부를 확인해야 합니다. 같은 내용으로 다시 시도해 주세요.";
    }
  },
  mounted() {
    if (!this.pendingTransitionPayload && this.resolutions.length) {
      // 완료 전환에서는 Jira 선택지 순서와 무관하게 Done을 기본 처리 방법으로 둔다.
      // 워크플로에 Done이 없거나 완료 전환이 아니면 Jira가 준 첫 값을 그대로 존중한다.
      const done = this.transition.toCategory === "done"
        ? this.resolutions.find((item) => String(item.name || "").trim().toLowerCase() === "done")
        : null;
      this.resolution = (done || this.resolutions[0]).name;
    }
    api.timetracking().then((t) => { this.tt = t; }).catch(() => {});
    // ★ allowEmpty 가 없으면 **빈 검색어에서 무조건 빈 배열**이라 칸을 눌러도 아무것도 안 뜬다
    //   — 사용자에겐 "검색이 동작 안 한다" 로 보인다. 빈 검색어는 이 티켓 관련자를 먼저 주므로
    //   (서버가 key 로 판단) 오히려 가장 쓸모 있는 첫 화면이다.
    this._ta = createUserTypeahead(this.ticket, []);
    this.who = defaultUserSuggestions([], []);
    this.searchWho("");
    if (!this.pendingTransitionPayload) this.initAssignee();
    // Parent menus also listen for Escape and can otherwise unmount this dialog without calling
    // closeGuarded(). Capture it first so unsaved text gets the same explicit discard choice and
    // an in-flight/uncertain payload remains visible instead of disappearing behind the parent.
    this._guardEscape = (event) => {
      // confirmBox owns Escape while its own overlay is open.
      if (event.key !== "Escape" || this._confirmingClose) return;
      event.preventDefault(); event.stopImmediatePropagation();
      this.closeGuarded();
    };
    window.addEventListener("keydown", this._guardEscape, true);
  },
  unmounted() {
    if (this._guardEscape) window.removeEventListener("keydown", this._guardEscape, true);
  },
  methods: {
    // 드래그가 창 밖에서 끝났을 뿐인데 닫히지 않게 — lib/backdrop.js 참고
    fromBackdrop,
    pendingScope() {
      return "transition:" + String(this.ticket || "").toUpperCase()
        + ":" + String(this.transition.id || "");
    },
    async closeGuarded() {
      if (this._confirmingClose) return;
      if (this.busy || this.transitionMutationId) {
        this.err = this.busy
          ? "전환 처리 중에는 창을 닫을 수 없습니다."
          : "Jira 반영 여부 확인이 끝날 때까지 창을 유지하고 같은 요청으로 다시 시도해 주세요.";
        return;
      }
      const editor = this.$refs.ed;
      if (editor && editor.isBlank && !editor.isBlank()) {
        // X/배경/Escape/취소가 작성 중인 코멘트와 붙여넣은 이미지를 조용히 버리지 않게 한다.
        // "계속 작성"은 창과 초안을 그대로 두고, 명시적으로 버린 경우에만 scope를 지운다.
        this._confirmingClose = true;
        let discard = false;
        try {
          discard = await confirmBox("작성 중인 전환 코멘트를 버리고 닫을까요?", {
            okLabel: "버리고 닫기", cancelLabel: "계속 작성", danger: true,
          });
        } finally { this._confirmingClose = false; }
        if (!discard) {
          if (editor.flushDraft) await editor.flushDraft();
          return;
        }
      }
      // 비어 보이는 scope에도 이전 debounce/write가 남을 수 있다. 닫기 전에 명시적으로 지워
      // 다음에 같은 전이를 열었을 때 지운 코멘트가 되살아나지 않게 한다.
      if (editor && editor.discardDraft && await editor.discardDraft() === false) return;
      this.$emit("close");
    },
    /** 담당자 입력이 있는 전이는 현재 티켓 담당자를 기본값으로 쓴다. ticketBadge는 카드·메뉴가
     *  이미 데운 가벼운 캐시를 재사용한다. 미할당/조회 실패일 때만 기존 동작대로 나를 넣는다. */
    async initAssignee() {
      if (!this.has.assignee) return;
      try {
        const current = await api.ticketBadge(this.ticket);
        if (this._userTouched) return;
        const id = current && current.assigneeId;
        if (id) {
          const name = current.assignee || id;
          this.user = { id, name, display: current.assigneeDisplay || name,
                        avatar: "/api/avatar/" + encodeURIComponent(id) };
          return;
        }
      } catch (e) { /* 현재 담당자를 못 읽으면 아래의 나 기본값으로 폴백한다. */ }
      try {
        const me = await api.me();
        if (this._userTouched || !me || !me.id) return;
        this.user = { id: me.id, name: me.name || me.id, display: me.display || me.name || me.id,
                      avatar: "/api/avatar/" + encodeURIComponent(me.id) };
      } catch (e) { /* 필수 여부와 오류 표시는 기존 problems/제출 경로가 담당한다. */ }
    },
    searchWho(q) {
      this.hi = 0;
      if (!String(q || "").trim()) this.who = defaultUserSuggestions([], []);
      this._ta.run(q).then((r) => { if (r) this.who = r.slice(0, 8); }).catch(() => {});
    },
    pickWho(u) { this._userTouched = true; rememberUser(u); this.user = u; this.whoOpen = false; this.q = ""; this.who = []; },
    clearWho() {
      this._userTouched = true;
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
      if (this.needsComment && this.$refs.ed) { this.$refs.ed.submit(); return; }
      this.busy = true;
      await this.sendTransition("");
    },
    /** 실제 전송. 에디터가 부르는 경로라 실패는 **던져야** 한다 — 에디터가 그걸 받아
     *  올린 이미지를 되돌리고 사용자에게 알린다(조용히 삼키면 첨부만 남는다). */
    async sendTransition(html) {
      this.busy = true;
      let r;
      let payload = this.pendingTransitionPayload;
      if (!payload) {
        this.transitionMutationId = newMutationId("transition");
        payload = {
          id: this.transition.id,
          targetStatusId: this.transition.toId || "",
          targetStatusName: this.transition.to || "",
          targetStatusCategory: this.transition.toCategory || "",
          days: Number(this.days) || 0,
          hours: Number(this.hours) || 0,
          minutes: Number(this.minutes) || 0,
          assignee: (this.user && this.user.id) || "",
          resolution: this.resolution,
          commentHtml: html || "",
          clientMutationId: this.transitionMutationId,
        };
        this.pendingTransitionPayload = payload;
        savePendingMutation(this.pendingScope(), this.transitionMutationId, payload, {
          ticket: this.ticket, transitionId: String(this.transition.id || ""),
        });
      }
      try {
        r = await api.doTransition(this.ticket, payload);
        if (r && r.ok === false) throw new Error(r.error || "전이에 실패했습니다.");
      } catch (e) {
        this.busy = false;
        if (!(e && (e.uncertain || e.needLogin))) {
          clearPendingMutation(this.pendingScope());
          this.transitionMutationId = "";
          this.pendingTransitionPayload = null;
        }
        // Jira 가 거절한 이유를 그대로 보인다 — 삼키면 무엇을 고쳐야 할지 알 수 없다.
        this.err = (e && e.uncertain ? "반영 여부 확인 필요: "
          : (e && e.needLogin ? "로그인 후 같은 내용으로 다시 시도하세요: " : ""))
          + ((e && e.message) || "전이에 실패했습니다.");
        throw e;
      }
      clearPendingMutation(this.pendingScope());
      this.transitionMutationId = "";
      this.pendingTransitionPayload = null;
      this.$emit("done");
      // 후처리: 이 전이가 부모 상태 규칙을 촉발하면(하위 완료/진행중/재열림) 상위도 바꿀지 물어본다.
      if (r && r.cascade) window.dispatchEvent(new CustomEvent("cascade-prompt", { detail: r.cascade }));
    },
  },
  template: `
  <div class="trx-ov" @click.self="fromBackdrop($event) && closeGuarded()">
    <div class="trx">
      <div class="trx-h">
        <b>{{ transition.name || ('→ ' + transition.to) }}</b>
        <span class="trx-key">{{ ticket }}</span>
        <button class="trx-x" @click="closeGuarded" title="닫기">×</button>
      </div>

      <div v-if="unsupported.length" class="trx-block">
        이 전이는 앱에서 처리할 수 없는 필수 항목이 있습니다 — <b>{{ unsupported.join(', ') }}</b>.
        Jira 에서 진행해 주세요.
      </div>

      <div v-else class="trx-b" :inert="busy || !!transitionMutationId">
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

        <div v-if="needsComment" class="trx-f">
          <span class="trx-l">코멘트 <i v-if="transition.toCategory === 'done' || (has.comment && has.comment.required)">필수</i></span>
          <!-- 댓글과 **같은 에디터** — 표·코드·이미지 붙여넣기·멘션이 그대로 된다.
               버튼 줄은 감추고(제출은 아래 한 곳) ref 로 submit() 을 부른다. -->
          <CommentEditor ref="ed" :ticket-key="ticket" hide-footer kind="transition"
                         :draft-scope="pendingScope()"
                         :initial="pendingCommentHtml" :submit-fn="sendTransition"
                         @cancel="closeGuarded" />
        </div>
      </div>

      <div class="trx-f2">
        <span v-if="err" class="trx-err">{{ err }}</span>
        <span v-else-if="problems.length" class="trx-need">입력 필요: {{ problems.join(' · ') }}</span>
        <button class="trx-cancel" @click="closeGuarded">취소</button>
        <button class="trx-ok" :disabled="busy || problems.length || unsupported.length" @click="submit">
          {{ busy ? '처리 중…' : (transition.to || '전이') + ' 로 이동' }}
        </button>
      </div>
    </div>
  </div>`,
};
