// agentMd.js — 에이전트 답변(마크다운) → HTML. 아주 작은 부분집합만 지원한다.
//
// ★ **먼저 이스케이프하고 나중에 마크업한다.** 이 글은 LLM 이 썼고, 그 재료는 남이 쓴 티켓
//   본문·코멘트다. 거기 `<img onerror=...>` 가 들어 있으면 그대로 우리 화면에서 실행된다.
//   그래서 원문을 통째로 escape 한 뒤, **우리가 아는 문법만** 다시 태그로 바꾼다.
//   (라이브러리를 하나 더 들이는 대신 이 방식을 쓴 이유 — 지원 범위를 우리가 정할 수 있고,
//    무엇이 HTML 이 되는지가 이 파일 안에서 전부 보인다.)
//
// 지원: ## 제목 · **굵게** · `코드` · - 목록 · 1. 목록 · > 인용 · --- · 문단
//       그리고 **티켓 키 자동 링크**(DL-123 → 티켓 열기).

const KEY_RE = /\b([A-Z][A-Z0-9]*-\d+)\b/g;

function esc(s) {
  return String(s == null ? "" : s)
    .replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;");
}

/** 인라인 문법. 이미 escape 된 문자열을 받는다. */
function inline(s) {
  return s
    .replace(/`([^`]+)`/g, (_, c) => `<code>${c}</code>`)
    .replace(/\*\*([^*]+)\*\*/g, "<b>$1</b>")
    // 티켓 키는 클릭하면 티켓 다이얼로그가 열린다 — 근거를 바로 확인할 수 있어야 믿을 수 있다.
    // `.tkt[data-key]` 는 **앱 전역 위임 처리기**가 잡는 관례다(app-root). 코멘트·검색 결과의
    // 티켓 링크와 똑같이 동작하므로 여기만 특별히 다르게 굴지 않는다.
    .replace(KEY_RE, '<a href="#" class="tkt" data-key="$1">$1</a>');
}

export function renderMarkdown(text) {
  const lines = esc(text || "").split("\n");
  const out = [];
  let list = null;          // "ul" | "ol" | null

  const closeList = () => { if (list) { out.push(`</${list}>`); list = null; } };

  for (const raw of lines) {
    const line = raw.replace(/\s+$/, "");
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
  return out.join("");
}
