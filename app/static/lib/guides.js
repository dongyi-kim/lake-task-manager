// guides.js — 기능 안내(가이드) **레지스트리**. 여기가 단일 소스다.
//
// 왜 목록으로 관리하나: 안내를 컴포넌트마다 흩어 두면 (1) 어떤 안내가 있는지 아무도 모르고
// (2) 기능이 사라져도 안내가 남고 (3) '한 번만 보여 준다' 규칙이 제각각이 된다.
// → 항목을 **이 배열에 추가/삭제**하는 것만으로 안내가 생기고 사라진다.
//
// 규칙
//  · 유저에게 **한 번만** 보인다. 본 기록은 브라우저(localStorage)에 id 로 남는다.
//  · 항목을 지우면 그 안내는 즉시 사라지고, 본 기록도 다음 실행 때 청소된다(pruneSeen).
//    → 사라진 기능의 기록이 저장소에 영원히 쌓이지 않는다.
//  · id 는 **재사용하지 마라.** 같은 id 를 다른 안내에 다시 쓰면 이미 본 사람에겐 안 뜬다.
//  · 한 번에 하나만 띄운다(order 오름차순). 화면을 안내로 도배하지 않는다.
//
// 항목 형태
//   id      고유 문자열(기록 키). 기능이 바뀌어 다시 알려야 하면 **새 id** 를 쓴다.
//   anchor  가리킬 요소의 CSS 선택자. 이 요소가 화면에 없으면 안내는 뜨지 않는다
//           (= 기능이 없는 화면에선 저절로 조용하다).
//   routes  이 화면들에서만. 비우면 어디서든.
//   place   말풍선 위치 — 'right' | 'left' | 'top' | 'bottom'
//   title / body  안내 문구. 평문만(마크다운은 그대로 글자로 보인다).
//   order   낮을수록 먼저.

const KEY = "lake.guides.seen";

/** 데이터를 그려 주는 화면들 — '불러오는 중' 이나 낡은 캐시를 만날 수 있는 곳. */
export const DATA_ROUTES = ["wbs", "vit", "workload", "mytasks"];

export const GUIDES = [
  {
    id: "refresh-fab-1",
    anchor: ".fab-refresh",
    routes: DATA_ROUTES,
    place: "right",
    order: 10,
    title: "화면이 안 뜨거나 데이터가 낡았을 때",
    body: "이 버튼을 누르면 캐시를 비우고 지금 화면을 처음부터 다시 받습니다. "
        + "누를 때 사내 Jira 인증도 함께 확인해서, 세션이 끊겼으면 다시 로그인합니다.",
  },
];

/** 본 기록(id 목록). 저장소가 없거나 깨졌으면 빈 목록 — 안내가 못 뜨는 것보다 낫다. */
export function seenIds() {
  try {
    const v = JSON.parse(window.localStorage.getItem(KEY) || "[]");
    return Array.isArray(v) ? v.filter((x) => typeof x === "string") : [];
  } catch (e) { return []; }
}

function writeSeen(ids) {
  try { window.localStorage.setItem(KEY, JSON.stringify(ids)); } catch (e) { /* 사생활 모드 등 */ }
}

/** 이 안내는 봤다고 기록한다. */
export function markSeen(id) {
  const ids = seenIds();
  if (ids.indexOf(id) < 0) { ids.push(id); writeSeen(ids); }
}

/** 레지스트리에 없는 id 를 버린다 — 지워진 안내의 기록이 영원히 남지 않게. */
export function pruneSeen() {
  const live = new Set(GUIDES.map((g) => g.id));
  const ids = seenIds(), kept = ids.filter((id) => live.has(id));
  if (kept.length !== ids.length) writeSeen(kept);
}

/** 전부 다시 보기(설정에서 부른다). */
export function resetSeen() { writeSeen([]); }

/**
 * 지금 이 화면에서 띄울 안내 하나. 없으면 null.
 * 아직 안 봤고 · 이 화면이 대상이고 · **가리킬 요소가 실제로 있을 때만** 고른다.
 */
export function nextGuide(route) {
  const seen = new Set(seenIds());
  const list = GUIDES.filter((g) => !seen.has(g.id))
    .filter((g) => !g.routes || g.routes.indexOf(route) >= 0)
    .sort((a, b) => (a.order || 0) - (b.order || 0));
  for (const g of list) {
    if (document.querySelector(g.anchor)) return g;
  }
  return null;
}
