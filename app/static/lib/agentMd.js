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

import { sigColor, initialOf } from "./colors.js";

const KEY_RE = /(?<![0-9A-Za-z-])([A-Z][A-Z0-9]*-\d+)(?![0-9A-Za-z-])/g;
const TICKET_TOKEN_RE = /\{\{ticket-(list|inline|detail):([A-Z][A-Z0-9]*-\d+)\}\}/g;
const MENTION_RE = /\[~([A-Za-z0-9_.:-]+)\]/g;
// 키 뒤에 모델이 붙인 따옴표 제목(`DL-118 "CDC 도입"`)은 뱃지가 실제 제목을 보여 주므로
// 중복이다 — 뱃지 렌더에서만 접는다(표 셀의 슬림 링크에서는 제목이 정보라 남긴다).
// 입력은 이미 esc() 를 거쳤으므로 곧은따옴표는 &quot; 로 온다.
const KEY_TITLED_RE = /(?<![0-9A-Za-z-])([A-Z][A-Z0-9]*-\d+)(?![0-9A-Za-z-])(?:\s*(?:&quot;|[“‘'])[^“”‘’'\n]{2,80}?(?:&quot;|[”’']))?/g;
// 모델이 사번 옆에 이름을 병기하는 일이 잦다(`skcc.x1042 최민서`, `최민서(skcc.x1042)`).
// 칩이 이미 이름을 그리므로 그대로 두면 이름이 두 번 보인다(실측) — 함께 삼킨다.
const UID_RE = /(?:([가-힣]{2,4})\s*[(（]\s*(skcc\.[a-z]{1,2}\d{2,6})\s*[)）]|(?<![0-9A-Za-z._])(skcc\.[a-z]{1,2}\d{2,6})(?![0-9A-Za-z._])(?:\s+([가-힣]{2,4})(?![가-힣]))?)/g;
// 문서 제목에 대괄호가 흔하다(`[데이터카탈로그] …`) — 제목 안의 `]` 를 허용하고
// `](http` 로 끝을 잡는다. 예전 패턴은 첫 `]` 에서 끊겨 링크가 통째로 평문이 됐다.
const MDLINK_RE = /\[([^\n]+?)\]\((https?:\/\/[^\s)]+)\)/g;
// Confluence 슬러그에도 대괄호가 그대로 들어온다 — URL 로 인정한다(마크다운 링크는
// 위에서 먼저 스태시하므로 충돌하지 않는다). 그러지 않으면 URL 이 중간에서 잘려
// 뒷부분이 평문으로 남았다(실측: `…/pages/123/` + `[데이터카탈로그]+…`).
const URL_RE = /(^|[\s(])(https?:\/\/[^\s<>()]+[^\s<>().,;:!?'"])/g;
const CONF_RE = /confluence|\/pages\/\d+|\/display\/|\/wiki\//i;

function esc(s) {
  return String(s == null ? "" : s)
    .replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;");
}

// 렌더 한 번 동안의 사번→본명 지도(서버 people). 모듈 전역이지만 렌더는 동기라 안전하다.
let PEOPLE = {};

/** 문서/웹 링크 → 뱃지. Confluence 는 conf-link(문서 제목), 그 외는 web-badge(favicon). */
function linkBadge(title, url, slim) {
  const t = (title || "").trim() || url;
  // 참조·표처럼 **나열하는 자리**는 뱃지가 과하다 — 제목이 걸린 평범한 링크면 충분하다
  // (사용자 지시: "참조나 근거의 문서·티켓은 하이퍼링크면 된다").
  if (slim) {
    // 제목이 없으면 Confluence URL 의 슬러그를 사람이 읽는 제목으로 편다 —
    // 참조에 `…/pages/123/[데이터카탈로그]+LAKE+적재주기+변경+절차` 가 통째로
    // 노출됐다(실측). 슬러그도 없으면 URL 그대로.
    return `<a class="ref-link" href="${url}" target="_blank" rel="noopener">` +
           `${t === url ? (slugTitle(url) || url) : t}</a>`;
  }
  if (CONF_RE.test(url)) {
    return `<a class="conf-link" href="${url}" target="_blank" rel="noopener" ` +
           `data-conf="1"><span class="conf-title">${t}</span></a>`;
  }
  return `<a class="web-badge" href="${url}" target="_blank" rel="noopener" ` +
         `style="--fav:url('/api/favicon?u=${encodeURIComponent(url)}')">${t}</a>`;
}

/** Confluence URL 의 마지막 조각(제목 슬러그) → 읽을 수 있는 제목. `+`·%인코딩을 편다. */
function slugTitle(url) {
  try {
    const seg = String(url).split("?")[0].split("#")[0].split("/").filter(Boolean).pop() || "";
    if (!seg || /^\d+$/.test(seg)) return "";
    const t = decodeURIComponent(seg.replace(/\+/g, " ")).trim();
    return t.length >= 2 && !/^https?:/i.test(t) ? esc(t) : "";
  } catch (e) { return ""; }
}

/** 티켓 키 → 본문·코멘트와 **같은 구조**의 jira-badge 스켈레톤. 타입·제목·상태는
 *  AgentView.augmentBadges() 가 api.ticketBadge 로 비동기 채움(렌더는 동기라서). */
function keyBadge(key, mode) {
  const variant = ["list", "inline", "detail"].includes(mode) ? mode : "inline";
  return `<a href="#" class="jira-badge jira-badge-${variant} tkt" data-key="${key}">` +
         `<span class="jb-type-icon" aria-hidden="true"></span><b class="jb-key">${key}</b>` +
         `<span class="jb-name"></span><span class="jb-owner"></span>` +
         `<span class="jb-meta"></span></a>`;
}

function personBadge(uid, name) {
  const label = (name || uid || "").trim();
  return `<span class="md-person mention" data-type="mention" data-id="${esc(uid)}" ` +
         `data-uid="${esc(uid)}"><span class="md-avt-wrap" style="background:${sigColor(uid)}">` +
         `${esc(initialOf(label, uid))}</span><span class="md-person-nm">${esc(label)}</span></span>`;
}

/** 인라인 문법. 이미 escape 된 문자열을 받는다.
 *  slim=true(표 셀)는 사람·문서 표기를 가볍게 하되 ticket은 목록형 badge로 유지한다. */
function inline(s, slim, ticketMode) {
  // ① 링크·URL 을 먼저 뱃지로 만들어 **스태시**한다 — 제목 속 티켓 키·사번이
  //    아래 치환에 오염되면 뱃지 안에 뱃지가 생긴다.
  const stash = [];
  const keep = (html) => { stash.push(html); return `\x00${stash.length - 1}\x00`; };
  s = s
    // 모델이 식별자를 강조하려고 티켓 키를 백틱으로 감싸는 일이 잦다. 먼저 격리하지 않으면
    // 아래 KEY_RE가 <code> 안의 키를 다시 뱃지로 바꿔 code+badge UI가 겹친다.
    // 티켓 하나만 든 백틱은 뱃지로 정규화하고, JQL/명령처럼 더 긴 코드는 그대로 보존한다.
    .replace(/`([^`]+)`/g, (_, code) => {
      const token = /^\s*\{\{ticket-(list|inline|detail):([A-Z][A-Z0-9]*-\d+)\}\}\s*$/.exec(code);
      if (token) return keep(keyBadge(token[2], token[1]));
      const key = /^\s*([A-Z][A-Z0-9]*-\d+)\s*$/.exec(code);
      if (key) return keep(keyBadge(key[1], ticketMode || (slim ? "list" : "inline")));
      return keep(`<code>${code}</code>`);
    })
    .replace(TICKET_TOKEN_RE, (_, mode, key) => keep(keyBadge(key, mode)))
    .replace(MENTION_RE, (_, uid) => keep(personBadge(uid, PEOPLE[uid])))
    .replace(MDLINK_RE, (_, t, u) => keep(linkBadge(t, u, slim)))
    .replace(URL_RE, (_, pre, u) => pre + keep(linkBadge("", u, slim)));
  s = s
    .replace(/\*\*([^*]+)\*\*/g, "<b>$1</b>")
    .replace(/(^|[^*\w])\*([^*\n]+)\*(?!\*)/g, "$1<i>$2</i>")
    // [n] 참조 마커 — 클릭하면 참조 칸으로 점프+하이라이트, 호버(title)로 문헌 미리보기.
    .replace(/\[(\d{1,2})\](?!\()/g, (mm, n) => {
      const ref = REFS[n];
      if (!ref) return mm;
      // 툴팁은 **평문**이어야 한다. escape 하고 따옴표까지 실체참조로 바꾼다.
      const tip = esc(ref).replace(/"/g, "&quot;");
      // ★ 결과를 스태시한다 — 그러지 않으면 아래 티켓 키·사번 치환이 **속성 안의 글자까지**
      //   뱃지 HTML 로 바꿔 속성이 조기 종료되고, 남은 조각이 본문에 새어 나온다
      //   (실측: `… 근거">[3]` 이 글자로 보였다).
      // ★ `title` 이 아니라 `data-tip` 이다 — 브라우저 기본 툴팁(노란 상자)은 하단 참조
      //   목록과 생김새가 따로 놀고, 뜨는 데 1초 넘게 걸리며, 줄바꿈도 못 준다(사용자 지적).
      //   화면 쪽에서 같은 모양의 커스텀 상자로 띄운다(AgentView.refTip).
      return keep(`<a href="#" class="ref-mark" data-ref="${n}" data-tip="${tip}">[${n}]</a>`);
    })
    // 티켓 키는 클릭하면 티켓 다이얼로그가 열린다 — 근거를 바로 확인할 수 있어야 믿을 수 있다.
    // `.tkt[data-key]` 는 **앱 전역 위임 처리기**가 잡는 관례다(app-root).
    // 표 밖에서는 본문 뱃지와 같은 모양(jira-badge)으로 — plain text 금지(사용자 지시).
    // 뱃지가 실제 제목을 보여 주므로 모델이 병기한 따옴표 제목은 접는다(중복).
    .replace(slim ? KEY_RE : KEY_TITLED_RE,
             (m, k) => keep(keyBadge(k, ticketMode || (slim ? "list" : "inline"))))
    // 사번은 프사+본명 칩으로 — "skcc.x1042 만 달랑"은 읽는 사람에게 아무 정보가 없다
    // (사용자 지적). 본명을 모르는 사번(지도에 없음)은 건드리지 않는다.
    .replace(UID_RE, (m, n1, u1, u2, n2) => {
      const uid = u1 || u2;
      const name = PEOPLE[uid];
      const side = (n1 || n2 || "").trim();
      // 병기된 이름이 **다른 사람**이면 손대지 않는다(잘못 삼키면 사실이 바뀐다).
      if (!name || (side && side !== name)) return m;
      return personBadge(uid, name);
    });
  // ② 스태시 복원
  return s.replace(/\x00(\d+)\x00/g, (_, i) => stash[+i]);
}

// [n] 마커가 가리키는 참조 지도(n → 참조 한 줄의 평문). 렌더 한 번 동안만 유효.
let REFS = {};

export function renderMarkdown(text, people) {
  PEOPLE = people || {};
  REFS = {};
  // ── 참조 섹션 분리 — 본문과 별개의 **접이식 영역**으로 그린다(사용자 요청).
  //    [n] 마커는 여기로 점프+하이라이트하고, 호버 툴팁(title)으로 문헌을 보여 준다.
  const m = /\n\*\*참조\*\*\s*\n([\s\S]+)$/.exec(text || "");
  let body = text || "", refItems = [];
  if (m) {
    const lines = m[1].split("\n").map((l) => l.trim()).filter(Boolean);
    // ★ **번호 목록 형태도 받는다.** `[1] DL-9045` 만 인식하던 탓에 모델이
    //   `1. DL-9045 — …` 로 내면 참조 섹션이 통째로 본문에 남아 **접기·하이퍼링크·2층
    //   표시가 전부 안 걸렸다**(실사용 지적). 같은 뜻을 두 표기로 쓰는 것은 모델의
    //   자유이고, 그걸 받아 주는 것이 화면의 일이다.
    const rows = lines.map((l) => /^-?\s*(?:\[(\d{1,2})\]|(\d{1,2})[.)])\s*(.*)$/.exec(l))
                      .filter(Boolean)
                      .map((r) => ({ n: r[1] || r[2], text: r[3] }));
    if (rows.length) {
      body = text.slice(0, m.index);
      // ★ **같은 출처를 두 번 싣지 않는다** — 모델이 표의 근거 칸마다 번호를 새로 매겨
      //   DL-9044 가 [2] 와 [6] 으로 두 번 나왔다(실사용 지적: "근거랑 중복되지 말라").
      //   먼저 나온 번호를 남기고, 뒤엣것은 그 번호로 접어 준다.
      const bySrc = new Map();
      refItems = [];
      rows.forEach((r) => {
        const src = String(r.text || "").split(/\s+(?:—|–|--|:)\s+/)[0].trim();
        const seen = bySrc.get(src);
        if (seen) { REFS[r.n] = seen.text; return; }   // 마커는 살리되 목록엔 한 번만
        bySrc.set(src, r);
        refItems.push(r);
      });
      refItems.forEach((r) => { REFS[r.n] = r.text; });
    }
  }
  let html = _render(body);
  if (refItems.length) {
    html += `<details class="agent-refs"><summary>참조 ${refItems.length}건</summary>` +
            `<div class="agent-refs-list">${refItems.map(refRow).join("")}</div></details>`;
  }
  return html;
}

/** 참조 한 줄 → **출처 / 설명 두 층**.
 *
 *  실측 지적: "하이퍼링크랑 뱃지랑 섞여 있고, 티켓 제목이 없고, 출처와 인용 문구가
 *  한 줄에 뒤섞였다." 그래서 ① 출처(무엇)와 설명(왜)을 줄로 가르고 ② 출처 모양을
 *  **하나로 통일**한다(티켓=키+제목, 문서=제목). 티켓 제목은 렌더가 동기라 비워 두고
 *  AgentView.augmentBadges() 가 채운다.
 */
function refRow(r) {
  const raw = String(r.text || "").trim();
  // "출처 — 설명" · "출처 - 설명" · "출처: 설명" 중 **처음** 나오는 구분자에서 한 번만 자른다.
  const cut = /^(.*?)\s+(?:—|–|--)\s+(.*)$/.exec(raw);
  let src = (cut ? cut[1] : raw).trim();
  const why = (cut ? cut[2] : "").trim();
  // 문서 "제목 (URL)" → 제목이 걸린 링크 하나. URL 만 있으면 슬러그를 제목으로 편다.
  src = src.replace(/^(.*?)\s*\((https?:\/\/[^\s)]+)\)$/, (mm, t, u) => `[${t.trim() || u}](${u})`);
  const key = /^([A-Z][A-Z0-9]*-\d+)\b(.*)$/.exec(src);
  // 참조 항목의 ticket은 입력 형식과 관계없이 detail badge로 통일한다. 모델이 raw key,
  // typed token, Jira markdown link 중 무엇을 출력해도 key를 찾아 같은 기계화 경로로 보낸다.
  // 링크의 label과 URL에 key가 함께 들어갈 수 있으므로 Set으로 중복을 제거한다.
  const ticketKeys = [...new Set(src.match(KEY_RE) || [])];
  // 키 뒤에 붙은 글("DL-9062 코멘트 (…)")은 **제목이 아니다** — 설명 쪽으로 옮긴다.
  // 제목은 항상 조회로 채운다(사용자 지시: 티켓 표기에 이름을 포함하라).
  const tail = key ? key[2].trim().replace(/^[—–\-:,]\s*/, "") : "";
  const why2 = [tail, why].filter(Boolean).join(" · ");
  const srcHtml = ticketKeys.length
    ? ticketKeys.map((ticketKey) => keyBadge(ticketKey, "detail")).join(" ")
    : inline(esc(src), true);
  return `<div class="agent-ref-item" data-ref="${r.n}">` +
         `<span class="ref-no">[${r.n}]</span>` +
         `<span class="ref-src">${srcHtml}</span>` +
         (why2 ? `<div class="ref-why">${inline(esc(why2), true)}</div>` : "") +
         `</div>`;
}

function _render(text) {
  const lines = esc(text || "").split("\n");
  const out = [];
  let list = null;          // "ul" | "ol" | null
  let detailList = false;
  let detailLead = false;
  const ticketTotal = (String(text || "").match(KEY_RE) || []).length;

  const closeList = () => {
    if (!list) return;
    out.push(`</${list}>`);
    if (detailList) out.push("</details>");
    list = null;
    detailList = false;
  };

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
      detailLead = false;
      if (isSep(line)) continue;                       // |---|---| 구분줄은 버린다
      if (!tbl) tbl = { header: cells(line), rows: [] };
      else tbl.rows.push(cells(line));
      continue;
    }
    flushTable();
    if (!line.trim()) { closeList(); continue; }

    const h = /^(#{1,4})\s+(.*)$/.exec(line);
    if (h) {
      closeList();
      detailLead = false;
      // 답변의 `#`~`####` → h2~h4. 티켓 본문 CSS 가 h1~h4 만 꾸미므로 그 범위를 넘지
      // 않는다(넘기면 답변에서만 헤딩이 밋밋해진다 — 렌더 체계를 하나로).
      const lv = Math.min(h[1].length + 1, 4);
      out.push(`<h${lv}>${inline(h[2], false, "inline")}</h${lv}>`);
      continue;
    }

    if (/^(-{3,}|\*{3,})$/.test(line.trim())) {
      closeList(); detailLead = false; out.push("<hr>"); continue;
    }

    const q = /^&gt;\s?(.*)$/.exec(line);
    if (q) {
      closeList();
      detailLead = false;
      // GitHub 식 알림 인용(`> [!NOTE]`)은 티켓 본문의 **콜아웃**과 같은 마크업으로 —
      // 에디터·티켓 화면이 이미 그 CSS 를 갖고 있다(사용자 지시: 렌더 체계는 하나).
      const co = /^\[!(NOTE|INFO|TIP|SUCCESS|WARNING|CAUTION|ERROR|DANGER)\]\s*(.*)$/i
        .exec(q[1]);
      if (co) {
        const kind = { caution: "warning", danger: "error" }[co[1].toLowerCase()]
          || co[1].toLowerCase();
        out.push(`<div class="callout callout-${kind}">${inline(co[2], false, "inline")}</div>`);
      } else {
        out.push(`<blockquote>${inline(q[1], false, "inline")}</blockquote>`);
      }
      continue;
    }

    const ul = /^\s*[-*]\s+(.*)$/.exec(line);
    if (ul) {
      if (list !== "ul") {
        closeList();
        detailList = detailLead && /[A-Z][A-Z0-9]*-\d+/.test(ul[1]);
        if (detailList) out.push('<details class="agent-ticket-details"><summary>티켓 상세</summary>');
        out.push("<ul>");
        list = "ul";
      }
      const mode = detailList ? "detail" : (ticketTotal > 2 ? "list" : "detail");
      out.push(`<li>${inline(ul[1], false, mode)}</li>`);
      detailLead = false;
      continue;
    }
    const ol = /^\s*\d+\.\s+(.*)$/.exec(line);
    if (ol) {
      if (list !== "ol") { closeList(); out.push("<ol>"); list = "ol"; }
      out.push(`<li>${inline(ol[1], false, ticketTotal > 2 ? "list" : "detail")}</li>`);
      detailLead = false;
      continue;
    }

    closeList();
    const lineTickets = (line.match(KEY_RE) || []).length;
    out.push(`<p>${inline(line, false, lineTickets > 1 ? "list" : "inline")}</p>`);
    detailLead = /(?:다음의|아래의)\s*(?:상위\s*)?(?:Task|태스크|테스크|티켓)/.test(line);
  }
  closeList();
  flushTable();
  return out.join("");
}
