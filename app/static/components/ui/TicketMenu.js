// TicketMenu.js — 티켓 카드 **우클릭** 메뉴.
//
// 드래그로 상태를 옮기지 않는 이유: Jira 는 "상태를 지정" 하는 게 아니라 워크플로가 허용한
// **전이**를 실행하는 것이라, 어느 칸에 놓을 수 있는지가 티켓마다 다르다. 드래그는 "아무 데나
// 놓을 수 있다" 고 말해 놓고 놓는 순간 거절하게 된다. 메뉴는 **가능한 것만 보여 준다** —
// 같은 정보를 정직하게 전달하면서 구현도 단순하다.
//
// 메뉴는 문서 전체에 위임한다(.tkt[data-key] 는 어느 화면에나 있다). 앞으로 다른 항목
// (열기·복사 등)이 늘어날 자리이기도 하다.
import { api } from "../../lib/api.js";
import TransitionDialog from "./TransitionDialog.js";

export default {
  name: "TicketMenu",
  components: { TransitionDialog },
  data() {
    return { open: false, x: 0, y: 0, key: "", items: null, err: "", picked: null };
  },
  mounted() {
    document.addEventListener("contextmenu", this._onCtx = (e) => {
      const a = e.target.closest && e.target.closest(".tkt[data-key]");
      if (!a) return;                       // 티켓 위가 아니면 브라우저 기본 메뉴를 그대로 둔다
      e.preventDefault();
      this.openAt(e.clientX, e.clientY, a.getAttribute("data-key"));
    });
    document.addEventListener("click", this._onDoc = () => { if (!this.picked) this.open = false; });
    document.addEventListener("keydown", this._onEsc = (e) => {
      if (e.key === "Escape") { this.open = false; this.picked = null; }
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
      this.x = Math.min(x, window.innerWidth - 240);
      this.y = Math.min(y, window.innerHeight - 220);
      this.key = key; this.open = true; this.items = null; this.err = "";
      api.transitions(key)
        .then((r) => { this.items = Array.isArray(r) ? r : []; })
        .catch((e) => { this.err = (e && e.message) || "불러오지 못했습니다."; this.items = []; });
    },
    pick(t) { this.picked = t; this.open = false; },
    onDone() {
      this.picked = null;
      // 목록·카드가 바뀐 상태를 반영해야 한다. 전이는 상태·담당·해결이 한꺼번에 바뀌므로
      // 부분 갱신보다 화면을 다시 그리는 편이 확실하다.
      window.dispatchEvent(new CustomEvent("ticket-changed", { detail: { key: this.key } }));
    },
  },
  template: `
  <div>
    <div v-if="open" class="tkmenu" :style="{ left: x + 'px', top: y + 'px' }" @click.stop>
      <div class="tkm-h">{{ key }}</div>
      <div v-if="items === null" class="tkm-loading">전이 목록 불러오는 중…</div>
      <div v-else-if="err" class="tkm-err">{{ err }}</div>
      <div v-else-if="!items.length" class="tkm-err">가능한 전이가 없습니다.</div>
      <button v-for="t in items" :key="t.id" class="tkm-i" :class="'to-' + t.toCategory"
              @click="pick(t)" :title="t.name">
        <span class="tkm-dot"></span>{{ t.to }}
        <em v-if="t.hasScreen" title="추가 입력이 필요합니다">…</em>
      </button>
    </div>
    <TransitionDialog v-if="picked" :ticket="key" :transition="picked"
                      @close="picked = null" @done="onDone" />
  </div>`,
};
