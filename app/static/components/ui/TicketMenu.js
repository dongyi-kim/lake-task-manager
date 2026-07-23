// TicketMenu.js — 티켓 카드 **우클릭** 메뉴.
//
// 드래그로 상태를 옮기지 않는 이유: Jira 는 "상태를 지정" 하는 게 아니라 워크플로가 허용한
// **전이**를 실행하는 것이라, 어느 칸에 놓을 수 있는지가 티켓마다 다르다. 드래그는 "아무 데나
// 놓을 수 있다" 고 말해 놓고 놓는 순간 거절하게 된다. 메뉴는 **가능한 것만 보여 준다**.
//
// 항목이 늘면서 그룹을 나눴다. 순서는 **자주 쓰는 것부터**가 아니라 **되돌리기 쉬운 것부터**다:
//   담당자(되돌리기 쉬움) → 상태·삭제(무겁거나 못 되돌림) → 편의(아무것도 안 바꿈).
// 파괴적인 항목이 손가락이 먼저 가는 자리에 있으면 안 된다.
//
// 권한: 상태 변경·삭제는 **내 티켓(담당/보고)이거나 매니저**일 때만 연다. 남의 티켓 상태를
// 아무나 바꾸면 협업이 아니라 사고다. 서버도 같은 규칙으로 막는다(숨김은 접근 제어가 아니다).
import { api } from "../../lib/api.js";
import Avatar from "./Avatar.js";
import TransitionDialog from "./TransitionDialog.js";
import UserPickDialog from "./UserPickDialog.js";

export default {
  name: "TicketMenu",
  components: { Avatar, TransitionDialog, UserPickDialog },
  data() {
    return { open: false, x: 0, y: 0, key: "", info: null, err: "",
             picked: null, pickUser: false, busy: "" };
  },
  computed: {
    mayEdit() { return !!(this.info && this.info.mayEdit); },
    assigneeId() { return (this.info && this.info.assigneeId) || ""; },
    meId() { return (this.info && this.info.me && this.info.me.id) || ""; },
    isMine() { return !!this.assigneeId && this.assigneeId === this.meId; },
    transitions() { return (this.info && this.info.transitions) || []; },
    /** 복사·공유용 = **Jira 링크**. 우리 앱 주소는 그 사람 PC에서만 열리므로 남에게 주면 안 된다.
     *  (jiraBase 가 비어 있으면 config 가 덜 채워진 것 — 그때만 앱 주소로 폴백한다) */
    jiraUrl() {
      const base = (this.info && this.info.jiraBase) || "";
      return (base || location.origin) + "/browse/" + this.key;
    },
    /** 열기용 = **우리 앱의 티켓 페이지**. '새 창에서 열기' 는 이 앱 화면을 크게 보려는 것이지
     *  Jira 로 떠나려는 게 아니다(Jira 로 가려면 복사한 링크를 쓰면 된다). */
    appUrl() { return location.origin + "/browse/" + encodeURIComponent(this.key); },
  },
  mounted() {
    document.addEventListener("contextmenu", this._onCtx = (e) => {
      const a = e.target.closest && e.target.closest(".tkt[data-key]");
      if (!a) return;                       // 티켓 위가 아니면 브라우저 기본 메뉴를 그대로 둔다
      e.preventDefault();
      this.openAt(e.clientX, e.clientY, a.getAttribute("data-key"));
    });
    document.addEventListener("click", this._onDoc = () => {
      if (!this.picked && !this.pickUser) this.open = false;
    });
    document.addEventListener("keydown", this._onEsc = (e) => {
      if (e.key === "Escape") { this.open = false; this.picked = null; this.pickUser = false; }
    });
  },
  unmounted() {
    document.removeEventListener("contextmenu", this._onCtx);
    document.removeEventListener("click", this._onDoc);
    document.removeEventListener("keydown", this._onEsc);
  },
  methods: {
    openAt(x, y, key) {
      // 화면 밖으로 나가지 않게 — 아래/오른쪽 끝에서 열면 메뉴가 잘린다.
      this.x = Math.min(x, window.innerWidth - 250);
      this.y = Math.min(y, Math.max(8, window.innerHeight - 330));
      this.key = key; this.open = true; this.info = null; this.err = ""; this.busy = "";
      // 메뉴에 필요한 것(요약·권한·전이)을 **한 번에** 받는다 — 왕복이 셋이면 prod 의 단일
      // 상류 큐에서 그만큼 늦고, 그 사이 메뉴가 반쯤 빈 채로 떠 있다.
      api.ticketMenu(key)
        .then((r) => { this.info = r || {}; })
        .catch((e) => { this.err = (e && e.message) || "불러오지 못했습니다."; this.info = {}; });
    },
    changed() {
      // 바뀐 티켓만 화면에 반영한다(뷰가 스스로 조용히 다시 받는다).
      window.dispatchEvent(new CustomEvent("ticket-changed", { detail: { key: this.key } }));
    },
    async run(what, fn) {
      this.busy = what; this.err = "";
      try { await fn(); this.open = false; this.changed(); }
      catch (e) { this.err = (e && e.message) || "실패했습니다."; }
      finally { this.busy = ""; }
    },
    assignTo(id) { return this.run("assign", () => api.setAssignee(this.key, id)); },
    onPickUser(u) { this.pickUser = false; this.assignTo(u.id); },
    removeAssignee() { return this.assignTo(""); },
    async del() {
      // 되돌릴 수 없다 — 티켓 번호와 제목을 보여 주고 확인을 받는다.
      const t = this.info && this.info.summary ? "\n\n" + this.info.summary : "";
      if (!window.confirm(this.key + " 을(를) 삭제합니다. 되돌릴 수 없습니다." + t)) return;
      return this.run("del", () => api.deleteTicket(this.key));
    },
    /** 기본 브라우저로 연다 — 앱 창은 Playwright Chromium 이라 window.open 이면 탭·즐겨찾기도
     *  없는 자동화 창이 또 뜬다. 서버가 로컬에서 도니 OS 기본 브라우저를 띄우게 한다.
     *  실패하면(서버가 못 열면) 그때 창을 연다 — 아무것도 안 열리는 것보단 낫다. */
    async openNew() {
      this.open = false;
      try {
        const r = await api.openExternal(this.appUrl);
        if (r && r.ok) return;
      } catch (e) { /* 폴백으로 간다 */ }
      window.open(this.appUrl, "_blank", "noopener");
    },
    async copyLink() {
      try { await navigator.clipboard.writeText(this.jiraUrl); this.busy = "copied"; }
      catch (e) { this.err = "복사 실패 — " + this.jiraUrl; return; }
      setTimeout(() => { this.open = false; this.busy = ""; }, 700);
    },
  },
  template: `
  <div>
    <div v-if="open" class="tkmenu" :style="{ left: x + 'px', top: y + 'px' }" @click.stop>
      <div class="tkm-h">
        <b>{{ key }}</b>
        <span v-if="info && info.status" class="tkm-st">{{ info.status }}</span>
      </div>
      <div v-if="!info" class="tkm-loading">불러오는 중…</div>
      <template v-else>
        <!-- 1. 담당자 — 되돌리기 쉬우므로 맨 위 -->
        <div class="tkm-g">담당자</div>
        <button class="tkm-i" :disabled="!mayEdit || !!busy" @click="pickUser = true">
          <span class="tkm-ic">👤</span>담당자 변경…
        </button>
        <button class="tkm-i" :disabled="!mayEdit || isMine || !!busy" @click="assignTo(meId)">
          <span class="tkm-ic">🙋</span>담당자 나로 지정
          <em v-if="isMine">이미 나</em>
        </button>
        <button v-if="assigneeId" class="tkm-i" :disabled="!mayEdit || !!busy" @click="removeAssignee">
          <span class="tkm-ic">🚫</span>담당자 해제
          <em>{{ info.assignee }}</em>
        </button>

        <!-- 2. 상태 변경 — 내 티켓이거나 매니저만 -->
        <div class="tkm-g">상태 변경</div>
        <div v-if="!mayEdit" class="tkm-note">담당자·보고자 또는 매니저만 바꿀 수 있습니다.</div>
        <template v-else>
          <button v-for="t in transitions" :key="t.id" class="tkm-i" :class="'to-' + t.toCategory"
                  :disabled="!!busy" @click="picked = t; open = false" :title="t.name">
            <span class="tkm-dot"></span>{{ t.to }}
            <em v-if="t.hasScreen" title="추가 입력이 필요합니다">…</em>
          </button>
          <div v-if="!transitions.length" class="tkm-note">가능한 전이가 없습니다.</div>
          <button class="tkm-i danger" :disabled="!!busy" @click="del">
            <span class="tkm-ic">🗑</span>티켓 삭제
          </button>
        </template>

        <!-- 3. 편의 — 아무것도 바꾸지 않는다 -->
        <div class="tkm-g">편의</div>
        <button class="tkm-i" @click="openNew"><span class="tkm-ic">↗</span>새 창에서 열기</button>
        <button class="tkm-i" @click="copyLink">
          <span class="tkm-ic">🔗</span>Jira 링크 복사
          <em v-if="busy === 'copied'">복사됨</em>
        </button>
      </template>
      <div v-if="err" class="tkm-err">{{ err }}</div>
    </div>

    <UserPickDialog v-if="pickUser" :ticket="key" :current="assigneeId"
                    @close="pickUser = false" @pick="onPickUser" />
    <TransitionDialog v-if="picked" :ticket="key" :transition="picked"
                      @close="picked = null" @done="picked = null; changed()" />
  </div>`,
};
