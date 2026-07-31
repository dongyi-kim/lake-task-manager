// StatusPill.js — 상태(statusCategory) 색 텍스트 pill. 색은 tokens.css/colors.js 단일 소스.
// 표기는 statusLabel(한글) — 상태명이 없으면 카테고리의 한글 폴백(대기/진행 중/완료).
// updated: 2026-07-31
import { STATUS_VAR, statusLabel } from "../../lib/colors.js";

const CAT_FALLBACK = { todo: "대기", inprogress: "진행 중", done: "완료" };

export default {
  name: "StatusPill",
  props: { cat: { type: String, default: "todo" }, label: String },
  computed: {
    color() { return STATUS_VAR[this.cat] || "var(--st-todo)"; },
    text() { return statusLabel(this.label) || CAT_FALLBACK[this.cat] || this.cat; },
  },
  template: `<span class="pill" :style="{ color: color, borderColor: color }">{{ text }}</span>`,
};
