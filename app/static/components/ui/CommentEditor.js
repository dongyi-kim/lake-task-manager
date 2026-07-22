// CommentEditor.js — TipTap 기반 댓글 작성/수정 에디터 (모던 Confluence/Jira 스타일).
// · 마크다운 input rule: '# '·'## '·'- '·'1. '·'> '·백틱3개 실시간 변환 (StarterKit)
// · 고정 툴바: 굵게/기울임/취소선/코드 · H1~3 · 불릿/번호/인용/코드블록 · 링크/표/이미지
// · @사람 멘션: '@' 입력 → 유저 자동완성 팝업 → [~사번] 으로 저장(읽기 시 사용자 링크)
// · 링크 붙여넣기: URL 붙여넣으면 자동 링크(문서/웹 뱃지는 읽기 렌더에서 앱이 처리)
// · 이미지 붙여넣기/드롭 = 제출 시 업로드: 로컬 objectURL 미리보기 → 제출 때 첨부 업로드·롤백
// 부모는 submitFn(finalHTML) 만 넘긴다(작성/수정은 부모가 선택). 출력은 HTML(서버가 wiki 로 변환).
import { loadTiptap } from "../../lib/tiptap.js";
import { ensureHljsTheme } from "../../lib/hljs.js";
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

// 하이퍼링크를 favicon 칩(뱃지)으로 렌더 — href 로 /api/favicon URL 을 만들어 CSS 변수(--fav)로 전달.
// 저장은 그대로 [텍스트|url] (class/style 은 html_to_wiki 가 무시). favicon 이 없으면 기본 사각형.
function linkBadgeExt(T) {
  return T.Link.extend({
    renderHTML({ HTMLAttributes }) {
      const href = String(HTMLAttributes.href || "");
      const attrs = Object.assign({}, HTMLAttributes);
      if (/^https?:/i.test(href)) {
        attrs.class = (attrs.class ? attrs.class + " " : "") + "web-badge";
        attrs.style = "--fav:url('/api/favicon?u=" + encodeURIComponent(href) + "')";
      }
      return ["a", attrs, 0];
    },
  }).configure({ openOnClick: false, autolink: true, linkOnPaste: true });
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
  data() { return { ready: false, loadErr: "", busy: false, err: "", tick: 0, languages: [] }; },
  async mounted() {
    this._pending = new Map();        // objectURL -> { blob, name }
    this._seq = 0;
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
        singleLineHeadingExt(T),
        firstBlockEscapeExt(T),
        T.Mention.configure({ HTMLAttributes: { class: "mention" }, suggestion: mentionSuggestion(this.ticketKey) }),
        T.Table.configure({ resizable: true }), T.TableRow, T.TableHeader, T.TableCell,
        imageResizeExt(T), linkBadgeExt(T),
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
            self._ed.chain().focus().insertContent(
              [{ type: "text", marks: [{ type: "link", attrs: { href: url } }], text: url },
               { type: "text", text: " " }]).run();
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
    });
    if (this._dead) { try { this._ed.destroy(); } catch (e) { /* noop */ } return; }
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
    tbLink() {
      const prev = this._ed.getAttributes("link").href || "";
      const url = window.prompt("링크 URL", prev);
      if (url === null) return;
      if (url === "") { this.cmd((c) => c.unsetLink().run()); return; }
      this.cmd((c) => c.extendMarkRange("link").setLink({ href: url }).run());
    },
    tbImage() { this.$refs.file && this.$refs.file.click(); },
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
    async submit() {
      if (this.busy || !this._ed) return;
      let html = this._ed.getHTML();
      const text = (this._ed.getText() || "").trim();
      const hasImg = /<img\b/i.test(html);
      if (!text && !hasImg) { this.err = "내용을 입력하세요."; return; }
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
  <div class="cmt-editor">
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
        <button type="button" class="tb-b" :class="{on:active('link')}" @click="tbLink" title="링크">🔗</button>
        <button type="button" class="tb-b" @click="tbTable" title="표 삽입">▦</button>
        <button type="button" class="tb-b" @click="tbImage" title="이미지">🖼</button>
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
