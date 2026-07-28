// AddTicketFab.js — 좌하단 '+' 티켓 추가 버튼(공통). 새로고침 버튼 위에 뜬다.
//
// 누르면 3분류: Epic 추가 / Task 추가 / Sub Task 추가.
//  · Epic → EpicCreateDialog.
//  · Task → NewChildDialog(pickKind='epic') — **그 창 안에서** 상위 Epic 을 고른다('Epic 없음' 포함).
//  · Sub  → NewChildDialog(pickKind='task') — 그 창 안에서 상위 Task 를 고른다.
// (상위 선택 오버레이를 따로 띄우지 않는다 — 상위 선택과 내용 입력이 한 창이다.)
// 티켓 다이얼로그 안의 '＋' 는 상위가 이미 정해져 있어 NewChildDialog 를 상위 고정으로 연다.
import { api } from "../../lib/api.js";
import NewChildDialog from "./NewChildDialog.js";
import EpicCreateDialog from "./EpicCreateDialog.js";
import { pushToast } from "../../lib/toast.js";

export default {
  name: "AddTicketFab",
  components: { NewChildDialog, EpicCreateDialog },
  data() {
    return {
      menuOpen: false,
      showEpic: false,       // EpicCreateDialog
      child: null,           // NewChildDialog: { pickKind: 'epic' | 'task' }
    };
  },
  methods: {
    startEpic() { this.menuOpen = false; this.showEpic = true; },
    startTask() { this.menuOpen = false; this.child = { pickKind: "epic" }; },   // 상위=Epic (창 안에서 고름)
    startSub() { this.menuOpen = false; this.child = { pickKind: "task" }; },    // 상위=Task (창 안에서 고름)
    onCreated(key) {
      this.showEpic = false; this.child = null;
      if (key) {
        pushToast({ kind: "success", icon: "✓", title: "티켓 생성됨", message: key + " — 눌러서 열기", timeout: 6000 });
        window.dispatchEvent(new CustomEvent("ticket-changed", { detail: { key } }));
        window.dispatchEvent(new CustomEvent("lake-open-ticket", { detail: { key } }));
      }
    },
  },
  template: `
  <div class="addfab-wrap">
    <!-- + 버튼 -->
    <button class="addfab" :class="{ on: menuOpen }" @click="menuOpen = !menuOpen"
            :title="menuOpen ? '닫기' : '티켓 추가'" aria-label="티켓 추가">
      <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.4" stroke-linecap="round"><path d="M12 5v14M5 12h14"/></svg>
    </button>
    <!-- 분류 메뉴 -->
    <div v-if="menuOpen" class="addfab-back" @click="menuOpen = false"></div>
    <div v-if="menuOpen" class="addfab-menu">
      <button class="addfab-i" @click="startEpic">
        <span class="afi-ic ty-epic" aria-hidden="true"><svg viewBox="0 0 16 16">
          <path fill="#fff" d="M9.2 1.5 4 8.9h2.7l-1 5.6 5.1-7.4H9.9l1.1-5.6z"/></svg></span>Epic 추가하기</button>
      <button class="addfab-i" @click="startTask">
        <span class="afi-ic ty-task" aria-hidden="true"><svg viewBox="0 0 16 16" fill="none"
          stroke="#fff" stroke-width="1.9" stroke-linecap="round" stroke-linejoin="round">
          <path d="M4 8.3l2.6 2.6L12 4.8"/></svg></span>Task 추가하기<em>Epic 밑에</em></button>
      <button class="addfab-i" @click="startSub">
        <span class="afi-ic ty-sub" aria-hidden="true"><svg viewBox="0 0 16 16">
          <path fill="none" stroke="#fff" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"
                d="M6.2 7.6l2 2 3.6-3.9"/>
          <path fill="none" stroke="#fff" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round"
                d="M3.6 3.2v3.1h3"/></svg></span>Sub Task 추가하기<em>Task 밑에</em></button>
    </div>

    <!-- 생성 다이얼로그(상위 선택 + 내용 입력을 한 창에서) -->
    <EpicCreateDialog v-if="showEpic" @close="showEpic = false" @created="onCreated" />
    <NewChildDialog v-if="child" :pick-kind="child.pickKind" @close="child = null" @created="onCreated" />
  </div>`,
};
