// CommentEditor.js — TipTap 기반 댓글 작성/수정 에디터 (모던 Confluence/Jira 스타일).
// · 마크다운 input rule: '# '·'## '·'- '·'1. '·'> '·백틱3개 실시간 변환 (StarterKit)
// · 고정 툴바: 굵게/기울임/취소선/코드 · H1~3 · 불릿/번호/인용/코드블록 · 링크/표/이미지
// · @사람 멘션: '@' 입력 → 유저 자동완성 팝업 → [~사번] 으로 저장(읽기 시 사용자 링크)
// · 링크 붙여넣기: URL 붙여넣으면 자동 링크(문서/웹 뱃지는 읽기 렌더에서 앱이 처리)
// · 이미지 붙여넣기/드롭 = 제출 시 업로드: 로컬 objectURL 미리보기 → 제출 때 첨부 업로드·롤백
// 부모는 submitFn(finalHTML) 만 넘긴다(작성/수정은 부모가 선택). 출력은 HTML(서버가 wiki 로 변환).
import { loadTiptap } from "../../lib/tiptap.js";
import { ensureHljsTheme } from "../../lib/hljs.js";
import { saveDraft, loadDraft, clearDraft } from "../../lib/draft.js";
import { api } from "../../lib/api.js";

function esc(s) { return String(s == null ? "" : s).replace(/[&<>"]/g, (c) => (
  { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c])); }

// 멘션 팝업 아바타 — 네트워크 요청(=404 스팸) 없이 이니셜 원. id 로 색 결정.
const _MN_COLORS = ["#6d4fc0", "#2d8a5f", "#c07a2d", "#b34a6b", "#3a6ea5", "#8a5a2d"];
function mnAvatar(name, id) {
  let h = 0; const s = id || name || "";
  for (let i = 0; i < s.length; i++) h = (h * 31 + s.charCodeAt(i)) >>> 0;
  const bg = _MN_COLORS[h % _MN_COLORS.length];
  const ch = ((name || id || "?").trim()[0] || "?").toUpperCase();
  return `<span class="mn-av" style="background:${bg}">${esc(ch)}</span>`;
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
            a.innerHTML = '<span class="jb-dot"></span><b class="jb-key"></b>'
              + '<span class="jb-name"></span><span class="jb-meta"></span>';
            a.querySelector(".jb-key").textContent = key;
            ticketData(key).then((bd) => {
              if (!bd || !a.isConnected) return;
              const dot = a.querySelector(".jb-dot"), nm = a.querySelector(".jb-name"),
                    mt = a.querySelector(".jb-meta");
              if (!dot || !nm || !mt) return;
              const cat = bd.statusCategory || "todo";
              dot.className = "jb-dot st-" + cat;
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
      const base = (u.origin === location.origin && jiraBase)
        ? jiraBase.replace(/\/+$/, "") : u.origin;
      return { href: base + "/browse/" + key, title: key, key };
    }
    if (u.origin !== location.origin) return null;      // 외부 일반 URL → og:title 조회
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
      return {
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
function mentionSuggestion(ticketKey) {
  return {
    char: "@",
    items: ({ query }) => api.mentionUsers(query, ticketKey).then((r) => r || []).catch(() => []),
    render: () => {
      let el = null, items = [], sel = 0, command = null;
      const paint = () => {
        if (!el) return;
        if (!items.length) { el.innerHTML = '<div class="mn-empty">사용자 없음</div>'; return; }
        el.innerHTML = items.map((u, i) =>
          `<div class="mn-item${i === sel ? " sel" : ""}" data-i="${i}">`
          + mnAvatar(u.name, u.id)
          + `<span class="mn-nm">${esc(u.name)}</span><span class="mn-id">${esc(u.id)}</span></div>`).join("");
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

export default {
  name: "CommentEditor",
  props: {
    ticketKey: { type: String, required: true },
    initial: { type: String, default: "" },            // 수정 시 기존 HTML
    submitLabel: { type: String, default: "등록" },
    submitFn: { type: Function, required: true },       // async (html) => any (실패 시 throw)
  },
  emits: ["submitted", "cancel"],
  data() { return { ready: false, loadErr: "", busy: false, err: "", tick: 0, languages: [],
                    maximized: false, restored: false }; },
  async mounted() {
    this._pending = new Map();        // objectURL -> { blob, name }
    this._seq = 0;
    this._jiraBase = "";              // 앱 URL(/browse/KEY) 붙여넣기를 실 Jira 주소로 바꾸는 데 사용
    api.health().then((h) => { this._jiraBase = (h && h.jiraBase) || ""; }).catch(() => { /* noop */ });
    let T;
    try { T = await loadTiptap(); }
    catch (e) { this.loadErr = "에디터를 불러오지 못했습니다(네트워크/CDN 차단). 잠시 후 다시 시도."; return; }
    if (this._dead) return;
    ensureHljsTheme(document.documentElement.getAttribute("data-theme") === "dark");   // 구문강조 색 CSS
    this.languages = T.languages || [];
    const self = this;
    // 붙여넣기/드롭 이미지 → objectURL 삽입 + 추적(제출 시 업로드)
    const handleFiles = (files, view) => {
      let any = false;
      for (const f of files) {
        if (!f.type || !f.type.startsWith("image/")) continue;
        any = true;
        const ext = (f.type.split("/")[1] || "png").replace("jpeg", "jpg");
        const name = "paste-" + Date.now() + "-" + (++self._seq) + "." + ext;
        const url = URL.createObjectURL(f);
        self._pending.set(url, { blob: f, name });
        self._ed.chain().focus().setImage({ src: url, alt: name }).run();
        self.applyFitWidth(url);                 // 세로가 너무 길면 기본 상한으로 축소
      }
      return any;
    };
    this._ed = new T.Editor({
      element: this.$refs.ed,
      extensions: [
        T.StarterKit.configure({ codeBlock: false }),   // 아래 CodeBlockLowlight 로 교체(구문강조)
        // 코드블럭 — 원래 Jira 와 같은 태그(<pre class="jecodeblock"><code class="language-X">) + lowlight 강조
        T.CodeBlockLowlight.configure({ lowlight: T.lowlight, HTMLAttributes: { class: "jecodeblock" } }),
        calloutExt(T),
        singleLineHeadingExt(T),
        firstBlockEscapeExt(T),
        T.Mention.configure({ HTMLAttributes: { class: "mention" }, suggestion: mentionSuggestion(this.ticketKey) }),
        T.Table.configure({ resizable: true }), T.TableRow, T.TableHeader, T.TableCell,
        // inline:true — 이미지가 같은 줄에 글자와 나란히 놓이게(TipTap 기본은 블록이라 줄이 갈린다)
        imageResizeExt(T).configure({ inline: true }), linkBadgeExt(T),
        T.Placeholder.configure({ placeholder: "댓글을 입력하세요. '/' 없이 바로 마크다운(#, -, ``` )·@멘션 사용" }),
      ],
      content: this.initial || "",
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
            const norm = normalizeAppUrl(url, self._jiraBase);     // 우리 앱 URL 이면 정규화
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
  methods: {
    active(name, attrs) { this.tick; return this._ed && this._ed.isActive(name, attrs); },
    cmd(fn) { if (this._ed) { fn(this._ed.chain().focus()); this._ed.commands.focus(); } },
    tbBold() { this.cmd((c) => c.toggleBold().run()); },
    tbItalic() { this.cmd((c) => c.toggleItalic().run()); },
    tbStrike() { this.cmd((c) => c.toggleStrike().run()); },
    tbCode() { this.cmd((c) => c.toggleCode().run()); },
    tbH(l) { this.cmd((c) => c.toggleHeading({ level: l }).run()); },
    tbBullet() { this.cmd((c) => c.toggleBulletList().run()); },
    tbOrdered() { this.cmd((c) => c.toggleOrderedList().run()); },
    tbQuote() { this.cmd((c) => c.toggleBlockquote().run()); },
    tbCodeBlock() { this.cmd((c) => c.toggleCodeBlock().run()); },
    tbTable() { this.cmd((c) => c.insertTable({ rows: 2, cols: 2, withHeaderRow: true }).run()); },
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
    inCallout(t) { this.tick; return !!(this._ed && this._ed.isActive("callout", { type: t })); },
    tbCallout(t) { this.cmd((c) => c.toggleCallout(t).run()); },
    toggleMax() { this.maximized = !this.maximized; },
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
      const files = e.target.files;
      if (files && files.length && this._ed) {
        for (const f of files) {
          if (!f.type || !f.type.startsWith("image/")) continue;
          const ext = (f.type.split("/")[1] || "png").replace("jpeg", "jpg");
          const name = "paste-" + Date.now() + "-" + (++this._seq) + "." + ext;
          const url = URL.createObjectURL(f);
          this._pending.set(url, { blob: f, name });
          this._ed.chain().focus().setImage({ src: url, alt: name }).run();
          this.applyFitWidth(url);
        }
      }
      e.target.value = "";
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
    draftKey() { return this.initial ? null : "new:" + this.ticketKey; },
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
      const text = (this._ed.getText() || "").trim();
      // 이미지/링크 뱃지만 있는 댓글도 유효한 내용이다(텍스트가 비어도 통과).
      const hasNode = /<img\b/i.test(html) || /<a\b/i.test(html);
      if (!text && !hasNode) { this.err = "내용을 입력하세요."; return; }
      this.busy = true; this.err = "";
      const uploaded = [];
      try {
        for (const [url, info] of this._pending) {
          if (!html.includes(url)) continue;            // 지운 이미지는 업로드 안 함
          const file = new File([info.blob], info.name, { type: info.blob.type || "image/png" });
          const res = await api.attachmentUpload(this.ticketKey, file);
          uploaded.push(res.id);
          html = html.split(url).join(res.filename);    // objectURL → 실제 파일명
        }
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
  <div class="cmt-editor" :class="{ maximized }">
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
        <button type="button" class="tb-b" :class="{on:active('heading',{level:1})}" @click="tbH(1)" title="제목1">H1</button>
        <button type="button" class="tb-b" :class="{on:active('heading',{level:2})}" @click="tbH(2)" title="제목2">H2</button>
        <button type="button" class="tb-b" :class="{on:active('heading',{level:3})}" @click="tbH(3)" title="제목3">H3</button>
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
        <button type="button" class="tb-b" @click="tbImage" title="이미지">🖼</button>
        <button type="button" class="tb-b" style="margin-left:auto" @click="toggleMax"
                :title="maximized ? '최대화 해제' : '에디터 최대화'">{{ maximized ? '🗗' : '🗖' }}</button>
        <input ref="file" type="file" accept="image/*" multiple style="display:none" @change="onFile">
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
      <div ref="ed" class="cmt-ed-host"></div>
      <div class="cmt-ed-bar">
        <span v-if="err" class="cmt-ed-msg">{{ err }}</span>
        <button class="cmt-ed-btn ghost" :disabled="busy" @click="$emit('cancel')">취소</button>
        <button class="cmt-ed-btn primary" :disabled="busy || !ready" @click="submit">
          {{ busy ? '저장 중…' : submitLabel }}</button>
      </div>
    </template>
  </div>`,
};
