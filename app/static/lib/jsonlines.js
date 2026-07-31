// jsonlines.js — JSON 원문에서 **줄 위치**를 찾아낸다. 검증 오류를 편집기의 그 줄에 꽂기 위해서.
//
// 검증기는 "3번 항목의 duedate 가 틀렸다" 고 말한다. 그건 정확하지만 **어디를 고쳐야 하는지는
// 안 알려 준다** — 사용자가 항목을 눈으로 세야 한다. 여기서 항목 번호를 줄번호로 바꿔 준다.
//
// JSON.parse 는 위치를 알려 주지만(브라우저마다 형식이 다르다) 항목 경계는 안 알려 준다.
// 그래서 원문을 한 번 훑으며 items 배열의 **직속 자식 객체**가 시작하는 줄을 기록한다.
// 훑기는 문자열/이스케이프만 다루면 되므로 짧고, 깨진 JSON 이어도 도중까지는 답을 준다
// (입력 중인 글에도 표시가 붙어야 쓸모가 있다).

/**
 * JSON 안의 주석(`//`, `/* *\/`)을 지운다 — JSON.parse 는 주석을 모른다.
 *
 * 예제에 설명을 달아 두려면(그게 이 입력창의 가장 큰 도움이다) 주석을 받아 줘야 한다.
 * ★ **지우지 않고 공백으로 덮는다** — 글자 수와 줄 수가 그대로여야 오류 위치(줄번호)가
 *   원문과 어긋나지 않는다. 문자열 안의 `//` 는 주석이 아니므로 건드리지 않는다.
 */
export function stripJsonComments(text) {
  const s = String(text == null ? "" : text);
  let out = "", i = 0, inStr = false, esc = false;
  while (i < s.length) {
    const c = s[i];
    if (inStr) {
      out += c;
      if (esc) esc = false;
      else if (c === "\\") esc = true;
      else if (c === '"') inStr = false;
      i++; continue;
    }
    if (c === '"') { inStr = true; out += c; i++; continue; }
    if (c === "/" && s[i + 1] === "/") {
      while (i < s.length && s[i] !== "\n") { out += " "; i++; }
      continue;
    }
    if (c === "/" && s[i + 1] === "*") {
      const end = s.indexOf("*/", i + 2);
      const stop = end < 0 ? s.length : end + 2;
      for (; i < stop; i++) out += (s[i] === "\n" ? "\n" : " ");
      continue;
    }
    out += c; i++;
  }
  return out;
}

/**
 * items 배열의 i 번째 항목이 시작하는 줄번호(1-based) 목록.
 * items 를 못 찾으면 빈 배열 — 표시를 안 할 뿐, 검증 자체는 영향받지 않는다.
 */
export function itemStartLines(text) {
  // 주석 안의 중괄호가 항목으로 세어지면 안 된다(공백으로 덮으므로 줄 위치는 그대로).
  const s = stripJsonComments(text);
  const out = [];
  let line = 1, i = 0;
  let inStr = false, esc = false;
  let depth = 0;                 // 중괄호/대괄호 깊이
  let itemsDepth = -1;           // items 배열이 열린 깊이(그 안의 depth+1 객체가 항목이다)
  let lastKey = null;            // 방금 지나온 키 이름
  let keyStart = -1;             // 지금 읽는 문자열이 시작한 위치(여는 따옴표)

  for (; i < s.length; i++) {
    const c = s[i];
    if (c === "\n") { line++; continue; }

    if (inStr) {
      if (esc) { esc = false; continue; }
      if (c === "\\") { esc = true; continue; }
      if (c === '"') {
        inStr = false;
        // 이 문자열이 키인지(뒤에 ':') 보고 기억해 둔다.
        let j = i + 1;
        while (j < s.length && /\s/.test(s[j])) j++;
        lastKey = s[j] === ":" ? s.slice(keyStart + 1, i) : null;
      }
      continue;
    }

    if (c === '"') { inStr = true; esc = false; keyStart = i; continue; }
    if (c === "[") {
      depth++;
      if (lastKey === "items" && itemsDepth < 0) itemsDepth = depth;
      lastKey = null;
      continue;
    }
    if (c === "{") {
      depth++;
      // items 배열의 **직속** 자식 객체만 항목이다(항목 안의 중첩 객체는 아니다).
      if (itemsDepth >= 0 && depth === itemsDepth + 1) out.push(line);
      lastKey = null;
      continue;
    }
    if (c === "]" || c === "}") {
      if (itemsDepth >= 0 && c === "]" && depth === itemsDepth) itemsDepth = -2;   // items 끝 — 더 안 본다
      depth--;
      lastKey = null;
      continue;
    }
  }
  return out;
}

/** JSON.parse 오류 메시지에서 줄번호를 뽑는다. 브라우저마다 형식이 달라 둘 다 본다. */
export function parseErrorLine(text, message) {
  const m = String(message || "");
  const byLine = /line (\d+)/i.exec(m);
  if (byLine) return parseInt(byLine[1], 10);
  const byPos = /position (\d+)/i.exec(m);
  if (byPos) {
    const at = parseInt(byPos[1], 10);
    return String(text || "").slice(0, at).split("\n").length;
  }
  return null;
}

/**
 * 검증 오류 목록 → 붉게 표시할 줄번호들.
 * index 가 있으면 그 항목의 줄, 없으면(최상위 오류) 문법 오류 줄 또는 1행.
 */
export function errorLines(text, errors) {
  const starts = itemStartLines(text);
  const out = new Set();
  for (const e of errors || []) {
    if (e && typeof e.index === "number" && starts[e.index]) out.add(starts[e.index]);
    else {
      const ln = parseErrorLine(text, (e && e.message) || "");
      if (ln) out.add(ln);
    }
  }
  return [...out];
}
