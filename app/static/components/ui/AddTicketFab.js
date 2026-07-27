// AddTicketFab.js — 좌하단 '+' 티켓 추가 버튼(공통). 새로고침 버튼 위에 뜬다.
//
// 누르면 3분류: Epic 추가 / Task 추가 / Sub Task 추가.
//  · Epic  → EpicCreateDialog(상위 없음).
//  · Task  → **상위 Epic 검색·선택** → NewChildDialog(그 Epic 밑).
//  · Sub   → **상위 Task 검색·선택** → NewChildDialog(그 Task 밑).
// (티켓 다이얼로그 안의 '추가' 는 상위가 이미 정해져 있어 이 fab 을 안 탄다 — 그쪽은 상위 고정.)
import { api } from "../../lib/api.js";
import TypeBadge from "./TypeBadge.js";
import NewChildDialog from "./NewChildDialog.js";
import EpicCreateDialog from "./EpicCreateDialog.js";
import { pushToast } from "../../lib/toast.js";

export default {
  name: "AddTicketFab",
  components: { TypeBadge, NewChildDialog, EpicCreateDialog },
  data() {
    return {
      menuOpen: false,
      showEpic: false,                 // EpicCreateDialog
      pick: null,                      // null | { forSub:bool } — 상위 검색 오버레이 상태
      pickQ: "", pickList: [], pickBusy: false,
      child: null,                     // NewChildDialog props: {parent,isEpic,types,parentDue,parentComponents}
    };
  },
  unmounted() { clearTimeout(this._t); },
  methods: {
    // ── 메뉴 분류 ──
    startEpic() { this.menuOpen = false; this.showEpic = true; },
    startTask() { this.openPicker(false); },      // 상위=Epic
    startSub() { this.openPicker(true); },         // 상위=Task
    openPicker(forSub) {
      this.menuOpen = false;
      this.pick = { forSub };
      this.pickQ = ""; this.pickList = [];
      this.$nextTick(() => { const el = this.$refs.pinput; if (el) el.focus(); });
      this.searchParents("");
    },
    searchParents(q) {
      this.pickQ = q;
      clearTimeout(this._t);
      this._t = setTimeout(() => {
        this.pickBusy = true;
        // Sub → 상위는 Task(일반 이슈), Task → 상위는 Epic.
        const p = this.pick && this.pick.forSub
          ? api.epicCandidates(q).then((r) => (r && r.items) || [])
          : api.options("epics", q).then((r) => (r || []).map((e) => ({ key: e.key, summary: e.name || e.key, type: "Epic" })));
        p.then((items) => { this.pickList = items || []; })
          .catch(() => { this.pickList = []; })
          .finally(() => { this.pickBusy = false; });
      }, 250);
    },
    // 상위 고르면 → 그 상위의 자식 타입·기한·컴포넌트를 받아 NewChildDialog 를 연다.
    async choseParent(item) {
      const parent = item.key, forSub = !!(this.pick && this.pick.forSub);
      this.pick = null;
      let types = [], due = "", comps = [];
      try {
        const [t, v] = await Promise.all([
          api.childTypes(parent).catch(() => []),
          api.ticket(parent).catch(() => null),
        ]);
        types = t || [];
        if (v) { due = v.due || ""; comps = (v.components || []).slice(); }
      } catch (e) { /* 최소값으로 연다 */ }
      // Sub-Task 는 부모가 Task 라 isEpic=false, Task 는 부모가 Epic 이라 isEpic=true.
      this.child = { parent, isEpic: !forSub, types, parentDue: due, parentComponents: comps };
    },
    onCreated(key) {
      this.showEpic = false; this.child = null;
      if (key) {
        pushToast({ kind: "success", icon: "✓", title: "티켓 생성됨", message: key + " — 눌러서 열기", timeout: 6000 });
        // 목록·계보 갱신 + 새 티켓 열기 신호
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
      <button class="addfab-i" @click="startEpic"><span class="afi-ic">📐</span>Epic 추가하기</button>
      <button class="addfab-i" @click="startTask"><span class="afi-ic">🗂</span>Task 추가하기<em>Epic 밑에</em></button>
      <button class="addfab-i" @click="startSub"><span class="afi-ic">✅</span>Sub Task 추가하기<em>Task 밑에</em></button>
    </div>

    <!-- 상위 검색 오버레이(Task/Sub) -->
    <Teleport to="body">
    <div v-if="pick" class="nk-ov" @click.self="pick = null">
    <div class="nk nk-pick" @click.stop>
      <div class="nk-h">{{ pick.forSub ? '상위 Task 고르기' : '상위 Epic 고르기' }}
        <span class="nk-h-s">{{ pick.forSub ? 'Sub Task 를 만들 Task 를 고르세요' : 'Task 를 만들 Epic 을 고르세요' }}</span>
        <button class="lp-x" @click="pick = null" aria-label="닫기">✕</button>
      </div>
      <input ref="pinput" class="nk-mini nk-tsearch" :value="pickQ" @input="searchParents($event.target.value)"
             :placeholder="(pick.forSub ? 'Task' : 'Epic') + ' 검색 (키 또는 제목)'">
      <div class="nk-cands nk-cands-tall">
        <div v-if="pickBusy" class="muted nk-cand-empty">찾는 중…</div>
        <button v-for="c in pickList" :key="c.key" type="button" class="nk-cand" @click="choseParent(c)">
          <TypeBadge :type="c.type" /><b>{{ c.key }}</b>
          <span class="nk-cand-s">{{ c.summary }}</span>
        </button>
        <div v-if="!pickBusy && !pickList.length" class="muted nk-cand-empty">결과가 없습니다.</div>
      </div>
    </div>
    </div>
    </Teleport>

    <!-- 실제 생성 다이얼로그 -->
    <EpicCreateDialog v-if="showEpic" @close="showEpic = false" @created="onCreated" />
    <NewChildDialog v-if="child" :parent="child.parent" :is-epic="child.isEpic" :types="child.types"
                    :parent-due="child.parentDue" :parent-components="child.parentComponents"
                    @close="child = null" @created="onCreated" />
  </div>`,
};
