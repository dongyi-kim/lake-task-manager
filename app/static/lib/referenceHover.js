// referenceHover.js — 앱 전체 티켓/사람 참조의 단일 상세 호버.
// 화면별 tooltip은 overflow 컨테이너에 잘리고 정보 구성이 달라지므로 body에 하나만 둔다.
import { api } from "./api.js";

let installed = false;
let tip = null;
let active = null;

function element(tag, className, text) {
  const el = document.createElement(tag);
  if (className) el.className = className;
  if (text != null) el.textContent = String(text);
  return el;
}

function ensureTip() {
  if (tip) return tip;
  tip = element("div", "reference-hover");
  tip.setAttribute("role", "tooltip");
  tip.hidden = true;
  document.body.appendChild(tip);
  return tip;
}

function rows(data) {
  const box = ensureTip();
  box.replaceChildren();
  data.forEach(([label, value]) => {
    const row = element("div", "reference-hover-row");
    row.appendChild(element("span", "reference-hover-label", label));
    row.appendChild(element("span", "reference-hover-value", value || "—"));
    box.appendChild(row);
  });
}

function position(target) {
  const box = ensureTip(), r = target.getBoundingClientRect();
  box.hidden = false;
  const w = box.offsetWidth, h = box.offsetHeight, gap = 8;
  let left = Math.min(Math.max(gap, r.left), window.innerWidth - w - gap);
  let top = r.bottom + gap;
  if (top + h > window.innerHeight - gap) top = Math.max(gap, r.top - h - gap);
  box.style.left = left + "px";
  box.style.top = top + "px";
}

function ticketTarget(node) {
  // `.tkt[data-key]`가 앱의 표준이지만, v-html 본문/댓글은 Jira 링크가 뱃지로 보강되기 전에도
  // 마우스가 올라올 수 있고 일부 목록은 의미 있는 행 class만 가진다. 표준 누락 하나 때문에
  // 호버가 사라지지 않도록 티켓임을 기계적으로 확인할 수 있는 형태를 모두 받는다.
  return node && node.closest ? node.closest(
    ".tkt[data-key], .jira-badge[data-key], .kidrow[data-key], .spn-sibrow[data-key], " +
    "a[href*='/browse/']") : null;
}

function ticketKey(target) {
  const direct = target.getAttribute("data-key");
  if (direct) return direct.toUpperCase();
  const m = /\/browse\/([A-Za-z][A-Za-z0-9]*-\d+)/.exec(target.getAttribute("href") || "");
  return m ? m[1].toUpperCase() : "";
}

function dateOnly(value) {
  return value ? String(value).slice(0, 10) : "—";
}

function dateTime(value) {
  if (!value) return "—";
  const d = new Date(value);
  if (Number.isNaN(d.getTime())) return String(value);
  return d.toLocaleString("ko-KR", { year: "numeric", month: "2-digit", day: "2-digit",
                                     hour: "2-digit", minute: "2-digit" });
}

function personTarget(node) {
  return node && node.closest ? node.closest(
    "[data-type='mention'][data-id], .mention[data-id], .md-person[data-uid], " +
    "a.user-hover[href*='ViewProfile.jspa']") : null;
}

function personId(target) {
  const direct = target.getAttribute("data-id") || target.getAttribute("data-uid");
  if (direct) return direct;
  try { return new URL(target.getAttribute("href") || "", location.origin).searchParams.get("name") || ""; }
  catch (e) { return ""; }
}

function show(target) {
  if (!target || target === active) return;
  active = target;
  target.removeAttribute("title"); // 브라우저 기본 tooltip과 이중 노출 방지
  rows([["불러오는 중", "…"]]);
  position(target);

  const key = ticketKey(target);
  if (key) {
    // api.ticketBadge는 api.js의 URL 기반 LRU memo를 사용한다. 여기서 별도 영구 Map을 두면
    // 쓰기 후 api.evict가 되어도 옛 Promise가 남고, 열람 티켓 수만큼 메모리가 증가한다.
    api.ticketBadge(key).then((b) => {
      if (active !== target || !b) return;
      rows([["티켓 번호", b.key || key], ["티켓 타입", b.type], ["제목", b.summary],
            ["담당자", b.assignee || "미지정"], ["진행상황", b.status],
            ["상위 Epic", b.epicKey ? b.epicKey + (b.epicSummary ? " · " + b.epicSummary : "") : "없음"],
            ["기한", dateOnly(b.due)], ["최근 업데이트", dateTime(b.updated)]]);
      position(target);
    }).catch(() => { if (active === target) rows([["티켓 번호", key], ["상세", "조회 실패"]]); });
    return;
  }

  const uid = personId(target);
  if (!uid) return;
  api.userBadge(uid).then((u) => {
    if (active !== target || !u) return;
    rows([["Full Display Name", u.displayName || u.name || uid], ["username", u.username || uid]]);
    position(target);
  }).catch(() => { if (active === target) rows([["사용자", uid], ["상세", "조회 실패"]]); });
}

function hideFrom(event) {
  if (!active) return;
  const next = event.relatedTarget;
  if (next && (active.contains(next) || (tip && tip.contains(next)))) return;
  active = null;
  if (tip) tip.hidden = true;
}

export function installReferenceHover() {
  if (installed) return;
  installed = true;
  ensureTip();
  document.addEventListener("mouseover", (e) => show(ticketTarget(e.target) || personTarget(e.target)));
  document.addEventListener("mouseout", hideFrom);
  document.addEventListener("focusin", (e) => show(ticketTarget(e.target) || personTarget(e.target)));
  document.addEventListener("focusout", hideFrom);
}
