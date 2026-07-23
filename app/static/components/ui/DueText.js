// DueText.js — 카드의 '언제까지 / 언제 끝냈나' 한 칸.
//
// 뱃지가 아니라 **고정폭 텍스트**다. 카드가 세로로 늘어서는 화면이라 이 칸의 폭이 카드마다
// 다르면 그 뒤(담당자·Epic)의 시작 위치가 흔들려 눈이 세로로 훑질 못한다. 가장 긴 값이
// 'D-DAY'(5자)이므로 그 폭에 맞추고 안에서 가운데 정렬한다.
//
// 카드 종류(2줄/그룹 부모/Sub-Task)마다 따로 그리면 형태가 갈라진다 — 실제로 한쪽만 뱃지로
// 남아 있었다. 그래서 이 한 곳에서만 그린다.
import { ymd } from "../../lib/fmt.js";

export default {
  name: "DueText",
  props: { card: { type: Object, required: true } },
  computed: {
    done() { return this.card.statusCategory === "done"; },
    d() { const v = this.card.dueDays; return v === null || v === undefined ? null : v; },
    label() {
      if (this.d === null) return "미정";
      return this.d < 0 ? "D+" + -this.d : this.d === 0 ? "D-DAY" : "D-" + this.d;
    },
    cls() {
      if (this.done) return "fin";
      if (this.d === null) return "none";
      return this.d < 0 ? "over" : this.d === 0 ? "today" : this.d <= 7 ? "soon" : "later";
    },
    doneAt() { return this.card.resolved ? ymd(this.card.resolved) : ""; },
    tip() {
      if (this.done) return "완료 " + (this.doneAt || "");
      if (this.card.dueInherited) return "상위 Task 의 마감(" + (this.card.due || "") + ")";
      return this.card.due || "마감 없음";
    },
  },
  template: `
  <span class="tc-when" :class="[cls, { inh: !done && card.dueInherited }]" :title="tip">
    <b v-if="done">✓ {{ doneAt || '완료' }}</b>
    <b v-else><i v-if="card.dueInherited" class="inh-m">↑</i>{{ label }}</b>
  </span>`,
};
