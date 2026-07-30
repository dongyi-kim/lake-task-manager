// voc.js — 사용자 VoC 티켓 제목 접두 파싱.
//
// 시스템이 만든 VoC 티켓은 제목 앞에 카테고리가 **항상** 붙는다:  [ 대분류 - 소분류 ] 실제 제목
// (사람이 직접 만든 VoC 는 안 붙는다). 그래서 뱃지는 [ VoC | 대분류 | 소분류 ] 로 쪼개 보이고,
// 카드 제목에서는 그 접두를 떼어 실제 제목만 남긴다. 접두가 없으면(사람 생성) 뱃지는 'VoC' 하나뿐.
//
// ★ 이 파싱은 **VoC 티켓에만** 적용한다 — 일반 티켓도 [ETL] 같은 접두를 쓰므로(잘못 떼면 안 됨).
//   호출부가 card.voc 일 때만 vocStripTitle 을 쓴다.

/** 제목 → { major, minor, rest }. 접두 [ 대분류 - 소분류 ] 가 없으면 major/minor=null, rest=원제목. */
export function parseVoc(title) {
  const t = title || "";
  const m = /^\s*\[([^\]]*)\]\s*([\s\S]*)$/.exec(t);
  if (!m) return { major: null, minor: null, rest: t.trim() };
  const inner = m[1].trim();
  const rest = m[2].trim();
  // 대분류 - 소분류: 첫 ' - '(공백 포함) 를 우선 구분자로, 없으면 첫 '-' 로 나눈다.
  let at = inner.indexOf(" - ");
  let len = 3;
  if (at < 0) { at = inner.indexOf("-"); len = 1; }
  if (at < 0) return { major: inner || null, minor: null, rest };   // 한 단계만 있는 경우
  return { major: inner.slice(0, at).trim(), minor: inner.slice(at + len).trim(), rest };
}

/** 뱃지 세그먼트: ['VoC'] · ['VoC', 대분류] · ['VoC', 대분류, 소분류]. */
export function vocBadgeSegs(title) {
  const p = parseVoc(title);
  const segs = ["VoC"];
  if (p.major) segs.push(p.major);
  if (p.minor) segs.push(p.minor);
  return segs;
}

/** 카드에 보일 제목 — VoC 접두([…])를 뗀 나머지(없으면 원제목). VoC 티켓에만 쓸 것. */
export function vocStripTitle(title) {
  const p = parseVoc(title);
  return p.rest || (title || "");
}
