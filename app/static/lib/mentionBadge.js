// mentionBadge.js — 에디터·티켓 본문·댓글이 공유하는 사람 멘션 UI.
// 사진은 성공했을 때만 @ 원 위를 덮는다. 로딩 중이거나 404면 구조·색·폭이 바뀌지 않는다.
import { sigColor } from "./colors.js";

const MISSING_AVATARS = new Set();

function cleanLabel(value, fallback) {
  return String(value || fallback || "").trim().replace(/^@+\s*/, "") || String(fallback || "");
}

function profileId(el) {
  const direct = el.getAttribute("data-id") || el.getAttribute("data-uid");
  if (direct) return direct;
  try {
    return new URL(el.getAttribute("href") || "", location.origin).searchParams.get("name") || "";
  } catch (e) { return ""; }
}

/** @ 폴백이 항상 먼저 존재하고, 사진이 로드된 경우에만 그 위를 덮는다. */
export function createMentionAvatar(userId, label) {
  const uid = String(userId || "");
  const avatar = document.createElement("span");
  avatar.className = "mention-av";
  avatar.setAttribute("aria-hidden", "true");
  avatar.style.background = sigColor(uid || label);
  avatar.textContent = "@";
  if (!uid || MISSING_AVATARS.has(uid)) return avatar;

  const img = new Image();
  img.className = "mention-av-img";
  img.alt = "";
  img.onload = () => {
    // 캐시된 이미지는 NodeView가 DOM에 붙기 전에 load될 수도 있다. 연결 여부로 버리면
    // 사진이 있는데도 이번 렌더 내내 @로 남으므로, 성공 여부만 보고 표시한다.
    if (!img.naturalWidth) return;
    img.classList.add("on");
  };
  img.onerror = () => {
    MISSING_AVATARS.add(uid);
    img.remove();
  };
  avatar.appendChild(img);       // opacity:0 — 요청 중에도 아래 @가 그대로 보인다.
  img.src = "/api/avatar/" + encodeURIComponent(uid);
  return avatar;
}

/** 기존 element의 의미 속성/href는 보존하고, 보이는 내부 구조만 공통 멘션 badge로 맞춘다. */
export function paintMentionBadge(el, userId, rawLabel) {
  const uid = String(userId || "");
  const label = cleanLabel(rawLabel, uid);
  el.classList.add("mention", "mention-badge");
  el.setAttribute("data-mention-ui", "1");
  if (uid) {
    el.setAttribute("data-id", uid);
    el.setAttribute("data-uid", uid);
  }
  el.setAttribute("data-label", label);
  const name = document.createElement("span");
  name.className = "mention-nm";
  name.textContent = label;
  el.replaceChildren(createMentionAvatar(uid, label), name);
  return el;
}

/** Jira가 돌려준 user-hover 앵커와 canonical mention span을 저장 후 본문 UI로 보강한다. */
export function enhanceMentionBadges(root) {
  if (!root || !root.querySelectorAll) return;
  const selector = [
    ".tkt-desc [data-type='mention'][data-id]",
    ".tkt-desc .mention[data-id]",
    ".tkt-desc a.user-hover[href*='ViewProfile.jspa']",
  ].join(",");
  root.querySelectorAll(selector).forEach((el) => {
    if (el.getAttribute("data-mention-ui") === "1") return;
    const uid = profileId(el);
    const label = el.getAttribute("data-label") || el.textContent || uid;
    paintMentionBadge(el, uid, label);
  });
}
