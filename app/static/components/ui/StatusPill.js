// StatusPill.js — 상태(statusCategory) 색 텍스트 pill. 색은 tokens.css/colors.js 단일 소스.
// updated: 2026-07-08
import { STATUS_VAR } from "../../lib/colors.js";
export default {
  name: "StatusPill",
  props: { cat: { type: String, default: "todo" }, label: String },
  computed: { color() { return STATUS_VAR[this.cat] || "var(--st-todo)"; } },
  template: `<span class="pill" :style="{ color: color, borderColor: color }">{{ label || cat }}</span>`,
};
