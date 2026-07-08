// colors.js — 색/라벨 매핑 단일 소스 (모듈 컬러, 상태, 이슈타입). 실제 색값은 tokens.css.
// updated: 2026-07-08
export function moduleColor(i) { return "var(--c" + ((i % 7) + 1) + ")"; }

// statusCategory(todo|inprogress|done) → CSS 변수 / 라벨
export const STATUS_VAR = { todo: "var(--st-todo)", inprogress: "var(--st-prog)", done: "var(--st-done)" };
export const STATUS_LABEL = { todo: "To Do", inprogress: "In Progress", done: "Done" };
// 트리 정렬 순서: 진행중 → To Do → 완료
export const STATUS_ORDER = { inprogress: 0, todo: 1, done: 2 };

// 이슈타입 → 짧은 라벨 (배지 표기)
export const TYPE_LABEL = {
  Epic: "Epic", Story: "Story", Task: "Task", Bug: "Bug",
  "Sub-Task": "Sub", Improvement: "Impr", "New Feature": "Feat",
};
// 이슈타입 → solid 배지 배경색 (WBS Gantt 스타일)
export const TYPE_BG = {
  Epic: "#7a5af5", Story: "#2f8fe0", Task: "#3568c4", Bug: "#e05353",
  "Sub-Task": "#8a8a86", Improvement: "#1aa06a", "New Feature": "#1aa06a",
};
export function typeLabel(t) { return TYPE_LABEL[t] || t; }
