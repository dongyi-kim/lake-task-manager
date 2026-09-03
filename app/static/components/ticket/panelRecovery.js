/** TicketDialog 보조 패널의 UI 비의존 상태 규칙. */
export const DIALOG_PANEL_ORDER = Object.freeze([
  "editmeta", "childTypes", "ancestors", "comments", "siblings", "attachments",
  "documents", "children", "related",
]);

export const DIALOG_PANEL_LABELS = Object.freeze({
  editmeta: "편집 권한", childTypes: "하위 타입", ancestors: "계보", comments: "코멘트",
  siblings: "형제", attachments: "첨부파일", documents: "관련문서", children: "하위 티켓",
  related: "관련 티켓",
});

export function panelStatus(states, name) {
  return (states && states[name]) || { state: "idle", error: "" };
}

export function setPanelStatus(states, name, state, error = "") {
  return Object.assign({}, states, { [name]: { state, error } });
}

export function panelsInState(states, state) {
  return Object.keys(states || {}).filter((name) => panelStatus(states, name).state === state);
}

export function requestErrorText(error, fallback = "불러오지 못했습니다.") {
  return (error && error.message) || fallback;
}
