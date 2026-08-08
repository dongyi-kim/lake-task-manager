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
import BulkCreateDialog from "./BulkCreateDialog.js";
import EpicCreateDialog from "./EpicCreateDialog.js";
import { pushToast } from "../../lib/toast.js";
import { confirmBox } from "../../lib/confirm.js";
import { copyTicketLink } from "../../lib/ticketlink.js";

// 상위 피커(Task/Sub 만들기)의 첫 로딩이 느린 이유: 검색어 없이 열면 200건 조회+정렬+Epic 이름
// 해석을 처음 한 번 돈다(~수 초). 그 조립결과는 서버가 30분 캐시하므로, **화면 진입 시 미리 한 번**
// 백그라운드로 돌려 캐시를 데워 두면 실제로 열 때 즉시 뜬다. 세션당 한 번만(모듈 플래그).
let _prewarmed = false;
function _prewarmPickers() {
  if (_prewarmed) return;
  _prewarmed = true;
  // 초기 페이지 로딩과 경쟁하지 않게 잠깐 뒤, 결과는 버린다(캐시만 데운다).
  setTimeout(() => {
    api.parentTaskCandidates("").catch(() => {});     // Sub-Task 상위(Task) 후보
    api.options("epics").catch(() => {});        // Task 상위(Epic) 후보
    api.taskTypes().catch(() => {});             // 'Epic 없음' 생성용 타입
  }, 1200);
}

export default {
  name: "AddTicketFab",
  components: { NewChildDialog, EpicCreateDialog, BulkCreateDialog },
  mounted() { _prewarmPickers(); },   // 화면 진입 시 상위 피커 캐시 미리 데우기
  data() {
    return {
      menuOpen: false,
      showEpic: false,       // EpicCreateDialog
      child: null,           // NewChildDialog: { pickKind: 'epic' | 'task' }
      bulkPick: false,       // Bulk: 모드(Task/Sub) 고르는 중
      bulk: null,            // BulkCreateDialog: 'task' | 'subtask'
    };
  },
  methods: {
    startEpic() { this.menuOpen = false; this.showEpic = true; },
    startTask() { this.menuOpen = false; this.child = { pickKind: "epic" }; },   // 상위=Epic (창 안에서 고름)
    startSub() { this.menuOpen = false; this.child = { pickKind: "task" }; },    // 상위=Task (창 안에서 고름)
    // Bulk — 한 번에 Task 만 / Sub-Task 만 만들 수 있다(섞기 불가). 그래서 먼저 종류를 고른다.
    startBulk() { this.menuOpen = false; this.bulkPick = true; },
    pickBulk(mode) { this.bulkPick = false; this.bulk = mode; },
    onBulkDone(r) {
      const n = ((r && r.created) || []).length;
      if (n) window.dispatchEvent(new CustomEvent("force-refresh"));   // 목록 화면들 새로 받기
    },
    async onCreated(key) {
      this.showEpic = false; this.child = null;
      if (!key) return;
      window.dispatchEvent(new CustomEvent("ticket-changed", { detail: { key } }));
      // 편의: 방금 만든 티켓의 Jira 링크를 클립보드로 복사할지 묻는다.
      const yes = await confirmBox(key + " 를 만들었습니다. Jira 링크를 클립보드에 복사할까요?",
                                   { okLabel: "복사", cancelLabel: "안 함" });
      if (yes) {
        const { ok, url } = await copyTicketLink(key);
        pushToast(ok
          ? { kind: "success", icon: "📋", title: "링크 복사됨", message: url, timeout: 5000 }
          : { kind: "error", icon: "⚠", title: "복사 실패", message: url, timeout: 7000 });
      }
      window.dispatchEvent(new CustomEvent("lake-open-ticket", { detail: { key } }));
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
      <div class="addfab-sep"></div>
      <button class="addfab-i" @click="startBulk">
        <span class="afi-ic ty-bulk" aria-hidden="true"><svg viewBox="0 0 16 16" fill="none"
          stroke="#fff" stroke-width="1.6" stroke-linecap="round">
          <path d="M2.6 4.2h10.8M2.6 8h10.8M2.6 11.8h7"/></svg></span>Bulk Task 추가하기<em>JSON 으로</em></button>
    </div>

    <!-- Bulk: 무엇을 만들지 먼저 고른다(Task 와 Sub-Task 는 섞을 수 없다) -->
    <div v-if="bulkPick" class="addfab-back" @click="bulkPick = false"></div>
    <div v-if="bulkPick" class="addfab-menu">
      <div class="addfab-g">무엇을 한 번에 만들까요?</div>
      <button class="addfab-i" @click="pickBulk('task')">
        <span class="afi-ic ty-task" aria-hidden="true"><svg viewBox="0 0 16 16" fill="none"
          stroke="#fff" stroke-width="1.9" stroke-linecap="round" stroke-linejoin="round">
          <path d="M4 8.3l2.6 2.6L12 4.8"/></svg></span>Task 추가하기<em>Epic 밑에</em></button>
      <button class="addfab-i" @click="pickBulk('subtask')">
        <span class="afi-ic ty-sub" aria-hidden="true"><svg viewBox="0 0 16 16">
          <path fill="none" stroke="#fff" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"
                d="M6.2 7.6l2 2 3.6-3.9"/>
          <path fill="none" stroke="#fff" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round"
                d="M3.6 3.2v3.1h3"/></svg></span>Sub Task 추가하기<em>Task 밑에</em></button>
    </div>

    <!-- 생성 다이얼로그(상위 선택 + 내용 입력을 한 창에서) -->
    <EpicCreateDialog v-if="showEpic" @close="showEpic = false" @created="onCreated" />
    <NewChildDialog v-if="child" :pick-kind="child.pickKind" @close="child = null" @created="onCreated" />
    <BulkCreateDialog v-if="bulk" :mode="bulk" @close="bulk = null" @done="onBulkDone" />
  </div>`,
};
