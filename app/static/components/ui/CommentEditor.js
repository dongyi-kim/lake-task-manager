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
import { pushToast } from "../../lib/toast.js";
import { agentApi } from "../../lib/agentApi.js";
import { beginBusy } from "../../lib/uibusy.js";
import AgentSettingsDialog from "./AgentSettingsDialog.js";
import { liftCheckboxes, liftSections, normalizeAiHtml } from "../editor/contentTransforms.js";
import {
  EDITOR_HEIGHT_KEY, EDITOR_HEIGHT_MIN, EDITOR_HEIGHT_MAX,
  loadEditorHeight, saveEditorHeight, validEditorHeight,
} from "../editor/editorHeight.js";
import { escapeHtml as esc } from "../editor/editorHtml.js";
import {
  UPLOAD_TRIES, sleep, stripPendingRef, fitWidth, fmtSize,
} from "../editor/editorFiles.js";
import {
  jiraBase, normalizeAppUrl, confTitleFromUrl, ticketLabel,
  openBadgeEditor, updateBadgeTitle,
} from "../editor/linkBadges.js";
import {
  createEditorExtensions, STYLES, FONTS, COLORS, BGCOLORS,
} from "../editor/editorExtensions.js";
import COMMENT_EDITOR_TEMPLATE from "../editor/commentEditorTemplate.js";

export { normalizeAiHtml } from "../editor/contentTransforms.js";

const URL_RE = /^https?:\/\/\S+$/i;

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
    heightKey: { type: String, default: EDITOR_HEIGHT_KEY },
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
      extensions: createEditorExtensions(T, {
        host: this,
        sections: this.sections,
        ticketKey: this.ticketKey,
        mentionUsers: this.mentionUsers,
        placeholder: this.placeholder,
      }),
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
          if (txt && URL_RE.test(txt.trim()) && self._ed.state.selection.empty) {
            const url = txt.trim();
            const norm = normalizeAppUrl(url);                     // 우리 앱 URL 이면 정규화
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
      this.cmd((ch) => (c ? ch.setColor(c) : ch.unsetColor()).run());
    },
    /** 배경색(형광펜) — 빈 값이면 해제. */
    setFontBg(c) {
      this.bgOpen = false;
      this.cmd((ch) => (c ? ch.setBackgroundColor(c) : ch.unsetBackgroundColor()).run());
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
        const h = Math.max(EDITOR_HEIGHT_MIN, Math.min(EDITOR_HEIGHT_MAX, Math.round(startH + delta)));
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
      const jiraOrigin = await jiraBase();
      if (jiraOrigin) {
        const port = location.port ? ":" + location.port : "";
        const re = new RegExp("https?://(?:localhost|127\\.0\\.0\\.1|\\[::1\\])" + port
                              + "(/browse/[A-Za-z][A-Za-z0-9]*-\\d+)", "g");
        html = html.replace(re, jiraOrigin.replace(/\/+$/, "") + "$1");
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
  template: COMMENT_EDITOR_TEMPLATE,
};
