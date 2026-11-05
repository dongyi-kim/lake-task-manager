// Shared Task-with-SubTask folding footer for compact and three-axis layouts.
import Avatar from "./Avatar.js";
import { childSyncBusy } from "../mytasks/taskHydration.js";

export default {
  name: "SubtaskFoldBar",
  components: { Avatar },
  props: {
    panel: { type: Object, required: true },
    closed: { type: Boolean, default: false },
  },
  emits: ["toggle"],
  computed: {
    done() { return Math.max(0, Number(this.panel?.group?.kidsDone) || 0); },
    total() { return Math.max(0, Number(this.panel?.group?.kidsTotal) || 0); },
    pct() {
      const value = Number(this.panel?.group?.pct);
      if (Number.isFinite(value)) return Math.max(0, Math.min(100, value));
      return this.total ? Math.round(this.done * 100 / this.total) : 0;
    },
    assignees() { return this.panel?.assignees || []; },
    pending() { return childSyncBusy(this.panel?.syncState); },
    note() {
      if (this.panel.emptyFiltered) return "현재 필터에 해당하는 SubTask 없음";
      if (this.panel.syncState === "waiting-auth") return "인증 대기";
      if (["failed", "paused"].includes(this.panel.syncState)) return "일부 SubTask 미수신";
      return "";
    },
  },
  template: `
    <button type="button" class="mt-subfoot" :class="{ open: !closed, pending }"
            :aria-disabled="panel.emptyFiltered || undefined"
            :aria-expanded="panel.emptyFiltered ? false : !closed" :title="note || (closed ? 'SubTask 펼치기' : 'SubTask 접기')"
            @click.stop="!panel.emptyFiltered && $emit('toggle')">
      <span class="mt-subfoot-toggle" :class="{ open: !closed }" aria-hidden="true">▸</span>
      <span class="mt-subfoot-label"><strong>{{ total }}</strong> Subtasks</span>
      <span v-if="assignees.length" class="mt-subfoot-sep" aria-hidden="true"></span>
      <span v-if="assignees.length" class="mt-subfoot-owners">
        <span v-for="owner in assignees" :key="owner.id || owner.name" class="mt-subfoot-owner"
              :title="owner.name">
          <Avatar :user="owner.id" :name="owner.name" :size="16" />
          <span>{{ owner.name }}</span>
        </span>
      </span>
      <span class="mt-subfoot-sep mt-subfoot-progress-sep" aria-hidden="true"></span>
      <span v-if="pending" class="mt-subfoot-sync"><i aria-hidden="true"></i>동기화 중</span>
      <span v-else-if="note" class="mt-subfoot-sync">{{ note }}</span>
      <span v-else class="mt-subfoot-progress">
        <span class="mt-pbar" role="progressbar" :aria-valuenow="done" aria-valuemin="0"
              :aria-valuemax="total" :aria-label="done + ' / ' + total + ' SubTask 완료'"
              :title="done + ' / ' + total + ' 완료'">
          <i :style="{ width: pct + '%' }"></i>
        </span>
        <em>{{ done }} / {{ total }}</em>
      </span>
    </button>`,
};
