// TipTap schema, node views, keyboard behavior, slash commands, and mention suggestions.
import { sigColor, initialOf } from "../../lib/colors.js";
import { createManagedMentionItems, mentionInitialUsers, rememberUser } from "../../lib/userSuggestions.js";
import { typeaheadDelay } from "../../lib/typeahead.js";
import { createManagedSuggestionRenderer } from "../../lib/suggestionPopup.js";
import { paintMentionBadge } from "../../lib/mentionBadge.js";
import { escapeHtml as esc } from "./editorHtml.js";
import { fileBadgeExt, imageResizeExt } from "./editorFiles.js";
import { linkBadgeExt } from "./linkBadges.js";

// 멘션 팝업 아바타 — 네트워크 요청(=404 스팸) 없이 이니셜 원.
// 색은 기본 아바타·댓글 구분 바와 같은 시그니처 컬러(colors.js) 를 쓴다.
function mnAvatar(name, id) {
  // 이니셜(시그니처색)을 **바탕으로 깔고** 그 위에 프로필 사진을 async 로 얹는다 — 로드되면 사진이
  // 덮고(부드럽게), 404/실패면 paint 가 img 를 지워 이니셜이 그대로 남는다. 바이트·브라우저 캐시라
  // 한 번 받은 뒤엔 즉시. (전엔 사진 없이 이니셜만 떠 '사진이 안 뜬다' 였다.)
  const img = id ? `<img class="mn-av-img" src="/api/avatar/${encodeURIComponent(id)}" alt="" loading="lazy">` : "";
  return `<span class="mn-av" style="background:${sigColor(id || name)}">${esc(initialOf(name, id))}${img}</span>`;
}
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

export const STYLES = [
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
export const FONTS = [
  { k: "default", label: "기본 글꼴", short: "가", css: "" },
  { k: "mono", label: "코딩 글꼴(고정폭)", short: "{ }",
    css: 'ui-monospace, "Cascadia Mono", Consolas, "D2Coding", monospace' },
];

// 글자색·배경색 팔레트 — 너무 많으면 고르기 어렵다. 기본(없음) + 강조 몇 개. 값은 Jira wiki
// {color:#..} 로도, html <span style> 로도 나가므로 hex 로 둔다.
export const COLORS = [
  { k: "", label: "기본색" },
  { k: "#dc2626", label: "빨강" }, { k: "#ea580c", label: "주황" }, { k: "#ca8a04", label: "노랑" },
  { k: "#16a34a", label: "초록" }, { k: "#2563eb", label: "파랑" }, { k: "#7c3aed", label: "보라" },
  { k: "#6b7280", label: "회색" },
];
export const BGCOLORS = [
  { k: "", label: "없음" },
  { k: "#fef08a", label: "노랑" }, { k: "#bbf7d0", label: "초록" }, { k: "#bfdbfe", label: "파랑" },
  { k: "#fecaca", label: "빨강" }, { k: "#e9d5ff", label: "보라" }, { k: "#e5e7eb", label: "회색" },
];

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

export function createEditorExtensions(T, {
  host,
  sections = false,
  ticketKey = "",
  mentionUsers = [],
  placeholder = "",
}) {
  return [
    // v3 StarterKit의 Link는 모든 <a>를 mark로 먼저 소비한다. Jira/문서 링크는
    // linkBadge atom으로 편집하므로 끄고, 코드블럭도 아래 Lowlight 구현으로 교체한다.
    T.StarterKit.configure({ codeBlock: false, link: false }),
    codeLineNumbers(T).configure({ lowlight: T.lowlight, HTMLAttributes: { class: "jecodeblock" } }),
    calloutExt(T),
    ...(sections ? [sectionExt(T)] : []),
    slashExt(T, host),
    fileBadgeExt(T),
    singleLineHeadingExt(T),
    firstBlockEscapeExt(T),
    mentionExt(T, ticketKey, mentionUsers),
    T.TableKit.configure({ table: { resizable: true } }),
    T.TextAlign.configure({ types: ["heading", "paragraph", "tableCell", "tableHeader"] }),
    T.TextStyleKit,
    T.TaskList,
    T.TaskItem.configure({ nested: true }),
    imageResizeExt(T).configure({ inline: true }),
    linkBadgeExt(T),
    T.Placeholder.configure({ placeholder: placeholder
      || "댓글을 입력하세요. '/' 로 표·코드·티켓 넣기, @ 로 멘션, 마크다운(#, -, ``` )" }),
  ];
}
