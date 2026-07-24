// NewChildDialog.js — 하위 티켓 만들기 창.
//
// 줄 안에서 바로 만들던 것을 창으로 옮겼다. 줄에서 만들면 좁은 폭에 칸이 여섯이라 담당자·기한이
// 먼저 잘려 나가고, 팝업(우선순위·담당자)이 목록 위로 겹쳐 무엇을 고르는 중인지 흐려진다.
// 만드는 일은 잠깐 멈춰 서서 하는 일이라 창이 맞다.
//
// **입력 방식은 티켓 정보와 같다** — 값이 그대로 보이고, 누르면 같은 팝업(FieldEdit)이 열린다.
// 새로 만들 때만 콤보박스를 쓰면 만들 때와 고칠 때 조작이 달라진다.
//
// 필수(제목·우선순위·타입)는 **비워 둔 채** 연다 — 기본값을 넣어 두면 아무도 판단하지 않은 값이
// 그대로 굳는다. 대신 기한은 상위의 것을 물려주고(하위는 대개 상위와 같은 날까지다),
// 담당자는 미지정으로 시작한다.
import { api } from "../../lib/api.js";
import Avatar from "./Avatar.js";
import TypeBadge from "./TypeBadge.js";
import FieldEdit from "./FieldEdit.js";
import PriIcon, { priRankOf } from "./PriIcon.js";
import { fromBackdrop } from "../../lib/backdrop.js";

export default {
  name: "NewChildDialog",
  components: { Avatar, TypeBadge, FieldEdit, PriIcon },
  props: {
    parent: { type: String, required: true },      // 부모 티켓 키
    isEpic: { type: Boolean, default: false },     // Epic 밑이면 타입을 고른다(아니면 Sub-Task 하나뿐)
    types: { type: Array, default: () => [] },     // 만들 수 있는 타입(서버가 부모를 보고 정한 목록)
    parentDue: { type: String, default: "" },      // 상위(Task/Epic)의 작업 기한 — 기본값으로 물려준다
    parentComponents: { type: Array, default: () => [] },   // 상위의 컴포넌트 — 기본값으로 물려받는다
  },
  emits: ["close", "created"],
  data() {
    // 우선순위는 **비워 둔 채** 시작한다. 기본값을 넣어 두면 아무도 판단하지 않은 등급이
    // 그대로 굳는다 — 등급은 만드는 사람이 정해야 하는 값이라 비워 두고 물어본다.
    return { busy: false, err: "", priOpts: [], compOpts: [],
             nc: { type: "", summary: "", priority: "", components: [],
                   duedate: "", assigneeId: "", assigneeName: "" } };
  },
  computed: {
    title() { return this.isEpic ? "하위 Task 만들기" : "Sub Task 만들기"; },
    canCreate() { return !!(this.nc.type && this.nc.priority && this.nc.summary.trim()); },
    // 고를 것이 하나뿐인 선택은 소음이다 — Sub-Task 는 타입 줄 자체를 안 그린다.
    pickType() { return this.isEpic && this.types.length > 1; },
  },
  mounted() {
    // 타입: Sub-Task 는 고를 것이 없으니 그대로 박고, Epic 밑은 **비워 두고 고르게** 한다
    // (Task 를 미리 넣어 두면 Story·Bug 로 만들 것도 Task 로 만들어진다).
    this.nc.type = this.pickType ? "" : (this.types[0] || "");
    // 기한은 상위의 것을 물려준다 — 하위는 대개 상위와 같은 날까지다. 다르면 고치면 된다.
    this.nc.duedate = this.parentDue || "";
    // 컴포넌트도 상위 것을 물려받는다. 컴포넌트는 곧 **모듈**(롤업 축)이라, 비워 두면 새 티켓이
    // 어느 모듈에도 안 잡혀 WBS·워크로드에서 사라진다.
    this.nc.components = (this.parentComponents || []).slice();
    api.options("components").then((r) => { this.compOpts = (r || []).map((x) => x.name); })
      .catch(() => { this.compOpts = []; });
    api.options("priorities").then((r) => { this.priOpts = (r || []).map((x) => x.name); })
      .catch(() => { this.priOpts = []; });
    this.$nextTick(() => { const el = this.$refs.sum; if (el) el.focus(); });
    // Esc 로 닫는다. capture 로 잡아 뒤에 있는 티켓 창이 함께 닫히지 않게 한다 —
    // 만들다 말고 Esc 를 눌렀는데 보던 티켓까지 사라지면 그건 취소가 아니라 사고다.
    document.addEventListener("keydown", this._onEsc = (e) => {
      if (e.key === "Escape") { e.stopPropagation(); this.$emit("close"); }
    }, true);
  },
  unmounted() { document.removeEventListener("keydown", this._onEsc, true); },
  methods: {
    // 드래그가 창 밖에서 끝났을 뿐인데 닫히지 않게 — lib/backdrop.js 참고
    fromBackdrop,
    rankOf: priRankOf,
    setWho(id, u) {
      this.nc.assigneeId = id || "";
      this.nc.assigneeName = u ? (u.display || u.name || "") : "";
    },
    async submit() {
      if (!this.canCreate || this.busy) return;
      this.busy = true; this.err = "";
      try {
        const r = await api.createChild(this.parent, {
          type: this.nc.type, summary: this.nc.summary.trim(), priority: this.nc.priority,
          duedate: this.nc.duedate || null, assignee: this.nc.assigneeId || null,
          components: this.nc.components.slice(),
        });
        if (!r || r.ok === false) { this.err = (r && r.error) || "만들지 못했습니다."; return; }
        this.$emit("created", r.key);
      } catch (e) {
        // 거절 사유를 그대로 보인다 — 삼키면 무엇이 문제인지 알 수 없다.
        this.err = (e && e.message) || "만들지 못했습니다.";
      } finally { this.busy = false; }
    },
  },
  template: `
  <Teleport to="body">
  <div class="nk-ov" @click.self="fromBackdrop($event) && $emit('close')">
  <div class="nk" @click.stop>
    <div class="nk-h">{{ title }}
      <span class="nk-h-s">{{ parent }} 아래에 만듭니다</span>
      <button class="lp-x" @click="$emit('close')" aria-label="닫기">✕</button>
    </div>

    <!-- 제목이 먼저다 — 이 창에서 유일하게 직접 치는 값이다 -->
    <input ref="sum" v-model="nc.summary" class="nk-sum" maxlength="200"
           :placeholder="isEpic ? '무엇을 할 Task 인가요?' : '무엇을 할 작업인가요?'"
           @keydown.enter.prevent="submit">

    <!-- 아래는 티켓 정보와 **같은 모양·같은 팝업**. 값이 그대로 보이고 누르면 고른다. -->
    <div class="nk-meta">
      <div><span class="k">우선순위</span><span class="val">
        <FieldEdit :ticket="parent" field="priority" local :choices="priOpts"
                   :value="nc.priority" @pick="(v) => nc.priority = v">
          <template v-if="nc.priority">
            <PriIcon :rank="rankOf(nc.priority)" :name="nc.priority" /><span class="prio-n">{{ nc.priority }}</span>
          </template>
          <span v-else class="nk-need">고르세요</span>
        </FieldEdit></span></div>

      <div v-if="pickType"><span class="k">티켓 타입</span><span class="val">
        <FieldEdit :ticket="parent" field="issuetype" local :choices="types"
                   :value="nc.type" @pick="(v) => nc.type = v">
          <TypeBadge v-if="nc.type" :type="nc.type" />
          <span v-else class="nk-need">고르세요</span>
        </FieldEdit></span></div>
      <div v-else><span class="k">티켓 타입</span><span class="val">
        <TypeBadge :type="nc.type" /></span></div>

      <div><span class="k">작업 기한</span><span class="val">
        <FieldEdit :ticket="parent" field="duedate" local :value="nc.duedate"
                   @pick="(v) => nc.duedate = v">
          <span :class="{ muted: !nc.duedate }">{{ nc.duedate || '미지정' }}</span>
        </FieldEdit></span></div>

      <div><span class="k">컴포넌트</span><span class="val">
        <FieldEdit :ticket="parent" field="components" local :choices="compOpts"
                   :value="nc.components" @pick="(v) => nc.components = v">
          <span v-if="nc.components.length" class="tkt-labels">
            <span v-for="c in nc.components" :key="c" class="tkt-label comp">{{ c }}</span>
          </span>
          <span v-else class="muted">미지정</span>
        </FieldEdit></span></div>

      <div><span class="k">담당자</span><span class="val val-user">
        <FieldEdit :ticket="parent" field="assignee" local :value="nc.assigneeId"
                   :user-id="nc.assigneeId" @pick="setWho">
          <!-- 미지정도 자리를 지킨다 — 아바타가 없으면 담당자 칸만 줄이 어긋나고,
               '아직 아무도 없다' 는 것도 하나의 상태다. -->
          <Avatar v-if="nc.assigneeId" :user="nc.assigneeId" :name="nc.assigneeName" :size="20" />
          <span v-else class="nk-noav" aria-hidden="true"></span>
          <span :class="{ muted: !nc.assigneeId }">{{ nc.assigneeName || '미지정' }}</span>
        </FieldEdit></span></div>
    </div>

    <div v-if="err" class="tkt-cmt-err">{{ err }}</div>
    <div class="nk-f">
      <span class="nk-hint">{{ canCreate ? '상태는 워크플로의 첫 상태로 시작합니다.'
                                          : '제목 · 우선순위 · 타입을 정해야 만들 수 있습니다.' }}</span>
      <button class="cmt-ed-btn ghost" @click="$emit('close')">취소</button>
      <button class="cmt-ed-btn primary" :disabled="!canCreate || busy" @click="submit">
        {{ busy ? '만드는 중…' : '만들기' }}</button>
    </div>
  </div>
  </div>
  </Teleport>`,
};
