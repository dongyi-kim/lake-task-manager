// CommentEditor.js — TipTap 기반 댓글 작성/수정 에디터 (모던 Confluence/Jira 스타일).
// · 마크다운 input rule: '# '·'## '·'- '·'1. '·'> '·백틱3개 실시간 변환 (StarterKit)
// · 고정 툴바: 굵게/기울임/취소선/코드 · H1~3 · 불릿/번호/인용/코드블록 · 링크/표/이미지
// · @사람 멘션: '@' 입력 → 유저 자동완성 팝업 → [~사번] 으로 저장(읽기 시 사용자 링크)
// · 링크 붙여넣기: URL 붙여넣으면 자동 링크(문서/웹 뱃지는 읽기 렌더에서 앱이 처리)
// · 이미지 붙여넣기/드롭 = 제출 시 업로드: 로컬 objectURL 미리보기 → 제출 때 첨부 업로드·롤백
// 부모는 submitFn(finalHTML) 만 넘긴다(작성/수정은 부모가 선택). 출력은 HTML(서버가 wiki 로 변환).
import { loadTiptap } from "../../lib/tiptap.js";
import { ensureHljsTheme } from "../../lib/hljs.js";
import { saveDraft, loadDraft, clearDraft, purgeExpired } from "../../lib/draft.js";
import { api } from "../../lib/api.js";
import LinkPicker from "./LinkPicker.js";
import MarkdownTableDialog from "./MarkdownTableDialog.js";
import { extOf } from "../../lib/filetype.js";
import { sigColor, initialOf, typeLabel, TYPE_BG } from "../../lib/colors.js";
import { createManagedMentionItems, mentionInitialUsers, rememberUser } from "../../lib/userSuggestions.js";
import { typeaheadDelay } from "../../lib/typeahead.js";
import { createManagedSuggestionRenderer } from "../../lib/suggestionPopup.js";
import { pushToast } from "../../lib/toast.js";
import { agentApi } from "../../lib/agentApi.js";
import { beginBusy } from "../../lib/uibusy.js";
import AgentSettingsDialog from "./AgentSettingsDialog.js";
import { paintMentionBadge } from "../../lib/mentionBadge.js";

// 첨부 업로드 재시도 — prod 는 SSO 세션/사내망 탓에 첨부가 간헐적으로 삐끗한다. 한 번 실패했다고
// 파일을 버리지 않고 최대 이만큼 **다시** 올려 본다(총 시도 횟수).
const UPLOAD_TRIES = 3;
function sleep(ms) { return new Promise((r) => setTimeout(r, ms)); }

// 끝내 못 올린 파일의 본문 참조(blob objectURL)를 통째로 걷어낸다 — 안 지우면 저장된 본문에
// 죽은 blob: 링크나 깨진 이미지가 남는다. 이미지 <img>·파일 뱃지 <a> 를 태그째 제거한다.
function stripPendingRef(html, url) {
  const u = url.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
  return (html || "")
    .replace(new RegExp('<img\\b[^>]*\\bsrc="' + u + '"[^>]*>', "gi"), "")
    .replace(new RegExp('<a\\b[^>]*\\bhref="' + u + '"[^>]*>.*?<\\/a>', "gi"), "")
    .split(url).join("");                 // 혹시 남은 raw 참조까지
}

function esc(s) { return String(s == null ? "" : s).replace(/[&<>"]/g, (c) => (
  { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c])); }

// 멘션 팝업 아바타 — 네트워크 요청(=404 스팸) 없이 이니셜 원.
// 색은 기본 아바타·댓글 구분 바와 같은 시그니처 컬러(colors.js) 를 쓴다.
function mnAvatar(name, id) {
  // 이니셜(시그니처색)을 **바탕으로 깔고** 그 위에 프로필 사진을 async 로 얹는다 — 로드되면 사진이
  // 덮고(부드럽게), 404/실패면 paint 가 img 를 지워 이니셜이 그대로 남는다. 바이트·브라우저 캐시라
  // 한 번 받은 뒤엔 즉시. (전엔 사진 없이 이니셜만 떠 '사진이 안 뜬다' 였다.)
  const img = id ? `<img class="mn-av-img" src="/api/avatar/${encodeURIComponent(id)}" alt="" loading="lazy">` : "";
  return `<span class="mn-av" style="background:${sigColor(id || name)}">${esc(initialOf(name, id))}${img}</span>`;
}
const _URL_RE = /^https?:\/\/\S+$/i;


// Jira 콜아웃 매크로 블록 — <div class="callout callout-info"> <-> {info}…{info}.
// 표준 4종(info/note/tip/warning)을 툴바로 넣는다. 렌더 CSS(.tkt-desc .callout*)를 그대로 쓴다.
/**
 * 편집 중인 코드블럭에 **줄번호**를 붙인다 — 코드블럭 노드뷰.
 *
 * 왜 노드뷰인가: 처음엔 완성된 <pre> 에 거터 <span> 을 끼우거나(렌더된 본문에서 쓰는 방법)
 * data 속성을 달아 CSS 로 그리려 했는데, **둘 다 에디터 안에서는 되돌려진다** — ProseMirror 는
 * 자기 DOM 에 낯선 변화가 보이면 상태로부터 다시 그려 지워 버린다(실제로 setAttribute 가
 * 곧바로 사라졌다). 노드뷰는 그 DOM 을 **우리가 소유한다고 선언**하는 유일한 방법이다.
 *
 * contentDOM(=<code>)만 에디터가 건드리고, 거터는 그 바깥이라 안전하다. 문서 모델에도
 * 안 들어가므로 저장되는 본문에 번호가 섞이지 않는다.
 */
function codeLineNumbers(T) {
  return T.CodeBlockLowlight.extend({
    addNodeView() {
      // configure({ HTMLAttributes }) 로 준 것은 노드뷰 인자에 안 섞여 온다 — 직접 얹는다.
      // (여기서 빠뜨리면 편집 중에만 class 가 사라져 저장본과 화면이 달라 보인다.)
      const base = (this.options && this.options.HTMLAttributes) || {};
      return ({ node, HTMLAttributes }) => {
        const pre = document.createElement("pre");
        Object.entries({ ...base, ...(HTMLAttributes || {}) }).forEach(([k, v]) => {
          if (v != null) pre.setAttribute(k, v);
        });
        pre.classList.add("has-ln");
        const gut = document.createElement("span");
        gut.className = "ln-gutter";
        gut.setAttribute("contenteditable", "false");   // 커서가 번호 안으로 들어가지 않게
        const code = document.createElement("code");
        if (node.attrs.language) code.className = "language-" + node.attrs.language;
        pre.append(gut, code);

        const paint = (n) => {
          const cnt = (n.textContent || "").replace(/\n$/, "").split("\n").length;
          const want = Array.from({ length: cnt }, (_, i) => i + 1).join("\n");
          if (gut.textContent !== want) gut.textContent = want;
        };
        paint(node);

        return {
          dom: pre,
          contentDOM: code,
          update(updated) {
            if (updated.type.name !== node.type.name) return false;
            node = updated;
            // 언어를 바꾸면 강조 클래스도 바뀐다(lowlight 는 이 class 를 보고 색을 입힌다).
            const want = node.attrs.language ? "language-" + node.attrs.language : "";
            if (code.className !== want) code.className = want;
            paint(node);
            return true;
          },
          // 거터는 우리 것이다 — 여기서 난 변화를 에디터가 문서 변경으로 오해하면 안 된다.
          ignoreMutation(m) { return m.target === gut || gut.contains(m.target); },
        };
      };
    },
  });
}

function calloutExt(T) {
  return T.Node.create({
    name: "callout",
    group: "block",
    content: "block+",
    defining: true,
    addAttributes() {
      return {
        type: {
          default: "info",
          parseHTML: (el) => {
            const m = /callout-(\w+)/.exec(el.className || "");
            return m ? m[1] : "info";
          },
          renderHTML: () => ({}),        // 종류는 아래 class 로만 표현
        },
      };
    },
    parseHTML() { return [{ tag: "div.callout" }]; },
    renderHTML({ node, HTMLAttributes }) {
      return ["div", Object.assign({}, HTMLAttributes,
        { class: "callout callout-" + (node.attrs.type || "info") }), 0];
    },
    addCommands() {
      const name = this.name;
      return {
        toggleCallout: (type) => ({ editor, commands }) => {
          if (editor.isActive(name, { type })) return commands.lift(name);      // 같은 종류 → 해제
          if (editor.isActive(name)) return commands.updateAttributes(name, { type });  // 종류 변경
          return commands.wrapIn(name, { type });
        },
      };
    },
  });
}

// 이미지 삽입 시 세로가 너무 길지 않도록 기본 높이 상한(px). 원본이 이보다 작으면 원본 유지.
const IMG_MAX_H = 320;

// 이미지의 자연 크기를 재서, 높이가 상한을 넘으면 비율 유지한 width(px)를 돌려준다(아니면 null).
function fitWidth(url) {
  return new Promise((resolve) => {
    const im = new Image();
    im.onload = () => {
      const h = im.naturalHeight || 0, w = im.naturalWidth || 0;
      resolve(h > IMG_MAX_H && w ? Math.round((w * IMG_MAX_H) / h) : null);
    };
    im.onerror = () => resolve(null);
    im.src = url;
  });
}

// 크기 조절 가능한 이미지 — width 속성(→ wiki !파일|width=N!) + 모서리 드래그 핸들 NodeView.
function imageResizeExt(T) {
  return T.Image.extend({
    addAttributes() {
      const parent = this.parent ? this.parent() : {};
      return Object.assign({}, parent, {
        width: {
          default: null,
          parseHTML: (el) => el.getAttribute("width") || null,
          renderHTML: (attrs) => (attrs.width ? { width: attrs.width } : {}),
        },
      });
    },
    addNodeView() {
      return ({ node, editor, getPos }) => {
        const wrap = document.createElement("span");
        wrap.className = "img-wrap";
        const img = document.createElement("img");
        img.src = node.attrs.src;
        if (node.attrs.alt) img.alt = node.attrs.alt;
        if (node.attrs.width) img.setAttribute("width", node.attrs.width);
        wrap.appendChild(img);
        const handle = document.createElement("span");
        handle.className = "img-resize";
        handle.title = "드래그해서 크기 조절";
        wrap.appendChild(handle);
        handle.addEventListener("mousedown", (e) => {
          e.preventDefault(); e.stopPropagation();
          const startX = e.clientX, startW = img.getBoundingClientRect().width;
          const move = (ev) => {
            img.setAttribute("width", String(Math.max(48, Math.round(startW + ev.clientX - startX))));
          };
          const up = () => {
            document.removeEventListener("mousemove", move);
            document.removeEventListener("mouseup", up);
            const w = parseInt(img.getAttribute("width") || "0", 10);
            if (w && typeof getPos === "function") {
              editor.chain().focus().command(({ tr }) => {
                tr.setNodeMarkup(getPos(), undefined, Object.assign({}, node.attrs, { width: w }));
                return true;
              }).run();
            }
          };
          document.addEventListener("mousemove", move);
          document.addEventListener("mouseup", up);
        });
        return {
          dom: wrap,
          ignoreMutation: () => true,
          update: (n) => {
            if (n.type !== node.type) return false;
            img.src = n.attrs.src;
            if (n.attrs.width) img.setAttribute("width", n.attrs.width);
            else img.removeAttribute("width");
            return true;
          },
        };
      };
    },
  });
}

// 웹 링크 뱃지 — **원자적 inline 노드**(atom). 글자 단위로 커서가 들어가지 않고 한 덩어리로 다뤄진다.
// 더블클릭하면 제목/URL 을 고칠 수 있고, URL 이 바뀌면 favicon(--fav)도 즉시 갱신된다.
// 저장은 <a href>제목</a> → wiki [제목|url] (html_to_wiki 가 그대로 처리).
function favCss(href) {
  return /^https?:/i.test(href) ? "url('/api/favicon?u=" + encodeURIComponent(href) + "')" : "";
}

// Jira 티켓 링크 판별 + 티켓 요약 조회(세션 캐시) — 에디터 뱃지를 읽기 렌더와 같게 그리기 위해.
function jiraKeyOf(href) {
  const m = /\/browse\/([A-Za-z][A-Za-z0-9]*-\d+)/.exec(href || "");
  return m ? m[1].toUpperCase() : null;
}
const _tkCache = new Map();
function ticketData(key) {
  if (_tkCache.has(key)) return Promise.resolve(_tkCache.get(key));
  return api.ticketBadge(key)
    .then((b) => { _tkCache.set(key, b); return b; })
    .catch(() => null);
}

// 첨부 파일 뱃지 — 이미지가 아닌 파일을 본문에 **한 덩어리**로 박는다.
// 이미지는 그림 자체가 내용이라 <img> 로 넣지만, 파일은 "무엇이 붙어 있다" 는 사실이 내용이다.
// 그래서 미리보기 대신 이름과 확장자를 보이는 칩으로 그린다. 제출 시 실제 티켓 첨부가 되고
// 본문에는 Jira 첨부 링크([^이름])로 저장된다.
// 영역 구분선 — 본문을 '=== 제목 ===' 으로 나누는 사내 관습. 티켓 뷰는 이미 이 표시를 읽어
// 영역별 카드로 그리는데(sections.py), **에디터에서는 그냥 글자**라 쓰는 사람이 결과를 못 봤다.
//
// 저장 형태는 바꾸지 않는다 — 노드는 '=== 제목 ===' 한 줄짜리 문단으로 직렬화된다. 새 문법을
// 만들면 Jira 웹에서 연 사람이 못 알아보고, 기존 티켓과도 어긋난다. 화면에서만 선처럼 보인다.
// 이미 저장된 본문을 편집기로 열 때: '=== 제목 ===' 줄을 구분선 노드로 바꾼다.
// 안 바꾸면 편집기에선 그냥 글자로 보이고, 사용자가 손대면 형식이 깨진다.
//
// ★ **문단 안에 <br> 로 이어진 경우까지** 처리해야 한다. Jira wiki 는 홑 줄바꿈을 <br> 로 내므로
//   실제 티켓 본문은 `<p>안녕하세요<br/>==== 신청정보 ====<br/>이렇게 신청함</p>` 처럼 한 문단에
//   뭉쳐 들어온다. 예전엔 '구분선만 담은 <p>' 만 봤던 탓에, 화면에선 영역이 갈려 보이는 본문이
//   수정 화면에서는 '==== 제목 ====' 맨 글자로 풀렸다(리포트된 버그).
//   자르는 규칙은 표시 계층(app/content/sections.py)과 같아야 한다 — 한쪽만 고치면 또 어긋난다.
const SEC_ONELINE = /^\s*={3,}\s*(.+?)\s*={3,}\s*$/;
const _P_BLOCK = /<p\b[^>]*>([\s\S]*?)<\/p>/gi;
const _TAGS = /<[^>]*>/g;

function liftSections(html) {
  if (!html || html.indexOf("===") < 0) return html;
  return html.replace(_P_BLOCK, (whole, inner) => {
    if (inner.indexOf("===") < 0) return whole;
    const lines = inner.split(/<br\s*\/?>/i);
    const isSec = (l) => SEC_ONELINE.test(l.replace(_TAGS, "").trim());
    if (!lines.some(isSec)) return whole;                 // 구분선 줄이 없으면 손대지 않는다

    // 구분선 줄에서 문단을 끊고, 그 자리에 노드를 넣는다(앞뒤 줄은 다시 <br> 로 이어 붙인다).
    const out = [];
    let buf = [];
    const flush = () => {
      const body = buf.join("<br>");
      if (body.replace(_TAGS, "").trim()) out.push("<p>" + body + "</p>");
      buf = [];
    };
    for (const line of lines) {
      const plain = line.replace(_TAGS, "").trim();
      const m = SEC_ONELINE.exec(plain);
      if (m) {
        flush();
        // 제목은 **다시 이스케이프하지 않는다** — 여기 들어온 건 이미 정화된 HTML 조각이라
        // '&lt;' 같은 엔티티가 그대로다. 한 번 더 걸면 'a &amp;lt; b' 로 글자가 새어 나온다.
        out.push('<div class="sec-title-node">' + m[1] + "</div>");
      } else {
        buf.push(line);
      }
    }
    flush();
    return out.join("");
  });
}

// 저장/표시된 체크박스(<p><input type=checkbox …>글<br><input …>글</p>)를 편집기로 열 때
// **TipTap TaskList** 로 되살린다. 안 하면 TipTap 이 <input> 노드를 몰라 통째로 버려 — 수정에
// 들어가면 체크박스가 사라진다(실제 리포트된 버그). 저장 때는 다시 <p><input> 로 평탄화된다.
const _CB_IN_P = /<p\b[^>]*>([\s\S]*?)<\/p>/gi;
const _CB_SPLIT = /<input\b([^>]*)>\s*([\s\S]*?)(?=<input\b|$)/gi;
function liftCheckboxes(html) {
  if (!html || !/<input[^>]*type=["']?\s*checkbox/i.test(html)) return html;
  return html.replace(_CB_IN_P, (m, inner) => {
    if (!/<input[^>]*type=["']?\s*checkbox/i.test(inner)) return m;   // 체크박스 없는 문단은 그대로
    const items = [];
    let mm;
    _CB_SPLIT.lastIndex = 0;
    while ((mm = _CB_SPLIT.exec(inner)) !== null) {
      const attrs = mm[1] || "";
      if (!/type=["']?\s*checkbox/i.test(attrs)) continue;
      const checked = /\bchecked\b/i.test(attrs);
      const text = (mm[2] || "").replace(/(?:<br\s*\/?>|\s)+$/i, "");  // 항목 사이 <br>·공백 제거
      // TipTap TaskItem 은 li[data-type=taskItem] + 본문이 <p> 로 감싸져야 파싱한다(안 그러면 항목이
      // 합쳐지거나 버려진다). data-type·<p> 래퍼를 정확히 맞춘다.
      items.push('<li data-checked="' + (checked ? "true" : "false") + '" data-type="taskItem">'
        + '<label><input type="checkbox"' + (checked ? ' checked="checked"' : "") + '><span></span></label>'
        + "<div><p>" + (text || "") + "</p></div></li>");
    }
    return items.length ? '<ul data-type="taskList">' + items.join("") + "</ul>" : m;
  });
}

// AI 자동완성이 낸 HTML 을 **이 편집기가 실제로 파싱하는 모양**으로 맞춘다.
//
// 모델에게는 읽기 쉬운 최소 형태(<li data-checked="false">글</li>)로 내게 하고, 편집기가
// 요구하는 군더더기(data-type·label·<p> 래퍼)는 코드가 채운다 — 프롬프트에 DOM 세부를
// 적어 두면 모델이 한 군데만 틀려도 조용히 깨진다. liftCheckboxes 가 '불러올 때'를 맡듯이
// 이 함수가 '끼워 넣을 때'를 맡는다(그쪽은 <input> 평문, 이쪽은 taskList 형태라 짝이 다르다).
const _AI_TASK_LI = /<li\b([^>]*?)data-checked=["']?(true|false)["']?([^>]*)>([\s\S]*?)<\/li>/gi;
export function normalizeAiHtml(html) {
  let out = String(html || "");
  out = out.replace(_AI_TASK_LI, (m, pre, checked, post, body) => {
    if (/data-type=["']?taskItem/i.test(pre + post)) return m;      // 이미 맞는 모양
    const on = String(checked).toLowerCase() === "true";
    const inner = /<(p|div|ul|ol)\b/i.test(body) ? body : "<p>" + body.trim() + "</p>";
    return '<li data-checked="' + (on ? "true" : "false") + '" data-type="taskItem">'
      + '<label><input type="checkbox"' + (on ? ' checked="checked"' : "") + '><span></span></label>'
      + "<div>" + inner + "</div></li>";
  });
  return out;
}

function sectionExt(T) {
  return T.Node.create({
    name: "sectionTitle",
    group: "block",
    content: "inline*",                 // 제목은 그대로 편집되는 글자다(별도 입력창을 두지 않는다)
    defining: true,
    parseHTML() { return [{ tag: "div.sec-title-node" }]; },
    renderHTML() { return ["div", { class: "sec-title-node" }, 0]; },
    addInputRules() {
      const type = this.type;
      return [
        // '=== 제목 ===' 을 다 치면 그 자리에서 선으로 바뀐다(= 3개 이상 양쪽).
        // ★ textblockTypeInputRule 만 쓰면 **일치한 글자를 통째로 지운다** — 제목까지 사라진다.
        //   직접 넣어 줘야 한다(실제로 빈 구분선이 만들어졌다).
        new T.InputRule({
          find: /^={3,}\s*(.+?)\s*={3,}$/,
          handler: ({ state, range, match, chain }) => {
            chain().deleteRange(range).insertContent({
              type: type.name,
              content: match[1] ? [{ type: "text", text: match[1] }] : [],
            }).run();
          },
        }),
      ];
    },
  });
}

function fileBadgeExt(T) {
  return T.Node.create({
    name: "fileBadge",
    group: "inline",
    inline: true,
    atom: true,                     // 한 덩어리 — 내부로 커서가 들어가지 않는다
    selectable: true,
    addAttributes() {
      return { href: { default: "" }, name: { default: "" }, size: { default: 0 } };
    },
    parseHTML() {
      return [{ tag: "a.file-badge", getAttrs: (el) => ({
        href: el.getAttribute("href") || "",
        name: el.getAttribute("data-file") || (el.textContent || "").trim(),
      }) }];
    },
    // atom 은 기본적으로 getText() 에 안 잡힌다 → 파일만 넣은 댓글이 '내용 없음' 으로 오판된다.
    renderText({ node }) { return node.attrs.name || ""; },
    renderHTML({ node }) {
      return ["a", { href: node.attrs.href || node.attrs.name || "",
                     class: "file-badge", "data-file": node.attrs.name || "",
                     "data-ext": extOf(node.attrs.name), rel: "noopener" },
              node.attrs.name || ""];
    },
    addNodeView() {
      return ({ node }) => {
        const a = document.createElement("a");
        a.className = "file-badge";
        a.setAttribute("data-file", node.attrs.name || "");
        a.setAttribute("href", node.attrs.href || "");
        a.setAttribute("rel", "noopener");
        a.title = node.attrs.name + (node.attrs.size ? "  (" + fmtSize(node.attrs.size) + ")" : "");
        // 아이콘·색은 filebadge.css 한 곳이 data-ext 로 정한다 — 서버가 렌더한 코멘트와 같은
        // 규칙이라야 작성 중 화면과 등록 뒤 화면이 같아 보인다.
        a.setAttribute("data-ext", extOf(node.attrs.name));
        a.innerHTML = '<i class="fb-ext"></i><span class="fb-n"></span>';
        a.querySelector(".fb-n").textContent = node.attrs.name || "";
        // 편집 중에는 링크를 따라가지 않는다 — 아직 올라가지도 않은 파일이다.
        a.addEventListener("click", (e) => e.preventDefault());
        return { dom: a };
      };
    },
  });
}

function fmtSize(n) {
  if (!n) return "";
  if (n < 1024) return n + "B";
  if (n < 1024 * 1024) return Math.round(n / 1024) + "KB";
  return (n / 1024 / 1024).toFixed(1) + "MB";
}

function linkBadgeExt(T) {
  return T.Node.create({
    name: "linkBadge",
    group: "inline",
    inline: true,
    atom: true,                     // 한 덩어리 — 내부로 커서가 들어가지 않는다
    selectable: true,
    addAttributes() {
      return { href: { default: "" }, title: { default: "" } };
    },
    parseHTML() {
      return [{
        tag: "a[href]",
        getAttrs: (el) => ({
          href: el.getAttribute("href") || "",
          title: (el.textContent || "").trim(),
        }),
      }];
    },
    // atom 이라 기본적으로 getText() 에 안 잡힌다 → 뱃지만 있는 댓글이 '내용 없음'으로 오판되던 문제.
    renderText({ node }) { return node.attrs.title || node.attrs.href || ""; },
    renderHTML({ node }) {
      const href = node.attrs.href || "";
      const attrs = { href, class: "web-badge", rel: "noopener" };
      const fav = favCss(href);
      if (fav) attrs.style = "--fav:" + fav;
      return ["a", attrs, node.attrs.title || href];
    },
    addNodeView() {
      return ({ node, editor, getPos }) => {
        let cur = node;
        const a = document.createElement("a");
        const paint = (n) => {
          const href = n.attrs.href || "";
          a.setAttribute("href", href);
          a.setAttribute("rel", "noopener");
          a.title = href + "  (더블클릭: 제목·링크 수정)";
          const key = jiraKeyOf(href);
          if (key) {
            // Jira 티켓 — 읽기 렌더(augmentLinks)와 **같은 구조·클래스**로 그려 모양을 일치시킨다.
            // 원문 Jira URL의 자동 변환은 기존 Short 타입(아이콘+키)을 쓴다. 상세 뱃지는
            // 사용자가 명시적으로 선택한 참조 UI에만 남겨 긴 제목/상태가 문장을 밀지 않게 한다.
            a.className = "jira-badge jira-badge-list tkt";
            a.style.removeProperty("--fav");
            a.innerHTML = '<span class="tbadge v-solid jb-type"></span><b class="jb-key"></b>'
              + '<span class="jb-name"></span><span class="jb-meta"></span>';
            a.querySelector(".jb-key").textContent = key;
            ticketData(key).then((bd) => {
              if (!bd || !a.isConnected) return;
              const tb = a.querySelector(".jb-type"), nm = a.querySelector(".jb-name"),
                    mt = a.querySelector(".jb-meta");
              if (!tb || !nm || !mt) return;
              const cat = bd.statusCategory || "todo";
              tb.textContent = typeLabel(bd.type || "");
              tb.style.setProperty("--tc", TYPE_BG[bd.type] || "var(--ty-task)");
              nm.textContent = bd.summary || "";
              mt.textContent = bd.status || "";            // 담당자는 표시하지 않는다
              mt.className = "jb-meta st-" + cat;
            });
            return;
          }
          a.className = "web-badge";
          a.textContent = n.attrs.title || href;
          const fav = favCss(href);
          if (fav) a.style.setProperty("--fav", fav);
          else a.style.removeProperty("--fav");
        };
        paint(cur);
        a.addEventListener("click", (e) => e.preventDefault());     // 편집 중 이동 방지
        a.addEventListener("dblclick", (e) => {
          e.preventDefault(); e.stopPropagation();
          openBadgeEditor(a, cur.attrs, (title, href) => {
            if (typeof getPos !== "function") return;
            editor.chain().focus().command(({ tr }) => {
              tr.setNodeMarkup(getPos(), undefined, { href, title });   // href 바뀌면 favicon 도 갱신
              return true;
            }).run();
          }, () => {
            // 언링크 — 뱃지를 지우고 **일반 텍스트**로 바꾼다(제목이 있으면 제목, 없으면 URL).
            if (typeof getPos !== "function") return;
            const pos = getPos();
            const text = cur.attrs.title || cur.attrs.href || "";
            editor.chain().focus().command(({ tr, state }) => {
              tr.replaceWith(pos, pos + cur.nodeSize, text ? state.schema.text(text) : []);
              return true;
            }).run();
          });
        });
        return {
          dom: a,
          update: (n) => { if (n.type !== cur.type) return false; cur = n; paint(n); return true; },
          ignoreMutation: () => true,
        };
      };
    },
  });
}

// 뱃지 편집 패널 — 제목과 URL 을 **한 화면에서** 고친다.
// (prompt 를 두 번 연달아 띄우면 브라우저가 '추가 대화상자 차단'으로 두 번째를 막아버린다.)
function openBadgeEditor(anchor, attrs, onSave, onUnlink) {
  document.querySelectorAll(".badge-edit").forEach((e) => e.remove());
  const box = document.createElement("div");
  box.className = "badge-edit";
  box.innerHTML =
    '<label>표시할 제목</label><input class="be-t" type="text">' +
    '<label>링크 URL</label><input class="be-h" type="text">' +
    '<div class="be-row"><button type="button" class="be-unlink" title="뱃지를 없애고 일반 텍스트로 바꿉니다">언링크</button>' +
    '<button type="button" class="be-cancel">취소</button>' +
    '<button type="button" class="be-ok">저장</button></div>';
  document.body.appendChild(box);
  const t = box.querySelector(".be-t"), h = box.querySelector(".be-h");
  t.value = attrs.title || "";
  h.value = attrs.href || "";
  // 화면 안에 들어오게 — 아래로 넘치면 앵커 위로 뒤집는다(에디터가 화면 하단에 있을 때가 많다).
  const r = anchor.getBoundingClientRect();
  const bh = box.offsetHeight || 180;
  let top = r.bottom + 6;
  if (top + bh > window.innerHeight - 8) top = Math.max(8, r.top - bh - 6);
  box.style.left = Math.round(Math.max(8, Math.min(r.left, window.innerWidth - 340))) + "px";
  box.style.top = Math.round(top) + "px";
  const close = () => { box.remove(); document.removeEventListener("mousedown", onDoc, true); };
  const onDoc = (ev) => { if (!box.contains(ev.target)) close(); };
  setTimeout(() => document.addEventListener("mousedown", onDoc, true), 0);
  const save = () => {
    const href = (h.value || "").trim();
    if (!href) { h.focus(); return; }
    onSave((t.value || "").trim() || href, href);
    close();
  };
  box.querySelector(".be-cancel").addEventListener("click", close);
  box.querySelector(".be-ok").addEventListener("click", save);
  const ul = box.querySelector(".be-unlink");
  if (onUnlink) ul.addEventListener("click", () => { onUnlink(); close(); });
  else ul.style.display = "none";
  box.addEventListener("keydown", (ev) => {
    if (ev.key === "Enter") { ev.preventDefault(); save(); }
    else if (ev.key === "Escape") { ev.preventDefault(); close(); }
  });
  t.focus(); t.select();
}

// 뱃지 제목 교체 — href 가 같고 제목이 아직 expect(기본=href) 인 뱃지만 바꾼다(사용자가 고친 건 유지).
function updateBadgeTitle(editor, href, title, expect) {
  if (!editor || editor.isDestroyed) return;
  const want = expect === undefined ? href : expect;
  let at = null;
  editor.state.doc.descendants((node, pos) => {
    if (at !== null) return false;
    if (node.type.name === "linkBadge" && node.attrs.href === href && node.attrs.title === want) at = pos;
  });
  if (at === null) return;
  editor.view.dispatch(editor.state.tr.setNodeMarkup(at, undefined, { href, title }));
}

// 앱 화면 이름 — app-root 의 해시 라우트/nav 라벨과 맞춘다(기본 라우트는 wbs).
const _APP_PAGES = {
  wbs: "WBS Dashboard", vit: "현안 (PMO_VIT)", workload: "인력 워크로드", devtools: "Dev Tools",
};

// 실 Jira 주소 — 모듈 단위로 **한 번만** 조회해 캐시한다. 에디터 인스턴스마다 받으면
// 붙여넣기가 응답보다 빨랐을 때 앱 주소(localhost)가 그대로 저장된다(다른 사람에겐 못 여는 링크).
let _jiraBase = "";
let _jiraBaseReq = null;
function jiraBase() {
  if (!_jiraBaseReq) {
    _jiraBaseReq = api.health()
      .then((h) => { _jiraBase = (h && h.jiraBase) || ""; return _jiraBase; })
      .catch(() => "");
  }
  return _jiraBaseReq;
}

// 이 URL 이 '우리 앱' 주소인가. 로컬 1인 앱이라 같은 앱을 localhost/127.0.0.1 어느 쪽으로도 연다
// → origin 문자열만 비교하면 별칭으로 연 주소를 남의 사이트로 오해한다(포트가 같으면 우리 앱).
const _LOOPBACK = new Set(["localhost", "127.0.0.1", "[::1]", "::1"]);
function isAppOrigin(u) {
  if (u.origin === location.origin) return true;
  return _LOOPBACK.has(u.hostname) && _LOOPBACK.has(location.hostname)
    && (u.port || "") === (location.port || "");
}

// 우리 앱 URL 을 붙여넣은 경우의 정규화. 외부 URL 이면 null.
//  · {앱}/browse/KEY  → **실 Jira 티켓 주소**(그게 정본) + 제목은 티켓 키
//  · 그 외 앱 URL     → 'Lake Task Manager - {화면 이름}' 뱃지 (localhost 링크로 보이지 않게)
function normalizeAppUrl(url, jiraBase) {
  try {
    const u = new URL(url, location.href);
    // /browse/KEY 는 우리 앱 주소든 실 Jira 주소든 **티켓**으로 취급한다.
    const m = /^\/browse\/([A-Za-z][A-Za-z0-9]*-\d+)/.exec(u.pathname);
    if (m) {
      const key = m[1].toUpperCase();
      // 우리 앱 주소면 실 Jira 로 바꾼다(그게 정본). 이미 Jira 주소면 그대로 둔다.
      const base = (isAppOrigin(u) && jiraBase) ? jiraBase.replace(/\/+$/, "") : u.origin;
      return { href: base + "/browse/" + key, title: key, key };
    }
    if (!isAppOrigin(u)) return null;                   // 외부 일반 URL → og:title 조회
    const route = (u.hash || "").replace(/^#\/?/, "").split(/[?/]/)[0] || "wbs";
    const page = _APP_PAGES[route] || route || "";
    return { href: url, title: "Lake Task Manager" + (page ? " - " + page : "") };
  } catch (e) { return null; }
}

// Confluence 문서 URL 의 **슬러그**에서 제목을 뽑는다 — 백엔드(og:title/문서조회) 응답 전에
// 즉시 라벨로 쓰려는 것. 신형 /pages/{id}/{slug}, 구형 /display/{space}/{slug}.
// pathname 만 보므로 #heading 앵커·?쿼리는 자동으로 빠진다(그게 raw url 로 새던 버그의 방지책).
function confTitleFromUrl(u) {
  try {
    const path = new URL(u, location.href).pathname;
    const m = path.match(/\/pages\/\d+\/([^/]+)\/?$/) || path.match(/\/display\/[^/]+\/([^/]+)\/?$/);
    if (m && m[1]) return decodeURIComponent(m[1].replace(/\+/g, " ")).trim();
  } catch (e) { /* noop */ }
  return null;
}

// 티켓 뱃지 라벨 — [타입] [번호] [제목] - [상태]
function ticketLabel(key, bd) {
  const parts = [];
  if (bd && bd.type) parts.push(bd.type);
  parts.push(key);
  if (bd && bd.summary) parts.push(bd.summary);
  let s = parts.join(" ");
  if (bd && bd.status) s += " - " + bd.status;
  return s;
}

// 표/코드블럭이 문서 첫 블록이면 그 '위'로 커서를 보낼 수 없다(문단이 없어서).
// → 첫 블록 시작에서 ArrowUp, 또는 어디서든 Mod+Shift+Enter 로 현재 최상위 블록 **위에 문단**을 만든다.
function firstBlockEscapeExt(T) {
  const insertAbove = (editor) => {
    const $from = editor.state.selection.$from;
    if ($from.depth < 1) return false;
    const top = $from.before(1);                 // 현재 최상위 블록의 시작 위치
    return editor.chain().insertContentAt(top, { type: "paragraph" })
      .setTextSelection(top + 1).focus().run();
  };
  return T.Extension.create({
    name: "firstBlockEscape",
    addKeyboardShortcuts() {
      const ed = () => this.editor;
      return {
        // ★ Tab 이 **에디터를 벗어나던** 버그. 리스트면 한 단계 들여쓰기(bullet level ↑),
        //   표면 셀 이동(Table 확장이 처리 — false 로 넘긴다), 그 외엔 그냥 소비해 포커스를 지킨다.
        Tab: () => {
          const e = ed();
          if (e.isActive("table")) return false;
          // 리스트면 한 단계 들여쓰기(가능할 때 — 앞 형제 밑으로). 그 외/불가면 소비만 해서
          // Tab 으로 에디터 밖으로 포커스가 나가지 않게 한다.
          if (e.isActive("listItem")) return e.chain().focus().sinkListItem("listItem").run() || true;
          return true;
        },
        "Shift-Tab": () => {
          const e = ed();
          if (e.isActive("table")) return false;
          if (e.isActive("listItem")) return e.chain().focus().liftListItem("listItem").run() || true;
          return true;
        },
        "Mod-Shift-Enter": () => insertAbove(this.editor),
        ArrowUp: () => {
          const sel = this.editor.state.selection;
          if (!sel.empty) return false;
          const $from = sel.$from;
          if ($from.depth < 1 || $from.before(1) !== 0) return false;   // 첫 최상위 블록 안이 아님
          if ($from.parentOffset !== 0) return false;                    // 그 줄의 맨 앞이 아님
          const first = this.editor.state.doc.firstChild;
          if (!first || first.type.name === "paragraph") return false;   // 이미 문단이면 기본 동작
          return insertAbove(this.editor);
        },
      };
    },
  });
}

// 헤딩은 무조건 한 줄 — 헤딩 블록 안에 줄바꿈(hardBreak)이 생기면 그 자리에서 줄별 헤딩으로 분리.
// 입력/토글/붙여넣기 등 경로와 무관하게 불변식을 보장(다른 블록의 소프트브레이크는 건드리지 않음).
function singleLineHeadingExt(T) {
  return T.Extension.create({
    name: "singleLineHeading",
    addProseMirrorPlugins() {
      return [new T.Plugin({
        appendTransaction: (trs, oldState, newState) => {
          if (!trs.some((t) => t.docChanged)) return null;
          const hb = newState.schema.nodes.hardBreak;
          if (!hb) return null;
          const breaks = [];
          newState.doc.descendants((node, pos) => {
            if (node.type.name !== "heading") return;
            node.forEach((child, offset) => { if (child.type === hb) breaks.push(pos + 1 + offset); });
          });
          if (!breaks.length) return null;
          const tr = newState.tr;
          breaks.sort((a, b) => b - a).forEach((p) => { tr.delete(p, p + 1); tr.split(p); });
          return tr.steps.length ? tr : null;
        },
      })];
    },
  });
}

// @사람 멘션 자동완성 팝업 (tippy 없이 순수 DOM) — TipTap suggestion.render 핸들러.
// ticketKey: 빈 쿼리 시 이 티켓 관련 사람(리포터/담당/댓글작성/멘션)·모듈 사람을 우선 표시.
// ── '/' 명령 ────────────────────────────────────────────────────────────────
// 툴바에 안 담기는 것들(표·콜아웃·티켓/문서 넣기)까지 **손을 키보드에 둔 채** 쓰게 한다.
// 툴바를 늘리지 않는 이유: 버튼이 스무 개가 되면 자주 쓰는 굵게/목록이 안 보인다.
//
// 이름은 화면에 보이는 우리말이 먼저고, '/'로 치는 영어 낱말은 별칭으로 붙인다 —
// 한글로 치는 사람이 '표'로 찾고, 손에 익은 사람은 '/table' 로 친다.
// 글 스타일(툴바 콤보) — 문단·제목1~3·인용·코드블록. 짧은 표기는 버튼에, 이름은 목록에.
const STYLES = [
  { k: "p", label: "본문", hint: "기본 문단", short: "본문" },
  { k: "h1", level: 1, label: "제목 1", hint: "가장 큰 제목", short: "H1" },
  { k: "h2", level: 2, label: "제목 2", hint: "", short: "H2" },
  { k: "h3", level: 3, label: "제목 3", hint: "", short: "H3" },
  { k: "quote", label: "인용", hint: "❝", short: "인용" },
  { k: "code", label: "코드 블록", hint: "언어 강조", short: "{ }" },
  { k: "clear", label: "모든 스타일 제거", hint: "본문으로", short: "본문" },
];

// 글꼴 — 기본(본문)과 코딩(고정폭). 코딩 폰트는 파일명·명령·값을 붙여 쓸 때 글자폭이 일정해야
// 눈으로 대조된다. css 는 실제 지정할 font-family(sanitizer 가 정렬·글꼴만 통과시킨다).
const FONTS = [
  { k: "default", label: "기본 글꼴", short: "가", css: "" },
  { k: "mono", label: "코딩 글꼴(고정폭)", short: "{ }",
    css: 'ui-monospace, "Cascadia Mono", Consolas, "D2Coding", monospace' },
];

// 글자색·배경색 팔레트 — 너무 많으면 고르기 어렵다. 기본(없음) + 강조 몇 개. 값은 Jira wiki
// {color:#..} 로도, html <span style> 로도 나가므로 hex 로 둔다.
const COLORS = [
  { k: "", label: "기본색" },
  { k: "#dc2626", label: "빨강" }, { k: "#ea580c", label: "주황" }, { k: "#ca8a04", label: "노랑" },
  { k: "#16a34a", label: "초록" }, { k: "#2563eb", label: "파랑" }, { k: "#7c3aed", label: "보라" },
  { k: "#6b7280", label: "회색" },
];
const BGCOLORS = [
  { k: "", label: "없음" },
  { k: "#fef08a", label: "노랑" }, { k: "#bbf7d0", label: "초록" }, { k: "#bfdbfe", label: "파랑" },
  { k: "#fecaca", label: "빨강" }, { k: "#e9d5ff", label: "보라" }, { k: "#e5e7eb", label: "회색" },
];

// 글자색·배경색 — TextStyle(<span style>) 위에 color/background-color 속성을 얹는다.
// 공식 Color/Highlight 확장이 번들에 없어 직접 만든다(콜아웃·구분선과 같은 인라인 확장 방식).
function fontColorExt(T) {
  return T.Extension.create({
    name: "fontColorBg",
    addGlobalAttributes() {
      return [{
        types: ["textStyle"],
        attributes: {
          color: {
            default: null,
            parseHTML: (el) => el.style.color || null,
            renderHTML: (a) => (a.color ? { style: "color: " + a.color } : {}),
          },
          backgroundColor: {
            default: null,
            parseHTML: (el) => el.style.backgroundColor || null,
            renderHTML: (a) => (a.backgroundColor ? { style: "background-color: " + a.backgroundColor } : {}),
          },
        },
      }];
    },
    addCommands() {
      return {
        setFontColor: (color) => ({ chain }) => chain().setMark("textStyle", { color }).run(),
        setFontBg: (backgroundColor) => ({ chain }) => chain().setMark("textStyle", { backgroundColor }).run(),
      };
    },
  });
}

const SLASH = [
  { g: "삽입", id: "code", ic: "{ }", t: "코드 블록", h: "언어 강조", k: "code 코드 codeblock",
    run: (e, r) => e.chain().focus().deleteRange(r).setCodeBlock().run() },
  { g: "삽입", id: "table", ic: "▦", t: "표", h: "행·열 골라 삽입", k: "table 표 테이블",
    run: (e, r, host) => { e.chain().focus().deleteRange(r).run(); host.openTablePicker(); } },
  { g: "삽입", id: "markdown_table", ic: "⊞", t: "마크다운 표", h: "붙여넣어 변환",
    k: "markdown_table md 마크다운 표 붙여넣기 paste",
    run: (e, r, host) => { e.chain().focus().deleteRange(r).run(); host.mdTable = true; } },
  { g: "삽입", id: "checkbox", ic: "☑", t: "체크박스", h: "할 일 목록", k: "checkbox 체크박스 할일 task todo",
    run: (e, r) => e.chain().focus().deleteRange(r).toggleTaskList().run() },
  { g: "삽입", id: "quote", ic: "❝", t: "인용", h: "", k: "quote 인용",
    run: (e, r) => e.chain().focus().deleteRange(r).toggleBlockquote().run() },
  // 구분선은 **본문에서만** 뜬다(sections). 댓글은 티켓 뷰가 영역으로 쪼개 주지 않아,
  // 넣어 봐야 등록 뒤엔 '=== 제목 ===' 글자로 남는다.
  { g: "삽입", id: "divider", ic: "⌗", t: "영역 구분선", h: "=== 제목 ===", k: "divider 구분선 영역 섹션",
    only: "sections",
    run: (e, r) => e.chain().focus().deleteRange(r).insertContent({ type: "sectionTitle" }).run() },

  { g: "알림", id: "info", ic: "ℹ", t: "정보", h: "{info}", k: "info 정보",
    run: (e, r) => e.chain().focus().deleteRange(r).toggleCallout("info").run() },
  { g: "알림", id: "note", ic: "📌", t: "노트", h: "{note}", k: "note 노트",
    run: (e, r) => e.chain().focus().deleteRange(r).toggleCallout("note").run() },
  { g: "알림", id: "tip", ic: "💡", t: "팁", h: "{tip}", k: "tip 팁",
    run: (e, r) => e.chain().focus().deleteRange(r).toggleCallout("tip").run() },
  { g: "알림", id: "success", ic: "✔", t: "성공", h: "{success}", k: "success 성공 완료",
    run: (e, r) => e.chain().focus().deleteRange(r).toggleCallout("success").run() },
  { g: "알림", id: "warning", ic: "⚠", t: "주의", h: "{warning}", k: "warning 주의 경고",
    run: (e, r) => e.chain().focus().deleteRange(r).toggleCallout("warning").run() },
  { g: "알림", id: "error", ic: "✖", t: "오류", h: "{error}", k: "error 오류 에러 실패",
    run: (e, r) => e.chain().focus().deleteRange(r).toggleCallout("error").run() },

  { g: "붙이기", id: "jira", ic: "🎫", t: "Jira 티켓", h: "검색해서 링크", k: "jira 티켓 이슈",
    run: (e, r, host) => { e.chain().focus().deleteRange(r).run(); host.openPick("jira"); } },
  { g: "붙이기", id: "confluence", ic: "📄", t: "Confluence 문서", h: "검색해서 링크",
    k: "confluence 문서 위키 컨플루언스",
    run: (e, r, host) => { e.chain().focus().deleteRange(r).run(); host.openPick("confluence"); } },
  { g: "붙이기", id: "file", ic: "📎", t: "파일 · 이미지", h: "등록할 때 함께 올라갑니다",
    k: "file image 파일 이미지 첨부 업로드",
    run: (e, r, host) => { e.chain().focus().deleteRange(r).run(); host.tbImage(); } },
];

function slashSuggestion(host) {
  return {
    char: "/",
    // 낱말 중간의 '/'(경로·URL)에는 뜨지 않게 — 앞이 줄머리이거나 공백일 때만.
    allowSpaces: false,
    startOfLine: false,
    command: ({ editor, range, props }) => props.run(editor, range, host),
    items: ({ query }) => {
      const q = (query || "").trim().toLowerCase();
      return SLASH.filter((it) => {
        if (it.only === "sections" && !host.sections) return false;
        if (!q) return true;
        return it.id.startsWith(q) || it.t.toLowerCase().includes(q) || it.k.includes(q);
      });
    },
    render: createManagedSuggestionRenderer({
      className: "mention-popup slash-popup",
      emptyLabel: "해당하는 명령이 없습니다",
      loadingLabel: "명령을 찾는 중…",
      itemSelector: ".sl-item",
      selectedSelector: ".sl-item.sel",
      selectOnTab: true,
      select: (item, command) => command(item),
      renderItems(items, selected) {
        let html = "", group = "";
        items.forEach((item, index) => {
          if (item.g !== group) { group = item.g; html += `<div class="sl-g">${esc(group)}</div>`; }
          html += `<div class="sl-item${index === selected ? " sel" : ""}" data-suggestion-index="${index}">`
                + `<span class="sl-ic">${esc(item.ic)}</span>`
                + `<span class="sl-t">${esc(item.t)}</span>`
                + (item.h ? `<span class="sl-h">${esc(item.h)}</span>` : "")
                + `<span class="sl-k">/${esc(item.id)}</span></div>`;
        });
        return html;
      },
    }),
  };
}

function slashExt(T, host) {
  return T.Extension.create({
    name: "slashCmd",
    addProseMirrorPlugins() {
      return [T.Suggestion(Object.assign({ editor: this.editor }, slashSuggestion(host)))];
    },
  });
}

function mentionSuggestion(ticketKey, localUsers) {
  const fetchUsers = createManagedMentionItems(ticketKey, localUsers);
  return {
    char: "@",
    items: fetchUsers,
    initialItems: mentionInitialUsers(localUsers),
    debounce: typeaheadDelay(),
    render: createManagedSuggestionRenderer({
      className: "mention-popup",
      emptyLabel: "사용자 없음",
      loadingLabel: "사용자 검색 중…",
      hideItemsWhileLoading: true,
      showLoadingWithItems: true,
      itemSelector: ".mn-item",
      selectedSelector: ".mn-item.sel",
      select(user, command) {
        rememberUser(user);
        command({ id: user.id, label: user.name });
      },
      renderItems: (items, selected) => items.map((user, index) =>
        `<div class="mn-item${index === selected ? " sel" : ""}" data-suggestion-index="${index}">`
        + mnAvatar(user.name, user.id)
        + `<span class="mn-nm">${esc(user.display || user.name)}</span>`
        + `<span class="mn-id">${esc(user.id)}</span></div>`).join(""),
      afterPaint(element) {
        // 사진이 실패해도 이니셜 원은 그대로 남는다.
        element.querySelectorAll(".mn-av-img").forEach((img) => {
          img.addEventListener("load", () => img.classList.add("on"));
          img.addEventListener("error", () => img.remove());
        });
      },
    }),
  };
}

/** TipTap mention의 저장 HTML은 그대로 두고 편집 중 DOM만 공통 badge로 그린다.
 *  노드뷰를 쓰지 않고 DOM을 보강하면 ProseMirror가 낯선 avatar를 즉시 지워 버린다. */
function mentionExt(T, ticketKey, localUsers) {
  return T.Mention.extend({
    addNodeView() {
      return ({ node, HTMLAttributes }) => {
        let current = node;
        const dom = document.createElement("span");
        Object.entries(HTMLAttributes || {}).forEach(([key, value]) => {
          if (value != null) dom.setAttribute(key, value);
        });
        dom.setAttribute("contenteditable", "false");
        const paint = (next) => {
          const uid = next.attrs.id || "";
          const label = next.attrs.label || uid;
          paintMentionBadge(dom, uid, label);
          dom.setAttribute("data-type", "mention");
        };
        paint(current);
        return {
          dom,
          update(next) {
            if (next.type !== current.type) return false;
            const changed = next.attrs.id !== current.attrs.id || next.attrs.label !== current.attrs.label;
            current = next;
            if (changed) paint(next);
            return true;
          },
          ignoreMutation: () => true,
        };
      };
    },
  }).configure({ HTMLAttributes: { class: "mention" }, suggestion: mentionSuggestion(ticketKey, localUsers) });
}

// 끌어서 정한 높이는 **기억한다**. 매번 다시 늘리게 하면 늘리는 의미가 없다 —
// 긴 글을 쓰는 사람은 늘 길게 쓴다. 화면(px)이라 localStorage 로 충분하다.
const H_KEY = "cmtEditorH";
const H_MIN = 120;
const H_MAX = 720;

function validEditorHeight(value) {
  const v = Number(value);
  return Number.isFinite(v) && v >= H_MIN && v <= H_MAX ? Math.round(v) : null;
}

function loadEditorHeight(key, fallback) {
  try {
    const saved = validEditorHeight(parseInt(localStorage.getItem(key) || "", 10));
    return saved === null ? validEditorHeight(fallback) : saved;
  } catch (e) { return validEditorHeight(fallback); }
}
function saveEditorHeight(key, value) {
  try { localStorage.setItem(key, String(value)); } catch (e) { /* 사파리 프라이빗 등 */ }
}

export default {
  name: "CommentEditor",
  components: { LinkPicker, MarkdownTableDialog, AgentSettingsDialog },
  props: {
    ticketKey: { type: String, required: true },
    initial: { type: String, default: "" },            // 수정 시 기존 HTML
    submitLabel: { type: String, default: "등록" },
    submitFn: { type: Function, required: true },       // async (html) => any (실패 시 throw)
    // 이 에디터가 더 큰 화면의 **한 필드**로 들어갈 때(예: 상태 전이 화면)는 자기 버튼 줄을
    // 감춘다. 화면에 제출 버튼이 두 개면 무엇이 무엇을 하는지 알 수 없다.
    // 바깥에서 ref 로 submit() 을 부르면 이미지 업로드·초안 정리까지 그대로 탄다 —
    // 그 로직을 밖에서 다시 짜면 반드시 어긋난다.
    hideFooter: { type: Boolean, default: false },
    // 영역 구분선(=== 제목 ===)을 쓸 수 있는가. **본문에서만 켠다.**
    // 티켓 뷰가 영역으로 쪼개 보여 주는 건 설명뿐이라, 댓글에서 구분선을 허용하면 편집기에선
    // 선으로 보이다가 등록하면 그냥 '=== 제목 ===' 글자로 남는다 — 없는 구조를 약속하는 셈이다.
    sections: { type: Boolean, default: false },
    // 자리표시자 문구 — 댓글이 아닌 곳(에이전트 채팅)에 임베드될 때 바꾼다. 빈 값 = 기본.
    placeholder: { type: String, default: "" },
    // 이 에디터가 무엇을 쓰는 중인가 — **초안 저장소를 가르는 열쇠**다.
    // 예전엔 "내용이 비었으면 새 댓글" 로 봤는데, 설명이 빈 티켓의 본문 편집기가 같은 조건에
    // 걸려 **새 댓글 초안을 본문에 불러왔다**. 목적이 다르면 칸도 달라야 한다.
    kind: { type: String, default: "comment" },   // comment | description | transition
    // 티켓 다이어로그가 이미 가진 담당/보고/최근 댓글 맥락. 네트워크 지연 중에도 이들이 먼저 뜬다.
    mentionUsers: { type: Array, default: () => [] },
    // 설명 편집에서 크게 늘린 값이 새 댓글 작성창까지 화면을 채우지 않도록 자리별 저장 키와
    // 기본 높이를 받을 수 있다. 사용자가 조절한 높이는 각 자리에서 계속 기억한다.
    heightKey: { type: String, default: H_KEY },
    initialHeight: { type: Number, default: 0 },
  },
  emits: ["submitted", "cancel"],
  data() { return { ready: false, loadErr: "", busy: false, err: "", tick: 0, languages: [],
                    maximized: false, restored: false,
                    // 인라인 모드에서 사용자가 끌어 정한 본문 높이(px). null = 기본값.
                    // 최대화 모드에는 안 쓴다 — 거기선 창이 높이를 정한다.
                    hostH: loadEditorHeight(this.heightKey, this.initialHeight), resizing: false,
                    // 업로드 진행 — 몇 개 중 몇 번째, 지금 무엇을 올리는 중인가
                    upTotal: 0, upDone: 0, upName: "", upSize: "", upStart: 0, tickNow: 0,
                    // 파일을 이 에디터 위로 끌고 왔는가 — 테두리로 "여기에 놓으면 본문" 을 말한다
                    dragOver: false, dragDepth: 0,
                    // '' | 'jira' | 'confluence' — '/' 로 연 검색창
                    pick: "",
                    // AI 자동완성 — 팝업 상태. seed 는 "쓰던 글을 재료로 쓸까"다
                    aiOpen: false, aiPrompt: "", aiSeed: true, aiReplace: false, aiAsk: "",
                    aiPopStyle: {},   // fixed 배치 — 에디터 기준 중앙(absolute 는 overflow 에 잘리고 좌로 쏠렸다)
                    // LLM 연결값이 없으면 생성 대신 안내+[설정] — null=아직 확인 전
                    aiReady: null, aiWhy: "", aiSettings: false,
                    aiBusy: false, aiErr: "", aiNote: "",
                    mdTable: false, styleOpen: false, fontOpen: false,
                    colorOpen: false, bgOpen: false,   // 글자색·배경색 팔레트 열림
                    // 표 크기 선택 격자 — { r, c } 는 지금 손이 올라간 칸(미리보기)
                    tablePick: false, tpR: 0, tpC: 0 }; },
  async mounted() {
    this._pending = new Map();        // objectURL -> { blob, name }
    this._seq = 0;
    jiraBase();                       // 앱 URL(/browse/KEY)→실 Jira 주소 변환용. 미리 받아 둔다.
    let T;
    try { T = await loadTiptap(); this._T = T; }
    catch (e) { this.loadErr = "에디터를 불러오지 못했습니다(네트워크/CDN 차단). 잠시 후 다시 시도."; return; }
    if (this._dead) return;
    ensureHljsTheme(document.documentElement.getAttribute("data-theme") === "dark");   // 구문강조 색 CSS
    this.languages = T.languages || [];
    const self = this;
    // 붙여넣기/드롭 이미지 → objectURL 삽입 + 추적(제출 시 업로드)
    const handleFiles = (files) => self.insertFiles(files);
    this._ed = new T.Editor({
      element: this.$refs.ed,
      extensions: [
        // v3 StarterKit의 Link는 모든 <a>를 mark로 먼저 소비한다. 이 앱은 Jira/문서 링크를
        // linkBadge atom으로 편집하므로 끄고, 코드블럭도 아래 Lowlight 구현으로 교체한다.
        T.StarterKit.configure({ codeBlock: false, link: false }),
        // 코드블럭 — 원래 Jira 와 같은 태그(<pre class="jecodeblock"><code class="language-X">) + lowlight 강조
        codeLineNumbers(T).configure({ lowlight: T.lowlight, HTMLAttributes: { class: "jecodeblock" } }),
        calloutExt(T),
        ...(this.sections ? [sectionExt(T)] : []),
        slashExt(T, this),
        fileBadgeExt(T),
        singleLineHeadingExt(T),
        firstBlockEscapeExt(T),
        mentionExt(T, this.ticketKey, this.mentionUsers),
        T.Table.configure({ resizable: true }), T.TableRow, T.TableHeader, T.TableCell,
        // 정렬 — 문단·제목·표 셀에. 표 셀을 포함해야 마크다운 표의 :-: / --: 정렬이 붙는다.
        T.TextAlign.configure({ types: ["heading", "paragraph", "tableCell", "tableHeader"] }),
        // 글꼴 — TextStyle(인라인 style) 위에서 FontFamily·글자색/배경색이 동작한다.
        T.TextStyle, T.FontFamily, fontColorExt(T),
        // 체크박스(태스크 리스트) — nested 허용(할 일 안의 할 일)
        T.TaskList, T.TaskItem.configure({ nested: true }),
        // inline:true — 이미지가 같은 줄에 글자와 나란히 놓이게(TipTap 기본은 블록이라 줄이 갈린다)
        imageResizeExt(T).configure({ inline: true }), linkBadgeExt(T),
        T.Placeholder.configure({ placeholder: this.placeholder
          || "댓글을 입력하세요. '/' 로 표·코드·티켓 넣기, @ 로 멘션, 마크다운(#, -, ``` )" }),
      ],
      content: liftCheckboxes(this.sections ? liftSections(this.initial) : this.initial) || "",
      autofocus: true,
      editorProps: {
        // 본문에 tkt-desc 를 부여 → 렌더된 댓글과 **같은 CSS**를 그대로 사용(인용·콜아웃 등 일치).
        // 에디터 전용 규칙(.cmt-ed-host .ProseMirror …)이 더 구체적이라 필요한 곳만 덮어쓴다.
        attributes: { class: "tkt-desc" },
        handlePaste: (view, event) => {
          const cd = event.clipboardData;
          const files = cd && cd.files;
          if (files && files.length && handleFiles(files, view)) { event.preventDefault(); return true; }
          // 순수 URL 붙여넣기 → 자동 링크(문서/웹). 읽기 렌더에서 앱이 Jira/Confluence 뱃지화.
          const txt = cd && cd.getData && cd.getData("text/plain");
          if (txt && _URL_RE.test(txt.trim()) && self._ed.state.selection.empty) {
            const url = txt.trim();
            const norm = normalizeAppUrl(url, _jiraBase);          // 우리 앱 URL 이면 정규화
            const href = norm ? norm.href : url;
            // 외부 URL 이면 Confluence 슬러그 제목을 **즉시** 라벨로(백엔드 지연/실패·앵커와 무관하게
            // raw url 이 박히지 않게). 백엔드 조회가 성공하면 더 정확한 제목으로 덮어쓴다.
            const slug = norm ? null : confTitleFromUrl(url);
            const title0 = norm ? norm.title : (slug || url);
            self._ed.chain().focus().insertContent([
              { type: "linkBadge", attrs: { href, title: title0 } },
              { type: "text", text: " " },
            ]).run();
            if (norm && norm.key) {
              // Jira 티켓 — 키에 요약을 붙여 읽기 쉽게(렌더에서는 앱이 리치 티켓 뱃지로 바꾼다)
              api.ticketBadge(norm.key).then((bd) => {
                if (bd) updateBadgeTitle(self._ed, href, ticketLabel(norm.key, bd), norm.key);
              }).catch(() => { /* noop */ });
            } else if (!norm) {
              // 외부 URL — 라벨을 페이지 제목(문서 실제 title/og:title)으로. 실패하면 슬러그(또는 URL) 유지.
              api.linkTitle(url).then((r) => {
                if (r && r.title) updateBadgeTitle(self._ed, url, r.title, title0);
              }).catch(() => { /* noop */ });
            }
            event.preventDefault(); return true;
          }
          // ── 채팅 입력창은 **서식 없이** 받는다 ─────────────────────────
          // 웹·문서에서 복사한 글에는 인라인 배경색이 딸려오는데, 우리 textStyle 확장이
          // 그것을 흡수해 "드래그한 것 같은 하이라이팅"이 남는다(사용자 지적). 채팅에는
          // 서식 도구가 없어 지울 방법도 없다 — 애초에 글자만 받는다.
          if (self.kind === "agentchat" && txt) {
            const lines = txt.replace(/\r/g, "").split("\n");
            self._ed.chain().focus().insertContent(
              lines.map((ln, i) => (i ? [{ type: "hardBreak" }] : []).concat(
                ln ? [{ type: "text", text: ln }] : [])).flat()).run();
            event.preventDefault(); return true;
          }
          return false;
        },
        handleDrop: (view, event) => {
          const files = event.dataTransfer && event.dataTransfer.files;
          if (files && files.length && handleFiles(files, view)) { event.preventDefault(); return true; }
          return false;
        },
      },
      onSelectionUpdate: () => { self.tick++; },
      onTransaction: () => { self.tick++; },
      onUpdate: () => { self.tick++; self.saveDraftSoon(); },   // 작성 중 임시저장(디바운스)
    });
    if (this._dead) { try { this._ed.destroy(); } catch (e) { /* noop */ } return; }
    await this.restoreDraft();                                   // 이전에 쓰다 만 내용 복원
    this.ready = true;
  },
  beforeUnmount() {
    this._dead = true;
    clearTimeout(this._dt); this._dt = null;       // 제출/이동 뒤 예약 저장이 옛 초안을 되살리지 않게
    if (this._upTick) { clearInterval(this._upTick); this._upTick = null; }   // 업로드 경과 타이머
    try { for (const u of this._pending.keys()) URL.revokeObjectURL(u); } catch (e) { /* noop */ }
    try { if (this._ed) this._ed.destroy(); } catch (e) { /* noop */ }
  },
  computed: {
    STYLES: () => STYLES,
    FONTS: () => FONTS,
    COLORS: () => COLORS,
    BGCOLORS: () => BGCOLORS,
    curFont() {
      this.tick;
      const e = this._ed;
      if (e && e.isActive("textStyle", { fontFamily: FONTS[1].css })) return FONTS[1];
      return FONTS[0];
    },
    curStyle() {
      this.tick;                                   // 커서 이동/편집마다 다시 계산
      const e = this._ed;
      if (!e) return STYLES[0];
      for (const o of STYLES) {
        if (o.k === "p") continue;
        if (o.k === "code" && e.isActive("codeBlock")) return o;
        if (o.k === "quote" && e.isActive("blockquote")) return o;
        if (o.level && e.isActive("heading", { level: o.level })) return o;
      }
      return STYLES[0];
    },
    /** 올리는 동안 **무엇을 기다리는지** 말한다. prod 는 첨부 하나에 몇 초씩 걸려서,
     *  '저장 중…' 만 떠 있으면 멈춘 것처럼 느껴진다 — 몇 개 중 몇 번째인지가 그 차이를 만든다. */
    busyLabel() {
      if (!this.upTotal) return "저장 중…";
      const n = Math.min(this.upDone + 1, this.upTotal);
      // 큰 파일은 분 단위로 걸린다 — 크기와 **경과 시간**을 같이 보여 줘야 '멈춘 것' 으로
      // 오해하고 다시 누르지 않는다(리포트된 문제: 12MB 업로드 중 반복 제출).
      const el = this.upStart ? Math.round((this.tickNow - this.upStart) / 1000) : 0;
      return "첨부 " + n + "/" + this.upTotal
        + (this.upName ? " · " + this.upName : "")
        + (this.upSize ? " (" + this.upSize + ")" : "")
        + (el > 3 ? " · " + el + "초" : "");
    },
  },
  methods: {
    active(name, attrs) { this.tick; return this._ed && this._ed.isActive(name, attrs); },
    cmd(fn) { if (this._ed) { fn(this._ed.chain().focus()); this._ed.commands.focus(); } },
    /** 글자색 — 빈 값이면 해제(color 속성 제거). */
    setFontColor(c) {
      this.colorOpen = false;
      this.cmd((ch) => ch.setMark("textStyle", { color: c || null }).run());
    },
    /** 배경색(형광펜) — 빈 값이면 해제. */
    setFontBg(c) {
      this.bgOpen = false;
      this.cmd((ch) => ch.setMark("textStyle", { backgroundColor: c || null }).run());
    },
    tbBold() { this.cmd((c) => c.toggleBold().run()); },
    tbItalic() { this.cmd((c) => c.toggleItalic().run()); },
    tbStrike() { this.cmd((c) => c.toggleStrike().run()); },
    tbCode() { this.cmd((c) => c.toggleCode().run()); },
    tbH(l) { this.cmd((c) => c.toggleHeading({ level: l }).run()); },
    /** 지금 커서가 놓인 블록 스타일(콤보 표시용). */
    setStyle(o) {
      this.styleOpen = false;
      this.cmd((c) => {
        if (o.k === "clear") {
          // 선택 범위(없으면 그 블록)의 **모든** 서식을 벗긴다: 인라인 마크(굵게·기울임·코드·
          // 글꼴·정렬 등) 제거 + 블록을 기본 문단으로. 붙여넣은 리치 텍스트를 본문으로 되돌릴 때.
          return c.unsetAllMarks().clearNodes().setParagraph()
                  .unsetFontFamily().setTextAlign("left").run();
        }
        if (o.k === "p") return c.setParagraph().run();
        if (o.k === "code") return c.toggleCodeBlock().run();
        if (o.k === "quote") return c.toggleBlockquote().run();
        return c.toggleHeading({ level: o.level }).run();
      });
    },
    tbBullet() { this.cmd((c) => c.toggleBulletList().run()); },
    tbTask() { this.cmd((c) => c.toggleTaskList().run()); },
    tbOrdered() { this.cmd((c) => c.toggleOrderedList().run()); },
    tbQuote() { this.cmd((c) => c.toggleBlockquote().run()); },
    tbCodeBlock() { this.cmd((c) => c.toggleCodeBlock().run()); },
    // 표 크기 선택 격자 — 8×8 칸. i(1~64)를 행/열로 환산.
    openTablePicker() { this.tablePick = true; this.tpR = 0; this.tpC = 0; },
    tpRowOf(i) { return Math.floor((i - 1) / 8) + 1; },
    tpColOf(i) { return ((i - 1) % 8) + 1; },
    insertTableSize(r, c) {
      this.tablePick = false;
      this.cmd((cmd) => cmd.insertTable({ rows: r, cols: c, withHeaderRow: true }).run());
    },
    isAlign(a) { this.tick; return !!(this._ed && this._ed.isActive({ textAlign: a })); },
    tbAlign(a) {
      // 이미 그 정렬이면 해제(기본=왼쪽)로 되돌린다 — 토글이 자연스럽다.
      const cur = this._ed && this._ed.isActive({ textAlign: a });
      this.cmd((c) => c.setTextAlign(cur ? "left" : a).run());
    },
    setFont(f) {
      this.fontOpen = false;
      this.cmd((c) => (f.css ? c.setFontFamily(f.css).run() : c.unsetFontFamily().run()));
    },
    /** 파싱된 마크다운 표({header, rows, align})를 **진짜 표 노드**로 넣는다.
     *  빈 표를 만든 뒤 셀을 채우는 것보다, HTML 로 한 번에 파싱해 넣는 게 정렬·헤더까지 정확하다. */
    insertMdTable(t) {
      this.mdTable = false;
      if (!t || !t.header || !this._ed) return;
      const esc = (x) => String(x == null ? "" : x)
        .replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;");
      const th = t.header.map((h, i) =>
        '<th' + (t.align[i] ? ' style="text-align:' + t.align[i] + '"' : "") + ">" + esc(h) + "</th>").join("");
      const body = t.rows.map((row) =>
        "<tr>" + t.header.map((_, i) =>
          '<td' + (t.align[i] ? ' style="text-align:' + t.align[i] + '"' : "") + ">"
          + esc(row[i] || "") + "</td>").join("") + "</tr>").join("");
      const html = "<table><thead><tr>" + th + "</tr></thead><tbody>" + body + "</tbody></table><p></p>";
      this._ed.chain().focus().insertContent(html).run();
    },
    // 링크 = 원자적 뱃지 노드. 선택 텍스트가 있으면 그 텍스트가 제목이 된다.
    tbLink() {
      const ed = this._ed;
      if (!ed) return;
      const sel = ed.state.selection;
      const host = this.$refs.ed;
      if (ed.isActive("linkBadge")) {                       // 이미 뱃지 선택 → 제목·URL 수정
        const at = sel.from;
        const node = ed.state.doc.nodeAt(at);
        const dom = (host && host.querySelector("a.web-badge.ProseMirror-selectednode")) || host;
        openBadgeEditor(dom, (node && node.attrs) || {}, (title, href) => {
          ed.chain().focus().command(({ tr }) => {
            tr.setNodeMarkup(at, undefined, { href, title });
            return true;
          }).run();
        });
        return;
      }
      const selText = ed.state.doc.textBetween(sel.from, sel.to, " ").trim();
      openBadgeEditor(host, { title: selText, href: "" }, (title, href) => {
        ed.chain().focus().insertContentAt({ from: sel.from, to: sel.to },
          { type: "linkBadge", attrs: { href, title: title || href } }).run();
      });
    },
    tbImage() { this.$refs.file && this.$refs.file.click(); },
    /** '/jira' · '/confluence' — 검색창을 띄운다. 넣는 것은 아래 onPick 이 한다. */
    openPick(kind) { this.pick = kind; },
    /** 고른 티켓/문서를 **링크 뱃지**로 넣는다 — 붙여넣은 URL 과 같은 모양이어야
     *  읽는 쪽에서 무엇이 뭔지 갈리지 않는다(저장도 같은 [제목|주소]). */
    async onPick(it) {
      this.pick = "";
      let href = (it && it.url) || "";
      // 최근 티켓은 URL host가 달라져도 한 항목으로 유지하려고 /browse/KEY로 저장한다.
      // 에디터에 넣을 때는 공유 가능한 실 Jira 주소로 복원한다. 검색 결과가 URL을 누락한
      // 경우에도 key가 있으면 같은 경로로 복구해, 선택 성공 뒤 '주소 없음'이 되지 않게 한다.
      const key = (it && it.key) || "";
      if (key && (!href || /^\/browse\//i.test(href))) {
        const base = (await jiraBase()) || location.origin;
        href = base.replace(/\/+$/, "") + "/browse/" + key.toUpperCase();
      }
      if (!href) {
        // 예전엔 조용히 return 했다 — 사용자에겐 "골랐는데 아무 일도 안 일어남" 으로만 보이고,
        // 우리도 무엇이 없었는지 알 수 없었다. 주소가 없으면 그 사실을 말한다.
        pushToast({ kind: "error", title: "링크를 넣지 못했습니다",
                    message: "고른 항목에 주소가 없습니다. 다시 검색해 주세요.", timeout: 7000 });
        return;
      }
      const title = key ? (key + " " + (it.title || "")).trim() : (it.title || href);
      this._ed.chain().focus()
        .insertContent([{ type: "linkBadge", attrs: { href, title } }, { type: "text", text: " " }])
        .run();
    },
    inCallout(t) { this.tick; return !!(this._ed && this._ed.isActive("callout", { type: t })); },
    tbCallout(t) { this.cmd((c) => c.toggleCallout(t).run()); },

    // ── AI 자동완성 ──────────────────────────────────────────────
    // 쓰기가 아니다 — 결과를 에디터에 꽂아 줄 뿐이고 저장은 사용자가 누른다.
    openAi() {
      this.aiOpen = true; this.aiErr = ""; this.aiNote = ""; this.aiAsk = "";
      // ── 배치: fixed + 에디터 기준 X축 중앙 (실측 지적 3: absolute(right:0)는
      //    ① 버튼이 왼쪽이라 팝업이 좌로 쏠리고 ② overflow/스택 컨텍스트에 가려지고
      //    ③ 320px 로 좁았다). fixed 는 어느 조상에도 잘리지 않는다.
      const host = (this.$el && (this.$el.querySelector(".cmt-ed-host") || this.$el));
      const btn = this.$refs.aiBtn;
      if (host && host.getBoundingClientRect) {
        const r = host.getBoundingClientRect();
        const b = btn && btn.getBoundingClientRect ? btn.getBoundingClientRect() : r;
        this.aiPopStyle = {
          position: "fixed",
          left: (r.left + r.width / 2) + "px",
          top: Math.min(b.bottom + 6, window.innerHeight - 260) + "px",
          transform: "translateX(-50%)",
          width: Math.max(360, Math.min(560, r.width - 24)) + "px",
          right: "auto",
        };
      }
      // 열 때마다 확인한다 — 설정 창에서 키를 막 넣고 돌아온 직후에도 맞아야 한다.
      agentApi.status()
        .then((s) => { this.aiReady = !!(s && s.llmReady); this.aiWhy = (s && s.llmReason) || ""; })
        .catch(() => { this.aiReady = false; this.aiWhy = "에이전트 상태를 확인하지 못했습니다."; });
      this.$nextTick(() => this.$refs.aiInput && this.$refs.aiInput.focus());
    },
    async runAi() {
      const prompt = (this.aiPrompt || "").trim();
      const seed = this.aiSeed ? this.htmlNow() : "";
      if (!prompt && !seed) { this.aiErr = "무엇을 써 드릴지 적어 주세요."; return; }
      this.aiBusy = true; this.aiErr = ""; this.aiNote = "";
      // 생성이 도는 동안 ① 에디터를 잠가 사용자가 쓴 글과 섞이지 않게 하고
      // ② 창이 닫혀 결과가 통째로 사라지지 않게 막는다. **푸는 것은 finally 가 맡는다** —
      // 실패·예외 어느 쪽으로 끝나도 잠긴 채로 남으면 앱을 새로고침해야 한다.
      const unlock = beginBusy("AI가 작성 중");
      try { if (this._ed) this._ed.setEditable(false); } catch (_) { /* noop */ }
      try {
        const r = await agentApi.compose({
          ticketKey: this.ticketKey || "", kind: this.kind || "comment",
          prompt, seedHtml: seed,
        });
        if (!r || !r.ok) {
          // 모호 신호(needsInfo)는 오류가 아니라 **보완 요청**이다 — 팝업을 유지한 채
          // 무엇을 더 적으면 되는지 보여 준다(피드백 루프, 사용자 요청).
          if (r && r.needsInfo) { this.aiErr = ""; this.aiAsk = r.error || ""; }
          else { this.aiAsk = ""; this.aiErr = (r && r.error) || "생성에 실패했습니다."; }
          return;
        }
        // 한 트랜잭션으로 넣는다 — Ctrl+Z 한 번에 통째로 되돌아가야 사용자가 부담 없이 쓴다.
        const ed = this._ed;
        if (!ed) return;
        const html = normalizeAiHtml(r.html);
        if (this.aiReplace) ed.chain().focus().clearContent().insertContent(html).run();
        else ed.chain().focus().insertContent(html).run();
        this.aiNote = r.note || "";
        this.aiOpen = false; this.aiPrompt = "";
        if (r.note) pushToast(r.note, "warn");
      } catch (e) {
        this.aiErr = String((e && e.message) || e || "생성에 실패했습니다.");
      } finally {
        this.aiBusy = false;
        try { if (this._ed) this._ed.setEditable(true); } catch (_) { /* noop */ }
        unlock();
        this.$nextTick(() => { try { this._ed && this._ed.commands.focus(); } catch (_) { /* noop */ } });
      }
    },
    htmlNow() {
      try { return this._ed ? this._ed.getHTML() : ""; } catch (e) { return ""; }
    },
    hasBody() {
      const h = this.htmlNow();
      return !!h && h.replace(/<[^>]*>/g, "").trim().length > 0;
    },
    toggleMax() { this.maximized = !this.maximized; },

    // 드래그 안내. 본문 위 드롭은 ProseMirror handleDrop 이 먼저 처리한다. 다만 툴바·여백처럼
    // .cmt-editor 안이지만 ProseMirror 밖인 곳에 놓으면 편집기 handler가 호출되지 않는다.
    // 루트에서는 defaultPrevented 여부를 보고 **아직 처리되지 않은** 파일만 같은 insertFiles 경로로
    // 넘긴다. 상위 TicketDialog까지 전파하면 티켓 첨부로도 올라가므로 여기서 멈춘다.
    hasFiles(e) {
      const t = e.dataTransfer && e.dataTransfer.types;
      return !!t && Array.prototype.indexOf.call(t, "Files") >= 0;
    },
    onDragEnter(e) { if (this.hasFiles(e)) { this.dragDepth++; this.dragOver = true; } },
    onDragOver(e) {
      if (!this.hasFiles(e)) return;
      e.dataTransfer.dropEffect = "copy";
      this.dragOver = true;    // dragenter 를 놓치는 경로(자식 위로 바로 진입)가 있어 여기서도 켠다
    },
    onDragLeave() { this.dragDepth = Math.max(0, this.dragDepth - 1); if (!this.dragDepth) this.dragOver = false; },
    onDropFiles(e) {
      this.dragDepth = 0; this.dragOver = false;
      if (!this.hasFiles(e) || e.defaultPrevented) return;
      e.preventDefault();
      e.stopPropagation();
      this.insertFiles(e.dataTransfer.files);
    },

    /** 아래 손잡이를 끌어 본문 높이를 바꾼다(인라인 모드 전용).
     *  pointer 이벤트를 window에 걸어 손잡이 밖으로 나가도 끌림을 유지한다. */
    startResize(e) { this.startResizeFrom(e, 1); },
    /** 새 댓글 도크의 상단 경계용. 위로 끌수록 본문이 커진다. */
    startResizeFromTop(e) { this.startResizeFrom(e, -1); },
    startResizeFrom(e, direction) {
      if (this.maximized) return;
      const host = this.$refs.ed;
      if (!host) return;
      e.preventDefault();
      this.resizing = true;
      const startY = e.clientY;
      const startH = host.getBoundingClientRect().height;
      const move = (ev) => {
        const delta = direction * (ev.clientY - startY);
        const h = Math.max(H_MIN, Math.min(H_MAX, Math.round(startH + delta)));
        this.hostH = h;
      };
      const up = () => {
        this.resizing = false;
        window.removeEventListener("pointermove", move);
        window.removeEventListener("pointerup", up);
        if (this.hostH) saveEditorHeight(this.heightKey, this.hostH);
        // ★ 드래그가 끝나면 브라우저가 click 을 한 번 더 쏜다. 그 click 이 바깥으로 올라가면
        //   오버레이의 '바깥 클릭 = 닫기' 에 걸려 **다이얼로그가 그냥 꺼진다**(실제로 겪었다).
        //   딱 한 번만 삼킨다 — 계속 막으면 다음 클릭까지 먹는다.
        window.addEventListener("click", (e) => { e.stopPropagation(); e.preventDefault(); },
                                { capture: true, once: true });
      };
      window.addEventListener("pointermove", move);
      window.addEventListener("pointerup", up);
    },
    /** 손잡이를 더블클릭하면 기본 높이로 되돌린다 — 잘못 늘렸을 때 되돌릴 길이 있어야 한다. */
    resetHeight() {
      this.hostH = validEditorHeight(this.initialHeight);
      try { localStorage.removeItem(this.heightKey); } catch (e) { /* noop */ }
    },
    inCodeBlock() { this.tick; return !!(this._ed && this._ed.isActive("codeBlock")); },
    codeLang() { this.tick; return (this._ed && this._ed.getAttributes("codeBlock").language) || ""; },
    /**
     * 선택기에 놓을 언어 목록 — **지금 코드블럭의 언어가 목록에 없으면 그것도 넣는다.**
     *
     * 옛 티켓의 코드블럭에는 우리가 등록하지 않은 언어(`text`·`plaintext`·대문자 표기 등)가
     * 붙어 있을 수 있다. 그때 <select> 의 값과 맞는 <option> 이 하나도 없으면 브라우저는
     * **빈 칸**을 보여 준다 — '(자동 감지)' 도 아니고 그 언어도 아닌, 아무 말도 안 하는 상태다
     * (리포트된 증상). 원래 값을 선택지로 넣어 주면 그대로 보이고, 건드리지 않는 한 보존된다.
     */
    codeLangs() {
      const cur = this.codeLang();
      const ls = this.languages || [];
      return cur && ls.indexOf(cur) < 0 ? [cur].concat(ls) : ls;
    },
    setCodeLang(e) {
      const lang = e.target.value;
      if (this._ed) this._ed.chain().focus().updateAttributes("codeBlock", { language: lang || null }).run();
    },
    inTable() { this.tick; return !!(this._ed && this._ed.isActive("table")); },
    tColBefore() { this.cmd((c) => c.addColumnBefore().run()); },
    tColAfter() { this.cmd((c) => c.addColumnAfter().run()); },
    tColDel() { this.cmd((c) => c.deleteColumn().run()); },
    tRowBefore() { this.cmd((c) => c.addRowBefore().run()); },
    tRowAfter() { this.cmd((c) => c.addRowAfter().run()); },
    tRowDel() { this.cmd((c) => c.deleteRow().run()); },
    tHeaderRow() { this.cmd((c) => c.toggleHeaderRow().run()); },
    tTableDel() { this.cmd((c) => c.deleteTable().run()); },
    onFile(e) {
      this.insertFiles(e.target.files);
      e.target.value = "";
    },

    /** 붙여넣기·드롭·파일 선택이 **모두 여기로** 온다 — 경로마다 따로 짜면 갈린다
     *  (실제로 선택 경로만 .txt 를 이미지로 넣고 있었다).
     *  제출 전까지는 아무것도 올리지 않는다: objectURL 로 본문에 자리만 잡아 두고, 제출할 때
     *  본문에 남아 있는 것만 업로드한다 — 넣었다 지운 파일이 첨부에 남지 않는다. */
    insertFiles(files) {
      if (!files || !files.length || !this._ed) return false;
      for (const f of files) {
        const url = URL.createObjectURL(f);
        if (f.type && f.type.startsWith("image/")) {
          // 이미지 — 그림 자체가 내용이라 본문에 그대로 그린다
          const ext = (f.type.split("/")[1] || "png").replace("jpeg", "jpg");
          const name = "paste-" + Date.now() + "-" + (++this._seq) + "." + ext;
          this._pending.set(url, { blob: f, name });
          this._ed.chain().focus().setImage({ src: url, alt: name }).run();
          this.applyFitWidth(url);              // 세로가 너무 길면 기본 상한으로 축소
        } else {
          // 그 외 파일 — "무엇이 붙어 있다" 는 사실이 내용이므로 칩으로 박는다.
          // ★ 원래 파일명을 그대로 쓴다. 이미지처럼 이름을 지어내면 '설계초안.txt' 가
          //   'paste-1784….plain' 이 돼 아무도 못 알아본다(실제로 그랬다).
          const name = f.name || ("file-" + Date.now() + "-" + (++this._seq));
          this._pending.set(url, { blob: f, name });
          this._ed.chain().focus()
            .insertContent({ type: "fileBadge", attrs: { href: url, name, size: f.size || 0 } })
            .insertContent(" ")
            .run();
        }
      }
      return true;
    },
    // 삽입된 이미지의 자연 크기를 재서 세로 상한을 넘으면 비율 유지로 width 지정.
    applyFitWidth(url) {
      fitWidth(url).then((w) => {
        if (!w || !this._ed || this._dead) return;
        const state = this._ed.state;
        let at = null;
        state.doc.descendants((n, pos) => {
          if (at === null && n.type.name === "image" && n.attrs.src === url && !n.attrs.width) at = pos;
        });
        if (at === null) return;
        const n = state.doc.nodeAt(at);
        if (!n) return;
        this._ed.view.dispatch(state.tr.setNodeMarkup(at, undefined,
          Object.assign({}, n.attrs, { width: w })));
      });
    },
    inImage() { this.tick; return !!(this._ed && this._ed.isActive("image")); },
    // 툴바 크기 조절 — 에디터 폭 대비 비율. pct 가 null 이면 원본(width 해제).
    imgWidth(pct) {
      if (!this._ed) return;
      let w = null;
      if (pct) {
        const host = this.$refs.ed && this.$refs.ed.querySelector(".ProseMirror");
        const base = host ? Math.max(120, host.clientWidth - 30) : 600;
        w = Math.max(48, Math.round(base * pct));
      }
      this._ed.chain().focus().updateAttributes("image", { width: w }).run();
    },
    // ── 작성 중 임시저장(IndexedDB, TTL 7일) — 가리기/이동해도 내용·이미지가 남는다 ──
    // 수정 모드는 원본이 있으므로 저장하지 않는다(새 댓글 작성만).
    //
    // **본문 편집은 초안을 쓰지 않는다.** 본문은 서버에 이미 있는 글이고, 다른 사람이 고쳤을 수도
    // 있다 — 열 때마다 최신 본문을 그대로 보여 줘야 한다. 지난 초안을 덮어 놓으면 사용자는
    // 자기가 안 쓴 글을 자기 글로 알고 저장한다. 상태 전이 코멘트도 그 창에서 끝나는 한 줄이다.
    draftKey() {
      // **본문 편집은 초안을 남기지 않는다.** 기준이 되는 글이 서버에 있고 남이 고칠 수도 있어,
      // '수정' 을 누를 때마다 최신 본문을 받아 거기서 시작한다. 그 옆에 지난 초안까지 두면
      // 어느 것이 진짜인지가 매번 문제가 된다 — 하나만 둔다.
      // (칸을 나누기 전엔 "내용이 비었으면 새 댓글" 로 갈라서, 설명이 빈 티켓의 본문
      //  편집기가 **새 댓글 초안을 불러왔다.**)
      if (this.kind !== "comment" || this.initial) return null;
      return "new:" + this.ticketKey;
    },
    saveDraftSoon() {
      const k = this.draftKey();
      if (!k || !this._ed) return;
      clearTimeout(this._dt);
      this._dt = setTimeout(() => {
        this._dt = null;
        this.flushDraft();
      }, 700);
    },
    /** 접는 순간에는 디바운스를 기다리지 않고 지금 보이는 초안을 저장한다. */
    async flushDraft() {
      const k = this.draftKey();
      if (!k || !this._ed || this._dead) return;
      clearTimeout(this._dt); this._dt = null;
      let html = this._ed.getHTML();
      const text = (this._ed.getText() || "").trim();
      const imgs = [];
      for (const [url, info] of this._pending) {
        if (!html.includes(url)) continue;
        const token = "draft:" + info.name;          // objectURL 은 새로고침 후 무효 → 토큰으로
        html = html.split(url).join(token);
        imgs.push({ token, name: info.name, blob: info.blob });
      }
      const write = (!text && !imgs.length)
        ? clearDraft(k)                               // 빈 초안은 남기지 않는다
        : saveDraft(k, { html, images: imgs });
      this._draftWrite = write;
      try { await write; } catch (_) { /* 임시저장 실패가 작성 자체를 막지는 않는다 */ }
      finally { if (this._draftWrite === write) this._draftWrite = null; }
    },
    async restoreDraft() {
      const k = this.draftKey();
      if (!k) return;
      purgeExpired();                       // 다른 티켓의 만료 초안도 이참에 정리(세션 1회)
      const rec = await loadDraft(k);
      if (!rec || !rec.html || this._dead || !this._ed) return;
      let html = rec.html;
      for (const im of (rec.images || [])) {
        try {
          const url = URL.createObjectURL(im.blob);     // 저장된 blob → 새 objectURL
          this._pending.set(url, { blob: im.blob, name: im.name });
          html = html.split(im.token).join(url);
        } catch (e) { /* noop */ }
      }
      this._ed.commands.setContent(html, { emitUpdate: false });
      this.restored = true;
    },
    async discardDraft() {
      const k = this.draftKey();
      clearTimeout(this._dt); this._dt = null;
      const prior = this._draftWrite;
      if (prior) { try { await prior; } catch (_) { /* 마지막 삭제가 최종 상태를 정한다 */ } }
      try { for (const u of this._pending.keys()) URL.revokeObjectURL(u); } catch (e) { /* noop */ }
      this._pending.clear();
      if (this._ed) this._ed.commands.clearContent(false);
      this.restored = false;
      const write = k ? clearDraft(k) : null;
      this._draftWrite = write;
      if (write) { try { await write; } catch (_) { /* IndexedDB 실패는 화면 취소를 막지 않는다 */ } }
      if (this._draftWrite === write) this._draftWrite = null;
    },
    // 바깥(생성 다이얼로그 등)에서 '설명을 쓸지' 판단용 — 글자·이미지·링크·체크박스 하나라도 있으면 false.
    isBlank() {
      if (!this._ed) return true;
      const text = (this._ed.getText() || "").trim();
      return !text && !/<img\b|<a\b|<input\b/i.test(this._ed.getHTML());
    },
    /** 접힌 바용 한 줄 미리보기. 이미지·표·코드블록처럼 한 줄로 읽을 수 없는 노드는 제외한다. */
    previewText(limit = 160) {
      const host = this.$refs.ed && this.$refs.ed.querySelector(".ProseMirror");
      if (!host) return "";
      const clone = host.cloneNode(true);
      clone.querySelectorAll("img, table, pre, hr, .img-wrap, .tableWrapper").forEach((n) => n.remove());
      clone.querySelectorAll("p, div, li, blockquote, h1, h2, h3, h4").forEach((n) => n.append(" "));
      const text = (clone.textContent || "").replace(/\u200b/g, "").replace(/\s+/g, " ").trim();
      const max = Math.max(24, Number(limit) || 160);
      return text.length > max ? text.slice(0, max).trimEnd() + "…" : text;
    },
    focus() { try { if (this._ed) this._ed.commands.focus(); } catch (_) { /* noop */ } },
    // Creation dialogs use this snapshot in the initial create request. Keep the editor
    // implementation private and expose only the serializable value and pending state.
    htmlValue() { return this._ed ? this._ed.getHTML() : ""; },
    hasPendingUploads() { return !!(this._pending && this._pending.size); },
    async submit() {
      if (this.busy || !this._ed) return;
      let html = this._ed.getHTML();
      // 안전망 — 앱 주소로 남은 티켓 링크(붙여넣기가 health 응답보다 빨랐던 경우)는 실 Jira 주소로.
      // 저장된 댓글은 다른 사람도 읽는다: localhost 링크로 남기면 안 된다.
      if (_jiraBase) {
        const port = location.port ? ":" + location.port : "";
        const re = new RegExp("https?://(?:localhost|127\\.0\\.0\\.1|\\[::1\\])" + port
                              + "(/browse/[A-Za-z][A-Za-z0-9]*-\\d+)", "g");
        html = html.replace(re, _jiraBase.replace(/\/+$/, "") + "$1");
      }
      const text = (this._ed.getText() || "").trim();
      // 이미지/링크 뱃지·체크박스만 있는 댓글도 유효한 내용이다(텍스트가 비어도 통과).
      // (체크박스는 getText 에 안 잡혀 '내용 없음' 으로 오판되던 문제 — <input> 존재로 판정.)
      const hasNode = /<img\b/i.test(html) || /<a\b/i.test(html) || /<input\b/i.test(html);
      if (!text && !hasNode) { this.err = "내용을 입력하세요."; return; }
      this.busy = true; this.err = "";
      const uploaded = [];
      const failed = [];                               // UPLOAD_TRIES 회 재시도 후에도 못 올린 파일 이름
      // 올릴 것부터 센다 — prod 는 첨부 하나에 몇 초씩 걸린다. 몇 개 중 몇 번째인지 모르면
      // 그 몇 초가 '멈춘 것' 으로 느껴진다.
      const queue = [];
      for (const [url, info] of this._pending) if (html.includes(url)) queue.push([url, info]);
      this.upTotal = queue.length;
      this.upDone = 0;
      this.upStart = Date.now();
      // 1초마다 경과 시간을 갱신한다(라벨이 살아 있어야 '진행 중'으로 읽힌다).
      this._upTick = setInterval(() => { this.tickNow = Date.now(); }, 1000);
      try {
        for (const [url, info] of queue) {
          this.upName = info.name;
          this.upSize = fmtSize((info.blob && info.blob.size) || 0);
          const file = new File([info.blob], info.name,
                                { type: (info.blob && info.blob.type) || "application/octet-stream" });
          // 최대 UPLOAD_TRIES 회까지 다시 올려 본다 — 간헐 실패(네트워크/세션)로 파일을 버리지 않게.
          let res = null;
          for (let attempt = 1; attempt <= UPLOAD_TRIES; attempt++) {
            try { res = await api.attachmentUpload(this.ticketKey, file); break; }
            catch (e) {
              if (attempt < UPLOAD_TRIES) {                        // 아직 기회가 남았으면 잠깐 뒤 재시도
                this.upName = info.name + " (재시도 " + attempt + "/" + (UPLOAD_TRIES - 1) + ")";
                await sleep(400 * attempt);                        // 점점 더 기다렸다 다시(백오프)
              }
            }
          }
          if (res) {
            uploaded.push(res.id);
            // objectURL → **첨부 콘텐츠 경로**(/secure/attachment/{id}/{name}). 파일명만 박으면
            // html 모드(prod)에서 <img src="name"> 가 앱 오리진 상대경로가 돼 이미지가 엑박이 된다.
            // 경로로 두면 렌더가 실제 첨부로 풀고(프록시 재작성), wiki 모드는 저장 시 !name! 로 축약한다.
            html = html.split(url).join(res.path || res.filename);
          } else {
            failed.push(info.name);                               // 끝내 실패 — 본문에서 참조를 걷어낸다
            html = stripPendingRef(html, url);
          }
          this.upDone += 1;
        }
        this.upName = ""; this.upSize = "";              // 이제 본문/댓글 자체를 올린다
        clearInterval(this._upTick); this._upTick = null;
        // 올릴 게 파일뿐이었는데 전부 실패 — 저장할 본문이 없다. 알리고 끝낸다(_pending 은 남겨
        // 사용자가 다시 [등록]으로 재시도할 수 있게). text 는 사용자가 친 글자(파일 참조 제거와 무관).
        if (!text && uploaded.length === 0 && failed.length) {
          pushToast({ kind: "error", icon: "⚠", title: "파일 업로드 실패",
                      message: failed.join(", ") + " — 모두 실패해 저장하지 않았습니다. 다시 시도해 주세요.",
                      timeout: 9000 });
          this.err = "파일 업로드에 모두 실패했습니다: " + failed.join(", ");
          return;                                                 // finally 가 busy=false
        }
        await this.submitFn(html);
        const dk = this.draftKey();
        // 제출 직전 onUpdate가 예약한 saveDraft가 clearDraft **뒤에** 끝나면 완료된 글이 다시
        // 살아난다. 예약을 취소하고 이미 시작한 write까지 기다린 다음 마지막으로 삭제한다.
        clearTimeout(this._dt); this._dt = null;
        if (this._draftWrite) { try { await this._draftWrite; } catch (_) { /* IndexedDB 실패는 무해 */ } }
        if (dk) await clearDraft(dk);                 // 제출 성공 → 임시저장 삭제(마지막 write)
        this._draftWrite = null;
        for (const u of this._pending.keys()) URL.revokeObjectURL(u);
        this._pending.clear();
        // 새 댓글 editor를 부모가 계속 mount해도 다음 입력은 빈 상태여야 한다. 수정/본문은
        // 서버 원문을 유지해야 하므로 새 comment에만 적용한다. false=onUpdate/draft 저장 미발생.
        if (this.kind === "comment" && !this.initial && this._ed && !this._dead) {
          this._ed.commands.clearContent(false);
          this.restored = false;
        }
        // 본문 저장까지 성공한 뒤에야 '일부 파일 실패'를 알린다 — 본문은 확실히 저장됐고, 어떤
        // 파일이 빠졌는지 우하단 알림으로 분명히 남긴다(다이얼로그가 닫혀도 보이도록 토스트로).
        if (failed.length) {
          pushToast({ kind: "error", icon: "⚠", title: "파일 업로드 실패",
                      message: failed.join(", ") + " — 본문은 저장했습니다. 파일은 다시 첨부해 주세요.",
                      timeout: 9000 });
          this.err = "파일 업로드 실패: " + failed.join(", ") + " (본문은 저장됨)";
        }
        this.$emit("submitted");
      } catch (e) {
        for (const id of uploaded) { try { await api.attachmentDelete(this.ticketKey, id); } catch (_) { /* noop */ } }
        this.err = "저장 실패: " + ((e && e.message) || e);
      } finally { this.busy = false; }
    },
  },
  template: `
  <div class="cmt-editor" :class="{ maximized, 'drag-over': dragOver }"
       @dragenter="onDragEnter" @dragover="onDragOver" @dragleave="onDragLeave" @drop="onDropFiles">
    <div v-if="loadErr" class="cmt-ed-err">{{ loadErr }}
      <button class="cmt-ed-btn" @click="$emit('cancel')">닫기</button>
    </div>
    <template v-else>
      <div class="cmt-tb" v-show="ready">
        <button type="button" class="tb-b" :class="{on:active('bold')}" @click="tbBold" title="굵게"><b>B</b></button>
        <button type="button" class="tb-b" :class="{on:active('italic')}" @click="tbItalic" title="기울임"><i>I</i></button>
        <button type="button" class="tb-b" :class="{on:active('strike')}" @click="tbStrike" title="취소선"><s>S</s></button>
        <button type="button" class="tb-b" :class="{on:active('code')}" @click="tbCode" title="인라인 코드">&lt;/&gt;</button>
        <!-- 글자색 — 문자서식과 같은 묶음에 둔다(첫 줄에 보이게). -->
        <span class="tb-style">
          <button type="button" class="tb-b tb-color-b" :class="{on:colorOpen}"
                  @click.stop="colorOpen=!colorOpen; bgOpen=false" title="글자색"><b class="tb-ca">A</b><i class="tb-caret">▾</i></button>
          <span v-if="colorOpen" class="tb-style-pop tb-sw-pop" @click.stop>
            <button v-for="c in COLORS" :key="'fc'+c.k" type="button" class="tb-sw" :class="{none:!c.k}"
                    :style="c.k ? {background:c.k} : {}" :title="c.label" @click="setFontColor(c.k)"></button>
          </span>
          <span v-if="colorOpen" class="tb-style-back" @click.stop="colorOpen=false"></span>
        </span>
        <!-- 배경색(형광펜) -->
        <span class="tb-style">
          <button type="button" class="tb-b tb-bg-b" :class="{on:bgOpen}"
                  @click.stop="bgOpen=!bgOpen; colorOpen=false" title="배경색(형광펜)"><b class="tb-ba">A</b><i class="tb-caret">▾</i></button>
          <span v-if="bgOpen" class="tb-style-pop tb-sw-pop" @click.stop>
            <button v-for="c in BGCOLORS" :key="'bg'+c.k" type="button" class="tb-sw" :class="{none:!c.k}"
                    :style="c.k ? {background:c.k} : {}" :title="c.label" @click="setFontBg(c.k)"></button>
          </span>
          <span v-if="bgOpen" class="tb-style-back" @click.stop="bgOpen=false"></span>
        </span>
        <span class="tb-sep"></span>
        <!-- 스타일 콤보 — 문단/제목/코드블록. 헤딩 버튼 셋을 여기로 합쳤다(툴바가 짧아진다). -->
        <span class="tb-style" @keydown.esc="styleOpen = false">
          <button type="button" class="tb-b tb-style-b" :class="{on:styleOpen}" @click.stop="styleOpen = !styleOpen"
                  :title="'글 스타일 — ' + curStyle.label">{{ curStyle.short }}<i class="tb-caret">▾</i></button>
          <span v-if="styleOpen" class="tb-style-pop" @click.stop>
            <button v-for="o in STYLES" :key="o.k" type="button" class="tb-style-i"
                    :class="[o.k, { on: curStyle.k === o.k }]" @click="setStyle(o)">
              <span class="tb-style-t">{{ o.label }}</span><em>{{ o.hint }}</em>
            </button>
          </span>
          <span v-if="styleOpen" class="tb-style-back" @click.stop="styleOpen = false"></span>
        </span>
        <span class="tb-sep"></span>
        <button type="button" class="tb-b" :class="{on:active('bulletList')}" @click="tbBullet" title="불릿">•</button>
        <button type="button" class="tb-b" :class="{on:active('orderedList')}" @click="tbOrdered" title="번호">1.</button>
        <button type="button" class="tb-b" :class="{on:active('taskList')}" @click="tbTask" title="체크박스(할 일)">☑</button>
        <button type="button" class="tb-b" :class="{on:active('blockquote')}" @click="tbQuote" title="인용">❝</button>
        <button type="button" class="tb-b" :class="{on:active('codeBlock')}" @click="tbCodeBlock" title="코드블록">{ }</button>
        <span class="tb-sep"></span>
        <button type="button" class="tb-b co-i" :class="{on:inCallout('info')}" @click="tbCallout('info')" title="정보 콜아웃 {info}">ℹ</button>
        <button type="button" class="tb-b co-n" :class="{on:inCallout('note')}" @click="tbCallout('note')" title="노트 콜아웃 {note}">📌</button>
        <button type="button" class="tb-b co-t" :class="{on:inCallout('tip')}" @click="tbCallout('tip')" title="팁 콜아웃 {tip}">💡</button>
        <button type="button" class="tb-b co-w" :class="{on:inCallout('warning')}" @click="tbCallout('warning')" title="경고 콜아웃 {warning}">⚠</button>
        <span class="tb-sep"></span>
        <!-- 정렬 — 문단·제목·표 셀에 적용. 표 셀에서도 쓰인다. -->
        <button type="button" class="tb-b" :class="{on:isAlign('left')}" @click="tbAlign('left')" title="왼쪽 정렬">⬅</button>
        <button type="button" class="tb-b" :class="{on:isAlign('center')}" @click="tbAlign('center')" title="가운데 정렬">⬌</button>
        <button type="button" class="tb-b" :class="{on:isAlign('right')}" @click="tbAlign('right')" title="오른쪽 정렬">➡</button>
        <span class="tb-sep"></span>
        <!-- 글꼴 — 기본 vs 코딩(고정폭). 선택 글자에 적용된다. -->
        <span class="tb-style">
          <button type="button" class="tb-b tb-style-b" :class="{on:fontOpen}" @click.stop="fontOpen = !fontOpen"
                  title="글꼴">{{ curFont.short }}<i class="tb-caret">▾</i></button>
          <span v-if="fontOpen" class="tb-style-pop" @click.stop>
            <button v-for="f in FONTS" :key="f.k" type="button" class="tb-style-i"
                    :class="{ on: curFont.k === f.k }" @click="setFont(f)">
              <span class="tb-style-t" :style="{ fontFamily: f.css || 'inherit' }">{{ f.label }}</span>
            </button>
          </span>
          <span v-if="fontOpen" class="tb-style-back" @click.stop="fontOpen = false"></span>
        </span>
        <span class="tb-sep"></span>
        <button type="button" class="tb-b" :class="{on:active('linkBadge')}" @click="tbLink"
                title="링크 뱃지 (선택 텍스트가 제목이 됨 · 뱃지 더블클릭으로 수정)">🔗</button>
        <span class="tb-style tp-wrap">
          <button type="button" class="tb-b" :class="{on:tablePick}" @click.stop="openTablePicker" title="표 삽입 — 행·열 선택">▦</button>
          <span v-if="tablePick" class="tp-pop" @click.stop @mouseleave="tpR = 0; tpC = 0">
            <span class="tp-grid">
              <span v-for="i in 64" :key="i" class="tp-cell"
                    :class="{ on: tpRowOf(i) <= tpR && tpColOf(i) <= tpC }"
                    @mouseenter="tpR = tpRowOf(i); tpC = tpColOf(i)"
                    @click="insertTableSize(tpRowOf(i), tpColOf(i))"></span>
            </span>
            <span class="tp-label">{{ tpR && tpC ? (tpR + ' × ' + tpC) : '행 × 열 선택' }}</span>
          </span>
          <span v-if="tablePick" class="tb-style-back" @click.stop="tablePick = false"></span>
        </span>
        <button type="button" class="tb-b" @click="mdTable = true" title="마크다운 표 붙여넣기 → 변환">⊞</button>
        <button type="button" class="tb-b" @click="tbImage" title="이미지">🖼</button>
        <span class="tb-sep"></span>
        <!-- AI 자동완성 — 이 에디터가 무엇을(본문/코멘트) 어느 티켓에 쓰는 중인지 서버가 알고
             있으므로, 사용자는 "무엇을 써 달라"만 적으면 된다. 결과는 삽입될 뿐 저장은 사용자가. -->
        <span class="tb-style ai-wrap">
          <button type="button" ref="aiBtn" class="tb-b tb-ai" :class="{on:aiOpen}" @click.stop="openAi"
                  title="AI 자동완성 — 지금 쓰는 글을 이어 쓰거나 새로 초안을 만든다">AI<span class="tb-ai-spark" aria-hidden="true">✨</span></button>
          <span v-if="aiOpen" class="ai-pop" :style="aiPopStyle" @click.stop @keydown.esc="aiOpen=false">
            <template v-if="aiReady === false">
              <span class="ai-err">AI 를 쓸 수 없습니다 — {{ aiWhy || 'LLM 연결이 설정되지 않았습니다.' }}</span>
              <span class="ai-row">
                <span class="ai-hint">키를 등록하면 바로 쓸 수 있습니다</span>
                <button type="button" class="cmt-ed-btn ghost" @click="aiOpen=false">닫기</button>
                <button type="button" class="cmt-ed-btn primary" @click="aiSettings = true">설정</button>
              </span>
            </template>
            <template v-else>
            <textarea ref="aiInput" class="ai-in" v-model="aiPrompt" rows="4"
                      :placeholder="kind === 'description'
                        ? '예) 배경·범위·완료 조건까지 본문 초안 잡아줘'
                        : '예) 진행 상황 공유 코멘트 써줘'"
                      @keydown.enter.exact.prevent="runAi"></textarea>
            <label v-if="hasBody()" class="ai-ck">
              <input type="checkbox" v-model="aiSeed"> 지금 쓰던 글을 재료로 사용
            </label>
            <label class="ai-ck">
              <input type="checkbox" v-model="aiReplace"> 전체 교체 (끄면 커서 위치에 이어쓰기)
            </label>
            <span v-if="aiAsk" class="ai-ask">{{ aiAsk }}</span>
            <span v-if="aiErr" class="ai-err">{{ aiErr }}</span>
            <span class="ai-row">
              <span class="ai-hint">Enter 로 생성 · Ctrl+Z 로 되돌리기</span>
              <button type="button" class="cmt-ed-btn ghost" @click="aiOpen=false">취소</button>
              <button type="button" class="cmt-ed-btn primary" :disabled="aiBusy" @click="runAi">
                {{ aiBusy ? '작성 중…' : '생성' }}
              </button>
            </span>
            </template>
          </span>
          <span v-if="aiOpen" class="tb-style-back" @click.stop="aiOpen = false"></span>
        </span>
        <button type="button" class="tb-b" style="margin-left:auto" @click="toggleMax"
                :title="maximized ? '최대화 해제' : '에디터 최대화'">{{ maximized ? '🗗' : '🗖' }}</button>
        <input ref="file" type="file" multiple style="display:none" @change="onFile">
        <span v-if="aiBusy" class="ai-run" aria-live="polite"><i class="ai-spin"></i>AI가 작성 중… 잠시만요</span>
        <AgentSettingsDialog v-if="aiSettings" @close="aiSettings = false; openAi()" />
        <LinkPicker v-if="pick" :mode="pick" insert @close="pick = ''" @pick="onPick" />
        <MarkdownTableDialog v-if="mdTable" @close="mdTable = false" @insert="insertMdTable" />
      </div>
      <div class="cmt-tb cmt-tb-tbl" v-show="ready && inCodeBlock()">
        <span class="tb-lbl">코드 언어</span>
        <select class="cmt-langsel" :value="codeLang()" @change="setCodeLang">
          <option value="">(자동 감지)</option>
          <option v-for="l in codeLangs()" :key="l" :value="l">{{ l }}</option>
        </select>
      </div>
      <div class="cmt-tb cmt-tb-tbl" v-show="ready && inImage()">
        <span class="tb-lbl">이미지</span>
        <button type="button" class="tb-b" @click="imgWidth(0.25)" title="작게 (폭 25%)">25%</button>
        <button type="button" class="tb-b" @click="imgWidth(0.5)" title="보통 (폭 50%)">50%</button>
        <button type="button" class="tb-b" @click="imgWidth(0.75)" title="크게 (폭 75%)">75%</button>
        <button type="button" class="tb-b" @click="imgWidth(1)" title="가득 (폭 100%)">100%</button>
        <span class="tb-sep"></span>
        <button type="button" class="tb-b" @click="imgWidth(null)" title="원본 크기로">원본</button>
        <span class="tb-lbl" style="margin-left:auto">모서리 드래그로도 조절</span>
      </div>
      <div class="cmt-tb cmt-tb-tbl" v-show="ready && inTable()">
        <span class="tb-lbl">표</span>
        <button type="button" class="tb-b tb-ic" @click="tColBefore" title="왼쪽에 열 추가">
          <svg viewBox="0 0 16 16" aria-hidden="true"><rect x="7.5" y="2.5" width="6" height="11" rx="1"/><path d="M10.5 2.5v11"/><path d="M3.2 6.2v3.6M1.4 8h3.6"/></svg></button>
        <button type="button" class="tb-b tb-ic" @click="tColAfter" title="오른쪽에 열 추가">
          <svg viewBox="0 0 16 16" aria-hidden="true"><rect x="2.5" y="2.5" width="6" height="11" rx="1"/><path d="M5.5 2.5v11"/><path d="M12.8 6.2v3.6M11 8h3.6"/></svg></button>
        <button type="button" class="tb-b tb-ic tb-del" @click="tColDel" title="열 삭제">
          <svg viewBox="0 0 16 16" aria-hidden="true"><rect x="2.5" y="2.5" width="11" height="11" rx="1"/><path d="M8 2.5v11"/><path d="M5.6 5.6l4.8 4.8M10.4 5.6l-4.8 4.8"/></svg></button>
        <span class="tb-sep"></span>
        <button type="button" class="tb-b tb-ic" @click="tRowBefore" title="위에 행 추가">
          <svg viewBox="0 0 16 16" aria-hidden="true"><rect x="2.5" y="7.5" width="11" height="6" rx="1"/><path d="M2.5 10.5h11"/><path d="M6.2 3.2h3.6M8 1.4v3.6"/></svg></button>
        <button type="button" class="tb-b tb-ic" @click="tRowAfter" title="아래에 행 추가">
          <svg viewBox="0 0 16 16" aria-hidden="true"><rect x="2.5" y="2.5" width="11" height="6" rx="1"/><path d="M2.5 5.5h11"/><path d="M6.2 12.8h3.6M8 11v3.6"/></svg></button>
        <button type="button" class="tb-b tb-ic tb-del" @click="tRowDel" title="행 삭제">
          <svg viewBox="0 0 16 16" aria-hidden="true"><rect x="2.5" y="2.5" width="11" height="11" rx="1"/><path d="M2.5 8h11"/><path d="M5.6 5.6l4.8 4.8M10.4 5.6l-4.8 4.8"/></svg></button>
        <span class="tb-sep"></span>
        <button type="button" class="tb-b tb-ic" @click="tHeaderRow" title="헤더 행 토글">
          <svg viewBox="0 0 16 16" aria-hidden="true"><rect x="2.5" y="2.5" width="11" height="11" rx="1"/><path d="M2.5 6.5h11"/><rect class="fillbar" x="2.5" y="2.5" width="11" height="4"/></svg></button>
        <button type="button" class="tb-b tb-ic tb-del" @click="tTableDel" title="표 삭제">
          <svg viewBox="0 0 16 16" aria-hidden="true"><rect x="2.5" y="2.5" width="11" height="11" rx="1"/><path d="M2.5 6.5h11M6.5 2.5v11"/><path d="M10 10l3.5 3.5M13.5 10L10 13.5"/></svg></button>
      </div>
      <div v-if="restored" class="cmt-restored">
        <span>작성 중이던 내용을 복원했습니다.</span>
        <button type="button" class="cmt-ed-btn ghost" @click="discardDraft">새로 쓰기</button>
      </div>
      <div ref="ed" class="cmt-ed-host"
           :style="!maximized && hostH ? { height: hostH + 'px', maxHeight: 'none' } : null"></div>
      <!-- 세로 크기 조절 손잡이 — 최대화 모드에는 없다(거기선 창이 높이를 정한다).
           얇은 선이 아니라 잡을 수 있는 띠로 둔다: 1~2px 짜리는 조준하다 지친다. -->
      <div v-if="!maximized" class="cmt-ed-grip" :class="{ on: resizing }"
           @pointerdown="startResize" @dblclick="resetHeight"
           title="끌어서 높이 조절 · 더블클릭하면 기본 높이"><i></i></div>
      <div v-if="hideFooter && err" class="cmt-ed-msg solo">{{ err }}</div>
      <div v-if="!hideFooter" class="cmt-ed-bar">
        <span v-if="err" class="cmt-ed-msg">{{ err }}</span>
        <button class="cmt-ed-btn ghost" :disabled="busy" @click="$emit('cancel')">취소</button>
        <button class="cmt-ed-btn primary" :disabled="busy || !ready" @click="submit">
          {{ busy ? busyLabel : submitLabel }}</button>
      </div>
    </template>
  </div>`,
};
