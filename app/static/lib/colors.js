// colors.js — 색/라벨 매핑 단일 소스 (모듈 컬러, 상태, 이슈타입). 실제 색값은 tokens.css.
// updated: 2026-07-08
export function moduleColor(i) { return "var(--c" + ((i % 7) + 1) + ")"; }

// ── 범주형 팔레트 — Epic·모듈처럼 **'서로 다름' 만 뜻하는 값**에 쓴다(순서·크기 의미 없음).
// 해시로 hue 를 만드는 방식(hsl(h 62% 52%))은 인접 해시가 비슷한 색이 돼 구분이 안 된다.
// hue 를 충분히 벌린 고정 12색을 두고 키로 그중 하나를 고른다. 실제 색값은 tokens.css(라이트/다크).
const CATEGORY_N = 12;
export function categoryColor(key) {
  const s = String(key == null ? "" : key);
  if (!s) return null;
  let h = 0;
  for (let i = 0; i < s.length; i++) h = (h * 131 + s.charCodeAt(i)) >>> 0;
  return "var(--cat" + ((h % CATEGORY_N) + 1) + ")";
}

// statusCategory(todo|inprogress|done) → CSS 변수 / 라벨
export const STATUS_VAR = { todo: "var(--st-todo)", inprogress: "var(--st-prog)", done: "var(--st-done)" };
// 하위 티켓 정렬 순서: Open → 진행중 → 해결(완료)
// (현안 하위 목록·자손 트리 공통. 완료가 맨 아래로 모여 세로 진척 바와 구간이 일치한다)
export const STATUS_ORDER = { todo: 0, inprogress: 1, done: 2 };

// 이슈타입 → 짧은 라벨 (배지 표기)
// 이슈타입 한글 라벨 — 뱃지 표기 단일 소스(여기만 고치면 전 화면 반영). Epic 은 고유명사라 유지.
export const TYPE_LABEL = {
  Epic: "Epic", Story: "스토리", Task: "업무", Bug: "버그",
  "Sub-Task": "서브", Improvement: "개선", "New Feature": "기능",
};
// 상태명 한글 라벨 — 사내 Jira 상태(Open/In Progress/…) → 짧은 한글. 모르는 상태는 원문 유지.
export const STATUS_LABEL = {
  Open: "대기", "To Do": "대기", Reopened: "재오픈",
  "In Progress": "진행 중", Resolved: "해결", Closed: "종료", Done: "완료",
};
export function statusLabel(s) { return STATUS_LABEL[s] || s || ""; }
// 이슈타입 → solid 배지 배경색. 실제 색값은 tokens.css(--ty-*) — 테마(다크/라이트) 따라감.
// 색 문법: Epic 보라 / Story 앰버 / Task 파랑 / Sub-Task 청록 / Bug 레드 / Feature·Improvement 그린
export const TYPE_BG = {
  Epic: "var(--ty-epic)", Story: "var(--ty-story)", Task: "var(--ty-task)", Bug: "var(--ty-bug)",
  "Sub-Task": "var(--ty-sub)", Improvement: "var(--ty-feat)", "New Feature": "var(--ty-feat)",
};
export function typeLabel(t) { return TYPE_LABEL[t] || t; }

// 이슈 타입의 작은 Jira식 아이콘. 에이전트 답변·티켓 입력기처럼 공간이 좁은 곳에서
// 타입명을 글자로 반복하지 않고도 Epic/Task/Sub-Task 등을 구별하게 한다.
// 반환값은 고정 SVG 조각만 사용하므로 서버 응답 문자열이 HTML로 들어가지 않는다.
const TYPE_ICON_PATH = {
  Epic: '<path fill="currentColor" d="M9.2 1.5 4 8.9h2.7l-1 5.6 5.1-7.4H9.9l1.1-5.6z"/>',
  Task: '<path d="M4 8.3l2.6 2.6L12 4.8"/>',
  "Sub-Task": '<path d="M6.2 7.6l2 2 3.6-3.9M3.6 3.2v3.1h3"/>',
  Bug: '<path d="M5.2 5.1h5.6v5.8a2.8 2.8 0 0 1-5.6 0zM6.3 5.1a1.7 1.7 0 0 1 3.4 0M3 7h2.2m5.6 0H13M3 10h2.2m5.6 0H13M4 13l1.6-1.1m6.4 1.1-1.6-1.1"/>',
  Story: '<path d="M4 2.8h8v10.4l-4-2.1-4 2.1z"/>',
  Improvement: '<path d="M3.5 11.8 7 8.3l2.1 2.1 3.4-4.1M9.7 6.3h2.8v2.8"/>',
  "New Feature": '<path d="M8 2.5v11M2.5 8h11M4.1 4.1l7.8 7.8m0-7.8-7.8 7.8"/>',
};
export function typeIconSvg(type) {
  const path = TYPE_ICON_PATH[type] || TYPE_ICON_PATH.Task;
  const filled = type === "Epic" || type === "Story";
  return `<svg viewBox="0 0 16 16" aria-hidden="true"${filled ? "" :
    ' fill="none" stroke="currentColor" stroke-width="1.7" stroke-linecap="round" stroke-linejoin="round"'}>${path}</svg>`;
}

// 사용자 시그니처 컬러 — id 해시 → 고정 hue. 같은 사람은 어디서나 같은 색.
// 쓰는 곳: 댓글 좌측 구분 바(글쓴이 식별) · 프로필 사진 없는 사람의 기본 아바타 배경 · 멘션 팝업.
export function sigColor(id) {
  const s = String(id || "");
  if (!s) return "var(--border)";
  let h = 0;
  for (let i = 0; i < s.length; i++) h = (Math.imul(h, 31) + s.charCodeAt(i)) >>> 0;
  // ★ 끝자리만 다른 id(예: test.ui01 vs test.ui02)는 해시가 1 차이라 hue 도 1° 차이 → 같은 색으로 보였다.
  //   avalanche 믹싱으로 작은 입력차가 hue 전체에 퍼지게 한다(인접 id 가 확실히 다른 색).
  h ^= h >>> 16; h = Math.imul(h, 0x7feb352d) >>> 0; h ^= h >>> 15; h = Math.imul(h, 0x846ca68b) >>> 0; h ^= h >>> 16;
  return "hsl(" + (h % 360) + " 62% 52%)";
}
// 기본 아바타에 넣을 이니셜 — 본명 우선(한글은 그대로, 영문은 대문자).
export function initialOf(name, id) {
  const s = String(name || id || "").trim();
  if (!s) return "?";
  // 본명(첫 어절)의 **마지막 두 글자** — 예: "손다슬 (주)대원씨엔씨" → "다슬".
  // 한 글자면 그 글자, 영문 id 폴백은 앞 두 글자를 대문자로.
  const first = s.split(/\s+/)[0] || s;
  if (/[가-힣]/.test(first)) return first.length >= 2 ? first.slice(-2) : first;
  return first.slice(0, 2).toUpperCase();
}
