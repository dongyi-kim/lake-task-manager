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
import { debouncedItems } from "../../lib/typeahead.js";

function esc(s) { return String(s == null ? "" : s).replace(/[&<>"]/g, (c) => (
  { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c])); }

// 멘션 팝업 아바타 — 네트워크 요청(=404 스팸) 없이 이니셜 원.
// 색은 기본 아바타·댓글 구분 바와 같은 시그니처 컬러(colors.js) 를 쓴다.
function mnAvatar(name, id) {
  return `<span class="mn-av" style="background:${sigColor(id || name)}">${esc(initialOf(name, id))}</span>`;
}
const _URL_RE = /^https?:\/\/\S+$/i;


// Jira 콜아웃 매크로 블록 — <div class="callout callout-info"> <-> {info}…{info}.
// 표준 4종(info/note/tip/warning)을 툴바로 넣는다. 렌더 CSS(.tkt-desc .callout*)를 그대로 쓴다.
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
// 이미 저장된 본문을 편집기로 열 때: '=== 제목 ===' 한 줄짜리 문단을 구분선 노드로 바꾼다.
// 안 바꾸면 편집기에선 그냥 글자로 보이고, 사용자가 손대면 형식이 깨진다.
const SEC_LINE = /<p>\s*={3,}\s*([^<]+?)\s*={3,}\s*<\/p>/gi;
function liftSections(html) {
  return (html || "").replace(SEC_LINE, (m, t) => '<div class="sec-title-node">' + t + "</div>");
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
            a.className = "jira-badge tkt";
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
function openBadgeEditor(anchor, attrs, onSave) {
  document.querySelectorAll(".badge-edit").forEach((e) => e.remove());
  const box = document.createElement("div");
  box.className = "badge-edit";
  box.innerHTML =
    '<label>표시할 제목</label><input class="be-t" type="text">' +
    '<label>링크 URL</label><input class="be-h" type="text">' +
    '<div class="be-row"><button type="button" class="be-cancel">취소</button>' +
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
          if (e.isActive("listItem")) return e.chain().focus().sinkListItem("listItem").run() || true;
          return true;                       // 소비만 — Tab 으로 에디터 밖으로 나가지 않게
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
];

const SLASH = [
  { g: "삽입", id: "code", ic: "{ }", t: "코드 블록", h: "언어 강조", k: "code 코드 codeblock",
    run: (e, r) => e.chain().focus().deleteRange(r).setCodeBlock().run() },
  { g: "삽입", id: "table", ic: "▦", t: "표", h: "3×3 · 머리행", k: "table 표 테이블",
    run: (e, r) => e.chain().focus().deleteRange(r)
                    .insertTable({ rows: 3, cols: 3, withHeaderRow: true }).run() },
  { g: "삽입", id: "markdown_table", ic: "⊞", t: "마크다운 표", h: "붙여넣어 변환",
    k: "markdown_table md 마크다운 표 붙여넣기 paste",
    run: (e, r, host) => { e.chain().focus().deleteRange(r).run(); host.mdTable = true; } },
  { g: "삽입", id: "quote", ic: "❝", t: "인용", h: "", k: "quote 인용",
    run: (e, r) => e.chain().focus().deleteRange(r).toggleBlockquote().run() },
  { g: "삽입", id: "bullet", ic: "•", t: "글머리 목록", h: "", k: "list bullet 목록 불릿",
    run: (e, r) => e.chain().focus().deleteRange(r).toggleBulletList().run() },
  { g: "삽입", id: "ordered", ic: "1.", t: "번호 목록", h: "", k: "numlist ordered 번호 목록",
    run: (e, r) => e.chain().focus().deleteRange(r).toggleOrderedList().run() },
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
    render: () => {
      let el = null, items = [], sel = 0, command = null;
      const paint = () => {
        if (!el) return;
        if (!items.length) { el.innerHTML = '<div class="mn-empty">해당하는 명령이 없습니다</div>'; return; }
        let html = "", g = "";
        items.forEach((it, i) => {
          if (it.g !== g) { g = it.g; html += `<div class="sl-g">${esc(g)}</div>`; }
          html += `<div class="sl-item${i === sel ? " sel" : ""}" data-i="${i}">`
                + `<span class="sl-ic">${esc(it.ic)}</span>`
                + `<span class="sl-t">${esc(it.t)}</span>`
                + (it.h ? `<span class="sl-h">${esc(it.h)}</span>` : "")
                + `<span class="sl-k">/${esc(it.id)}</span></div>`;
        });
        el.innerHTML = html;
        el.querySelectorAll(".sl-item").forEach((row) => {
          row.addEventListener("mousedown", (e) => { e.preventDefault(); pick(+row.dataset.i); });
        });
        const cur = el.querySelector(".sl-item.sel");
        if (cur && cur.scrollIntoView) cur.scrollIntoView({ block: "nearest" });
      };
      const pick = (i) => { const it = items[i]; if (it && command) command(it); };
      const place = (rectFn) => {
        if (!el || !rectFn) return; const r = rectFn(); if (!r) return;
        // 아래가 모자라면 위로 — 화면 끝에서 목록이 잘리면 고를 수가 없다.
        const h = el.offsetHeight || 260;
        const below = window.innerHeight - r.bottom;
        el.style.left = Math.round(Math.min(r.left, window.innerWidth - 300)) + "px";
        el.style.top = Math.round(below < h + 12 ? Math.max(8, r.top - h - 4) : r.bottom + 4) + "px";
      };
      return {
        onStart: (p) => {
          items = p.items || []; sel = 0; command = p.command;
          el = document.createElement("div"); el.className = "mention-popup slash-popup";
          document.body.appendChild(el); paint(); place(p.clientRect);
        },
        onUpdate: (p) => {
          items = p.items || []; command = p.command;
          if (sel >= items.length) sel = 0;
          paint(); place(p.clientRect);
        },
        onKeyDown: (p) => {
          const k = p.event.key, n = items.length;
          if (k === "ArrowDown") { sel = n ? (sel + 1) % n : 0; paint(); return true; }
          if (k === "ArrowUp") { sel = n ? (sel - 1 + n) % n : 0; paint(); return true; }
          if (k === "Enter" || k === "Tab") { if (n) { pick(sel); return true; } }
          if (k === "Escape") { return true; }
          return false;
        },
        onExit: () => { if (el) el.remove(); el = null; },
      };
    },
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

function mentionSuggestion(ticketKey) {
  // 디바운스 없이 두면 **한 글자마다** 요청이 나간다(한글은 자모 단위라 더 심하다).
  // 대기 중이던 호출은 최신 결과로 함께 해소한다 — 취소해 버리면 팝업이 멎는다.
  // ★ TipTap 은 items 에 **객체**({ query, editor, … })를 넘긴다. 문자열로 받으면
  //   질의가 "[object Object]" 가 돼 팝업이 늘 비어 보인다.
  const fetchUsers = debouncedItems((q) => api.mentionUsers(q, ticketKey));
  return {
    char: "@",
    items: ({ query }) => fetchUsers(query || ""),
    render: () => {
      let el = null, items = [], sel = 0, command = null;
      const paint = () => {
        if (!el) return;
        if (!items.length) { el.innerHTML = '<div class="mn-empty">사용자 없음</div>'; return; }
        el.innerHTML = items.map((u, i) =>
          `<div class="mn-item${i === sel ? " sel" : ""}" data-i="${i}">`
          + mnAvatar(u.name, u.id)
          // 팝업엔 회사까지 붙은 전체 표시명(동명이인 구분). 삽입되는 멘션은 본명만(pick).
          + `<span class="mn-nm">${esc(u.display || u.name)}</span><span class="mn-id">${esc(u.id)}</span></div>`).join("");
        el.querySelectorAll(".mn-item").forEach((row) => {
          row.addEventListener("mousedown", (e) => { e.preventDefault(); pick(+row.dataset.i); });
        });
      };
      const pick = (i) => { const u = items[i]; if (u && command) command({ id: u.id, label: u.name }); };
      const place = (rectFn) => {
        if (!el || !rectFn) return; const r = rectFn(); if (!r) return;
        el.style.left = Math.round(r.left) + "px";
        el.style.top = Math.round(r.bottom + 4) + "px";
      };
      return {
        onStart: (p) => {
          items = p.items || []; sel = 0; command = p.command;
          el = document.createElement("div"); el.className = "mention-popup";
          document.body.appendChild(el); paint(); place(p.clientRect);
        },
        onUpdate: (p) => { items = p.items || []; command = p.command; if (sel >= items.length) sel = 0; paint(); place(p.clientRect); },
        onKeyDown: (p) => {
          const k = p.event.key, n = items.length;
          if (k === "ArrowDown") { sel = n ? (sel + 1) % n : 0; paint(); return true; }
          if (k === "ArrowUp") { sel = n ? (sel - 1 + n) % n : 0; paint(); return true; }
          if (k === "Enter") { if (n) { pick(sel); return true; } }
          if (k === "Escape") { return true; }
          return false;
        },
        onExit: () => { if (el) el.remove(); el = null; },
      };
    },
  };
}

// 끌어서 정한 높이는 **기억한다**. 매번 다시 늘리게 하면 늘리는 의미가 없다 —
// 긴 글을 쓰는 사람은 늘 길게 쓴다. 화면(px)이라 localStorage 로 충분하다.
const H_KEY = "cmtEditorH";
const H_MIN = 120;
const H_MAX = 720;

function loadEditorHeight() {
  try {
    const v = parseInt(localStorage.getItem(H_KEY) || "", 10);
    return v >= H_MIN && v <= H_MAX ? v : null;
  } catch (e) { return null; }
}
function saveEditorHeight(v) {
  try { localStorage.setItem(H_KEY, String(v)); } catch (e) { /* 사파리 프라이빗 등 */ }
}

export default {
  name: "CommentEditor",
  components: { LinkPicker, MarkdownTableDialog },
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
    // 이 에디터가 무엇을 쓰는 중인가 — **초안 저장소를 가르는 열쇠**다.
    // 예전엔 "내용이 비었으면 새 댓글" 로 봤는데, 설명이 빈 티켓의 본문 편집기가 같은 조건에
    // 걸려 **새 댓글 초안을 본문에 불러왔다**. 목적이 다르면 칸도 달라야 한다.
    kind: { type: String, default: "comment" },   // comment | description | transition
  },
  emits: ["submitted", "cancel"],
  data() { return { ready: false, loadErr: "", busy: false, err: "", tick: 0, languages: [],
                    maximized: false, restored: false,
                    // 인라인 모드에서 사용자가 끌어 정한 본문 높이(px). null = 기본값.
                    // 최대화 모드에는 안 쓴다 — 거기선 창이 높이를 정한다.
                    hostH: loadEditorHeight(), resizing: false,
                    // 업로드 진행 — 몇 개 중 몇 번째, 지금 무엇을 올리는 중인가
                    upTotal: 0, upDone: 0, upName: "",
                    // 파일을 이 에디터 위로 끌고 왔는가 — 테두리로 "여기에 놓으면 본문" 을 말한다
                    dragOver: false, dragDepth: 0,
                    // '' | 'jira' | 'confluence' — '/' 로 연 검색창
                    pick: "",
                    mdTable: false, styleOpen: false }; },
  async mounted() {
    this._pending = new Map();        // objectURL -> { blob, name }
    this._seq = 0;
    jiraBase();                       // 앱 URL(/browse/KEY)→실 Jira 주소 변환용. 미리 받아 둔다.
    let T;
    try { T = await loadTiptap(); }
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
        T.StarterKit.configure({ codeBlock: false }),   // 아래 CodeBlockLowlight 로 교체(구문강조)
        // 코드블럭 — 원래 Jira 와 같은 태그(<pre class="jecodeblock"><code class="language-X">) + lowlight 강조
        T.CodeBlockLowlight.configure({ lowlight: T.lowlight, HTMLAttributes: { class: "jecodeblock" } }),
        calloutExt(T),
        ...(this.sections ? [sectionExt(T)] : []),
        slashExt(T, this),
        fileBadgeExt(T),
        singleLineHeadingExt(T),
        firstBlockEscapeExt(T),
        T.Mention.configure({ HTMLAttributes: { class: "mention" }, suggestion: mentionSuggestion(this.ticketKey) }),
        T.Table.configure({ resizable: true }), T.TableRow, T.TableHeader, T.TableCell,
        // inline:true — 이미지가 같은 줄에 글자와 나란히 놓이게(TipTap 기본은 블록이라 줄이 갈린다)
        imageResizeExt(T).configure({ inline: true }), linkBadgeExt(T),
        T.Placeholder.configure({ placeholder: "댓글을 입력하세요. '/' 로 표·코드·티켓 넣기, @ 로 멘션, 마크다운(#, -, ``` )" }),
      ],
      content: (this.sections ? liftSections(this.initial) : this.initial) || "",
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
            const title0 = norm ? norm.title : url;
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
              // 외부 URL — 라벨을 페이지 제목(og:title → <title>)으로. 실패하면 URL 그대로.
              api.linkTitle(url).then((r) => {
                if (r && r.title) updateBadgeTitle(self._ed, url, r.title);
              }).catch(() => { /* noop */ });
            }
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
    try { for (const u of this._pending.keys()) URL.revokeObjectURL(u); } catch (e) { /* noop */ }
    try { if (this._ed) this._ed.destroy(); } catch (e) { /* noop */ }
  },
  computed: {
    STYLES: () => STYLES,
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
      return "첨부 " + n + "/" + this.upTotal + (this.upName ? " · " + this.upName : "");
    },
  },
  methods: {
    active(name, attrs) { this.tick; return this._ed && this._ed.isActive(name, attrs); },
    cmd(fn) { if (this._ed) { fn(this._ed.chain().focus()); this._ed.commands.focus(); } },
    tbBold() { this.cmd((c) => c.toggleBold().run()); },
    tbItalic() { this.cmd((c) => c.toggleItalic().run()); },
    tbStrike() { this.cmd((c) => c.toggleStrike().run()); },
    tbCode() { this.cmd((c) => c.toggleCode().run()); },
    tbH(l) { this.cmd((c) => c.toggleHeading({ level: l }).run()); },
    /** 지금 커서가 놓인 블록 스타일(콤보 표시용). */
    setStyle(o) {
      this.styleOpen = false;
      this.cmd((c) => {
        if (o.k === "p") return c.setParagraph().run();
        if (o.k === "code") return c.toggleCodeBlock().run();
        if (o.k === "quote") return c.toggleBlockquote().run();
        return c.toggleHeading({ level: o.level }).run();
      });
    },
    tbBullet() { this.cmd((c) => c.toggleBulletList().run()); },
    tbOrdered() { this.cmd((c) => c.toggleOrderedList().run()); },
    tbQuote() { this.cmd((c) => c.toggleBlockquote().run()); },
    tbCodeBlock() { this.cmd((c) => c.toggleCodeBlock().run()); },
    tbTable() { this.cmd((c) => c.insertTable({ rows: 2, cols: 2, withHeaderRow: true }).run()); },
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
    onPick(it) {
      this.pick = "";
      const href = it.url || "";
      if (!href) return;
      const title = it.key ? (it.key + " " + (it.title || "")).trim() : (it.title || href);
      this._ed.chain().focus()
        .insertContent([{ type: "linkBadge", attrs: { href, title } }, { type: "text", text: " " }])
        .run();
    },
    inCallout(t) { this.tick; return !!(this._ed && this._ed.isActive("callout", { type: t })); },
    tbCallout(t) { this.cmd((c) => c.toggleCallout(t).run()); },
    toggleMax() { this.maximized = !this.maximized; },

    // 드래그 안내 — 실제 삽입은 ProseMirror 의 handleDrop 이 한다. 여기서는 **보이는 것만**
    // 맡는다(테두리). 두 곳에서 삽입하면 파일이 두 번 들어간다.
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
    onDropFiles() { this.dragDepth = 0; this.dragOver = false; },

    /** 아래 손잡이를 끌어 본문 높이를 바꾼다(인라인 모드 전용).
     *  pointer 이벤트 + setPointerCapture 를 쓰는 이유: 마우스가 에디터 밖으로 나가도 끌림이
     *  유지된다. mousemove 를 document 에 걸면 iframe·다른 요소 위에서 놓칠 수 있다. */
    startResize(e) {
      if (this.maximized) return;
      const host = this.$refs.ed;
      if (!host) return;
      e.preventDefault();
      this.resizing = true;
      const startY = e.clientY;
      const startH = host.getBoundingClientRect().height;
      const move = (ev) => {
        const h = Math.max(H_MIN, Math.min(H_MAX, Math.round(startH + (ev.clientY - startY))));
        this.hostH = h;
      };
      const up = () => {
        this.resizing = false;
        window.removeEventListener("pointermove", move);
        window.removeEventListener("pointerup", up);
        if (this.hostH) saveEditorHeight(this.hostH);
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
      this.hostH = null;
      try { localStorage.removeItem(H_KEY); } catch (e) { /* noop */ }
    },
    inCodeBlock() { this.tick; return !!(this._ed && this._ed.isActive("codeBlock")); },
    codeLang() { this.tick; return (this._ed && this._ed.getAttributes("codeBlock").language) || ""; },
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
    // ── 작성 중 임시저장(IndexedDB, TTL 7일) — 취소/이동해도 내용·이미지가 남는다 ──
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
        if (!this._ed || this._dead) return;
        let html = this._ed.getHTML();
        const text = (this._ed.getText() || "").trim();
        const imgs = [];
        for (const [url, info] of this._pending) {
          if (!html.includes(url)) continue;
          const token = "draft:" + info.name;          // objectURL 은 새로고침 후 무효 → 토큰으로
          html = html.split(url).join(token);
          imgs.push({ token, name: info.name, blob: info.blob });
        }
        if (!text && !imgs.length) { clearDraft(k); return; }   // 빈 초안은 남기지 않는다
        saveDraft(k, { html, images: imgs });
      }, 700);
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
      this._ed.commands.setContent(html, false);
      this.restored = true;
    },
    discardDraft() {
      const k = this.draftKey();
      if (k) clearDraft(k);
      try { for (const u of this._pending.keys()) URL.revokeObjectURL(u); } catch (e) { /* noop */ }
      this._pending.clear();
      if (this._ed) this._ed.commands.clearContent(true);
      this.restored = false;
    },
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
      // 이미지/링크 뱃지만 있는 댓글도 유효한 내용이다(텍스트가 비어도 통과).
      const hasNode = /<img\b/i.test(html) || /<a\b/i.test(html);
      if (!text && !hasNode) { this.err = "내용을 입력하세요."; return; }
      this.busy = true; this.err = "";
      const uploaded = [];
      // 올릴 것부터 센다 — prod 는 첨부 하나에 몇 초씩 걸린다. 몇 개 중 몇 번째인지 모르면
      // 그 몇 초가 '멈춘 것' 으로 느껴진다.
      const queue = [];
      for (const [url, info] of this._pending) if (html.includes(url)) queue.push([url, info]);
      this.upTotal = queue.length;
      this.upDone = 0;
      try {
        for (const [url, info] of queue) {
          this.upName = info.name;
          const file = new File([info.blob], info.name,
                                { type: (info.blob && info.blob.type) || "application/octet-stream" });
          const res = await api.attachmentUpload(this.ticketKey, file);
          uploaded.push(res.id);
          html = html.split(url).join(res.filename);    // objectURL → 실제 파일명
          this.upDone += 1;
        }
        this.upName = "";                                // 이제 본문/댓글 자체를 올린다
        await this.submitFn(html);
        const dk = this.draftKey();
        if (dk) clearDraft(dk);                      // 제출 성공 → 임시저장 삭제
        for (const u of this._pending.keys()) URL.revokeObjectURL(u);
        this._pending.clear();
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
        <button type="button" class="tb-b" :class="{on:active('blockquote')}" @click="tbQuote" title="인용">❝</button>
        <button type="button" class="tb-b" :class="{on:active('codeBlock')}" @click="tbCodeBlock" title="코드블록">{ }</button>
        <span class="tb-sep"></span>
        <button type="button" class="tb-b co-i" :class="{on:inCallout('info')}" @click="tbCallout('info')" title="정보 콜아웃 {info}">ℹ</button>
        <button type="button" class="tb-b co-n" :class="{on:inCallout('note')}" @click="tbCallout('note')" title="노트 콜아웃 {note}">📌</button>
        <button type="button" class="tb-b co-t" :class="{on:inCallout('tip')}" @click="tbCallout('tip')" title="팁 콜아웃 {tip}">💡</button>
        <button type="button" class="tb-b co-w" :class="{on:inCallout('warning')}" @click="tbCallout('warning')" title="경고 콜아웃 {warning}">⚠</button>
        <span class="tb-sep"></span>
        <button type="button" class="tb-b" :class="{on:active('linkBadge')}" @click="tbLink"
                title="링크 뱃지 (선택 텍스트가 제목이 됨 · 뱃지 더블클릭으로 수정)">🔗</button>
        <button type="button" class="tb-b" @click="tbTable" title="표 삽입">▦</button>
        <button type="button" class="tb-b" @click="mdTable = true" title="마크다운 표 붙여넣기 → 변환">⊞</button>
        <button type="button" class="tb-b" @click="tbImage" title="이미지">🖼</button>
        <button type="button" class="tb-b" style="margin-left:auto" @click="toggleMax"
                :title="maximized ? '최대화 해제' : '에디터 최대화'">{{ maximized ? '🗗' : '🗖' }}</button>
        <input ref="file" type="file" multiple style="display:none" @change="onFile">
        <LinkPicker v-if="pick" :mode="pick" insert @close="pick = ''" @pick="onPick" />
        <MarkdownTableDialog v-if="mdTable" @close="mdTable = false" @insert="insertMdTable" />
      </div>
      <div class="cmt-tb cmt-tb-tbl" v-show="ready && inCodeBlock()">
        <span class="tb-lbl">코드 언어</span>
        <select class="cmt-langsel" :value="codeLang()" @change="setCodeLang">
          <option value="">(자동 감지)</option>
          <option v-for="l in languages" :key="l" :value="l">{{ l }}</option>
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
