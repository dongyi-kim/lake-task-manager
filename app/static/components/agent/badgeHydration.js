// Async hydration for ticket, document, and person badges rendered from agent markdown.
import { api } from "../../lib/api.js";
import { TYPE_BG, typeIconSvg } from "../../lib/colors.js";
import { createMentionAvatar } from "../../lib/mentionBadge.js";

function regexEscape(value) {
  return String(value || "").replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
}

/** 뱃지가 이미 보여 주는 필드를 바로 뒤 문장이 되풀이하지 않게 한다.
 * API 응답은 badge의 data에 보존한다. 제거 범위는 같은 parent의 **인접 text node 앞부분**뿐이라
 * 뒤 문장의 별도 주장이나 집계 수치까지 건드리지 않는다. */
function dedupeTicketTail(badge, ticket) {
  const next = badge.nextSibling;
  if (!next || next.nodeType !== Node.TEXT_NODE) return;
  let value = next.nodeValue || "";
  const known = [ticket.key, ticket.summary, ticket.status, ticket.assignee,
                 ticket.assignee ? "담당 " + ticket.assignee : "",
                 ticket.assignee ? "담당자 " + ticket.assignee : ""]
    .filter(Boolean).sort((a, b) => b.length - a.length);
  for (let i = 0; i < 5; i++) {
    const before = value;
    for (const field of known) {
      value = value.replace(new RegExp("^\\s*(?:[·|,—-]|담당(?:자)?\\s*[:：])?\\s*" +
        regexEscape(field) + "(?=\\s|[·|,—-]|$)", "i"), "");
    }
    if (value === before) break;
  }
  value = value.replace(/^\s*[·|,—-]\s*/, " — ");
  if (/^\s*[·|,—-]?\s*$/.test(value)) value = "";
  next.nodeValue = value;
}

/** 참조 detail badge가 가진 title/assignee/status를 설명 줄에서 다시 보여 주지 않는다. */
function dedupeTicketReference(badge, ticket) {
  const item = badge.closest(".agent-ref-item");
  const why = item && item.querySelector(".ref-why");
  if (!why) return;
  let value = why.textContent || "";
  const known = [ticket.key, ticket.summary, ticket.status, ticket.assignee,
                 ticket.assignee ? "담당 " + ticket.assignee : "",
                 ticket.assignee ? "담당자 " + ticket.assignee : ""]
    .filter(Boolean).sort((a, b) => b.length - a.length);
  for (let i = 0; i < 5; i++) {
    const before = value;
    for (const field of known) {
      value = value.replace(new RegExp("^\\s*(?:[·|,—-]|담당(?:자)?\\s*[:：])?\\s*" +
        regexEscape(field) + "(?=\\s|[·|,—-]|$)", "i"), "");
    }
    if (value === before) break;
  }
  value = value.replace(/^\s*[·|,—-]\s*/, "").trim();
  if (value) why.textContent = value;
  else why.remove();
}

export function augmentAgentBadges(root) {
  if (!root || !root.querySelectorAll) return;
  // 이전 렌더 결과나 예외 입력이 <code><a class="jira-badge">...</a></code>를 만들었어도
  // 두 컴포넌트의 배경·테두리가 겹치지 않게 뱃지만 남긴다.
  root.querySelectorAll(".agent-md code > a.jira-badge:only-child").forEach((badge) => {
    const code = badge.parentElement;
    if (code && code.childNodes.length === 1) code.replaceWith(badge);
  });
  // 사람 칩의 프사 — 공통 mention avatar가 @를 먼저 그리고, 성공한 사진만 그 위를 덮는다.
  root.querySelectorAll(".agent-md .md-person[data-uid]:not([data-filled])").forEach((el) => {
    el.dataset.filled = "1";
    const uid = el.getAttribute("data-uid");
    const wrap = el.querySelector(".mention-av");
    const name = el.querySelector(".md-person-nm");
    if (uid && name) api.userBadge(uid).then((u) => {
      if (!u || !el.isConnected) return;
      const label = u.name || u.displayName || uid;
      name.textContent = label;
    }).catch(() => { /* username만 남겨도 식별 가능 */ });
    if (!uid) return;
    const avatar = createMentionAvatar(uid, name ? name.textContent : uid);
    if (wrap) wrap.replaceWith(avatar); else el.prepend(avatar);
  });
  root.querySelectorAll(".agent-md a.tkt[data-key]:not([data-filled])").forEach((a) => {
    a.dataset.filled = "1";
    const key = a.getAttribute("data-key");
    api.ticketBadge(key).then((b) => {
      if (!b || !a.isConnected) return;
      a.removeAttribute("title");
      const tb = a.querySelector(".jb-type-icon"), nm = a.querySelector(".jb-name"),
            owner = a.querySelector(".jb-owner"), mt = a.querySelector(".jb-meta");
      if (!tb || !nm || !owner || !mt) return;
      tb.innerHTML = typeIconSvg(b.type || "Task");
      tb.style.setProperty("--tc", TYPE_BG[b.type] || "var(--ty-task)");
      nm.textContent = b.summary || "";
      owner.textContent = b.assignee || "미지정";
      mt.textContent = b.status || "";
      const category = b.statusCategory === "done" ? "done" :
        (b.statusCategory === "inprogress" ? "inprogress" : "todo");
      const statusClass = "st-" + category;
      mt.className = "jb-meta " + statusClass;
      a.classList.add(statusClass);
      a.dataset.ticketKey = key || "";
      a.dataset.ticketTitle = b.summary || "";
      a.dataset.ticketAssignee = b.assignee || "";
      a.dataset.ticketStatus = b.status || "";
      dedupeTicketTail(a, Object.assign({ key }, b));
      dedupeTicketReference(a, Object.assign({ key }, b));
    }).catch(() => { /* 조회 실패 — 키만 보여도 클릭은 된다 */ });
  });
  root.querySelectorAll(".agent-md a.conf-link[data-conf]:not([data-filled])").forEach((a) => {
    a.dataset.filled = "1";
    const href = a.getAttribute("href") || "";
    const t = a.querySelector(".conf-title");
    if (!t) return;
    // 제목이 URL 그대로면(맨 URL 이었단 뜻) 슬러그 → 서버 제목 순으로 사람 말로 바꾼다.
    if ((t.textContent || "").trim() === href) {
      const m = href.match(/\/pages\/\d+\/([^/?#]+)\/?$/) || href.match(/\/display\/[^/]+\/([^/?#]+)\/?$/);
      if (m) t.textContent = decodeURIComponent(m[1].replace(/\+/g, " "));
      else api.linkTitle(href).then((r) => {
        if (r && r.title && a.isConnected) t.textContent = r.title;
      }).catch(() => { /* noop */ });
    }
  });
}
