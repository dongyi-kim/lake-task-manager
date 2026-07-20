// TypeBadge.js — 이슈타입 배지. variant: solid(타입별 컬러, WBS 스타일 · 기본) | plain(무색 테두리).
// colors.js 단일 소스. updated: 2026-07-09
import { typeLabel, TYPE_BG } from "../../lib/colors.js";
export default {
  name: "TypeBadge",
  props: { type: { type: String, default: "" }, variant: { type: String, default: "solid" } },
  computed: {
    label() { return typeLabel(this.type); },
    // solid = 알파 틴트 칩. 색은 --tc 로만 넘기고 배경/보더 틴트는 components.css 가 계산.
    style() {
      return this.variant === "solid" ? { "--tc": TYPE_BG[this.type] || "var(--ty-task)" } : {};
    },
  },
  template: `<span class="tbadge" :class="'v-' + variant" :style="style">{{ label }}</span>`,
};
