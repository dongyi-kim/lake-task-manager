// agentMd.js — 에이전트 답변(마크다운) → HTML. 아주 작은 부분집합만 지원한다.
//
// ★ **먼저 이스케이프하고 나중에 마크업한다.** 이 글은 LLM 이 썼고, 그 재료는 남이 쓴 티켓
//   본문·코멘트다. 거기 `<img onerror=...>` 가 들어 있으면 그대로 우리 화면에서 실행된다.
//   그래서 원문을 통째로 escape 한 뒤, **우리가 아는 문법만** 다시 태그로 바꾼다.
//   (라이브러리를 하나 더 들이는 대신 이 방식을 쓴 이유 — 지원 범위를 우리가 정할 수 있고,
//    무엇이 HTML 이 되는지가 이 파일 안에서 전부 보인다.)
//
// 지원: ## 제목 · **굵게** · `코드` · - 목록 · 1. 목록 · > 인용 · --- · **표(| a | b |)** · 문단
//       그리고 **티켓 키 자동 링크**(DL-123 → 티켓 열기).
// 표를 지원하는 이유 — 진척률·건수처럼 나열형 숫자를 불릿으로 길게 쓰면 가시성이 없다
// (사용자 지적). 모델에게 표로 쓰라고 지시했으니 렌더러가 못 그리면 말짱 꽝이다.
//
// ★ 언급은 전부 **뱃지**다(사용자 지시: plain text 금지). 티켓 키는 본문 뱃지(jira-badge)와
//   같은 구조로, [제목](URL) 링크·맨 URL 은 Confluence/웹 뱃지로, 사번은 프사+본명 칩으로.
//   유일한 예외는 **표 안** — 제목을 나열하는 자리라 셀에서는 가벼운 키 링크만 쓴다.
//   뱃지의 타입·상태 채움은 AgentView.augmentBadges() 가 비동기로 한다(렌더는 동기라서).

const KEY_RE = /\b([A-Z][A-Z0-9]*-\d+)\b/g;
// 키 뒤에 모델이 붙인 따옴표 제목(`DL-118 "CDC 도입"`)은 뱃지가 실제 제목을 보여 주므로
// 중복이다 — 뱃지 렌더에서만 접는다(표 셀의 슬림 링크에서는 제목이 정보라 남긴다).
// 입력은 이미 esc() 를 거쳤으므로 곧은따옴표는 &quot; 로 온다.
const KEY_TITLED_RE = /\b([A-Z][A-Z0-9]*-\d+)\b(?:\s*(?:&quot;|[“‘'])[^“”‘’'\n]{2,80}?(?:&quot;|[”’']))?/g;
const UID_RE = /\b(skcc\.[a-z]{1,2}\d{2,6})\b/g;
const MDLINK_RE = /\[([^\]\n]+)\]\((https?:\/\/[^\s)]+)\)/g;
const URL_RE = /(^|[\s(])(https?:\/\/[^\s<>()\[\]]+[^\s<>()\[\].,;:!?'"])/g;
const CONF_RE = /confluence|\/pages\/\d+|\/display\/|\/wiki\//i;

function esc(s) {
  return String(s == null ? "" : s)
    .replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;");
}

// 렌더 한 번 동안의 사번→본명 지도(서버 people). 모듈 전역이지만 렌더는 동기라 안전하다.
let PEOPLE = {};

/** 문서/웹 링크 → 뱃지. Confluence 는 conf-link(문서 제목), 그 외는 web-badge(favicon). */
function linkBadge(title, url) {
  const t = (title || "").trim() || url;
  if (CONF_RE.test(url)) {
    return `<a class="conf-link" href="${url}" target="_blank" rel="noopener" ` +
           `data-conf="1"><span class="conf-title">${t}</span></a>`;
  }
  return `<a class="web-badge" href="${url}" target="_blank" rel="noopener" ` +
         `style="--fav:url('/api/favicon?u=${encodeURIComponent(url)}')">${t}</a>`;
}

/** 티켓 키 → 본문·코멘트와 **같은 구조**의 jira-badge 스켈레톤. 타입·제목·상태는
 *  AgentView.augmentBadges() 가 api.ticketBadge 로 비동기 채움(렌더는 동기라서). */
function keyBadge(key) {
  return `<a href="#" class="jira-badge tkt" data-key="${key}">` +
         `<span class="tbadge v-solid jb-type"></span><b class="jb-key">${key}</b>` +
         `<span class="jb-name"></span><span class="jb-meta"></span></a>`;
}

/** 인라인 문법. 이미 escape 된 문자열을 받는다.
 *  slim=true(표 셀): 제목·키를 나열하는 자리라 무거운 뱃지 대신 가벼운 키 링크만 —
 *  "plain text 금지"의 **유일한 예외**(사용자 지시: 표에서 의도적 나열만 예외). */
function inline(s, slim) {
  // ① 링크·URL 을 먼저 뱃지로 만들어 **스태시**한다 — 제목 속 티켓 키·사번이
  //    아래 치환에 오염되면 뱃지 안에 뱃지가 생긴다.
  const stash = [];
  const keep = (html) => { stash.push(html); return `\x00${stash.length - 1}\x00`; };
  s = s
    .replace(MDLINK_RE, (_, t, u) => keep(linkBadge(t, u)))
    .replace(URL_RE, (_, pre, u) => pre + keep(linkBadge("", u)));
  s = s
    .replace(/`([^`]+)`/g, (_, c) => `<code>${c}</code>`)
    .replace(/\*\*([^*]+)\*\*/g, "<b>$1</b>")
    // 티켓 키는 클릭하면 티켓 다이얼로그가 열린다 — 근거를 바로 확인할 수 있어야 믿을 수 있다.
    // `.tkt[data-key]` 는 **앱 전역 위임 처리기**가 잡는 관례다(app-root).
    // 표 밖에서는 본문 뱃지와 같은 모양(jira-badge)으로 — plain text 금지(사용자 지시).
    // 뱃지가 실제 제목을 보여 주므로 모델이 병기한 따옴표 제목은 접는다(중복).
    .replace(slim ? KEY_RE : KEY_TITLED_RE,
             slim ? '<a href="#" class="tkt" data-key="$1">$1</a>'
                  : (m, k) => keep(keyBadge(k)))
    // 사번은 프사+본명 칩으로 — "skcc.x1042 만 달랑"은 읽는 사람에게 아무 정보가 없다
    // (사용자 지적). 본명을 모르는 사번(지도에 없음)은 건드리지 않는다.
    .replace(UID_RE, (m, uid) => {
      const name = PEOPLE[uid];
      if (!name) return m;
      return `<span class="md-person" title="${uid}">` +
             `<img class="md-avt" src="/api/avatar/${uid}" alt="" ` +
             `onerror="this.style.display='none'">${name}</span>`;
    });
  // ② 스태시 복원
  return s.replace(/\x00(\d+)\x00/g, (_, i) => stash[+i]);
}

export function renderMarkdown(text, people) {
  PEOPLE = people || {};
  return _render(text);
}

function _render(text) {
  const lines = esc(text || "").split("\n");
  const out = [];
  let list = null;          // "ul" | "ol" | null

  const closeList = () => { if (list) { out.push(`</${list}>`); list = null; } };

  // 표 상태 — 연속된 | … | 줄을 모아 하나의 <table> 로
  let tbl = null;           // {header: [...], rows: [[...]]}
  const isRow = (l) => /^\s*\|.*\|\s*$/.test(l);
  const isSep = (l) => /^\s*\|[\s:|-]+\|\s*$/.test(l);
  const cells = (l) => l.trim().replace(/^\||\|$/g, "").split("|").map((c) => c.trim());
  const flushTable = () => {
    if (!tbl) return;
    const h = tbl.header.map((c) => `<th>${inline(c, true)}</th>`).join("");
    const b = tbl.rows.map((r) => "<tr>" + r.map((c) => `<td>${inline(c, true)}</td>`).join("") + "</tr>").join("");
    out.push(`<table><thead><tr>${h}</tr></thead><tbody>${b}</tbody></table>`);
    tbl = null;
  };

  for (const raw of lines) {
    const line = raw.replace(/\s+$/, "");
    if (isRow(line)) {
      closeList();
      if (isSep(line)) continue;                       // |---|---| 구분줄은 버린다
      if (!tbl) tbl = { header: cells(line), rows: [] };
      else tbl.rows.push(cells(line));
      continue;
    }
    flushTable();
    if (!line.trim()) { closeList(); continue; }

    const h = /^(#{1,4})\s+(.*)$/.exec(line);
    if (h) { closeList(); out.push(`<h${h[1].length + 2}>${inline(h[2])}</h${h[1].length + 2}>`); continue; }

    if (/^(-{3,}|\*{3,})$/.test(line.trim())) { closeList(); out.push("<hr>"); continue; }

    const q = /^&gt;\s?(.*)$/.exec(line);
    if (q) { closeList(); out.push(`<blockquote>${inline(q[1])}</blockquote>`); continue; }

    const ul = /^\s*[-*]\s+(.*)$/.exec(line);
    if (ul) {
      if (list !== "ul") { closeList(); out.push("<ul>"); list = "ul"; }
      out.push(`<li>${inline(ul[1])}</li>`);
      continue;
    }
    const ol = /^\s*\d+\.\s+(.*)$/.exec(line);
    if (ol) {
      if (list !== "ol") { closeList(); out.push("<ol>"); list = "ol"; }
      out.push(`<li>${inline(ol[1])}</li>`);
      continue;
    }

    closeList();
    out.push(`<p>${inline(line)}</p>`);
  }
  closeList();
  flushTable();
  return out.join("");
}
