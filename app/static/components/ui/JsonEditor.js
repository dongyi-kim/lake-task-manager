// JsonEditor.js — 줄번호 + 구문강조 + **오류 줄 표시**가 있는 JSON 입력 팔레트.
//
// 왜 만드나: 맨 textarea 는 수십 줄짜리 JSON 을 다루기엔 너무 헐겁다. 오류가 "3번 항목의
// duedate" 라고만 나오면 사용자는 그 항목을 눈으로 세어 찾아야 한다. 줄번호와 오류 줄 표시가
// 있으면 **어디를 고쳐야 하는지 바로 보인다**.
//
// 구조 — 겹쳐 놓은 두 겹:
//   [줄번호 거터] [ <pre> 강조본(보이는 것) + <textarea> 투명(입력받는 것) ]
// 둘의 글꼴·줄높이·여백이 **완전히 같아야** 글자가 어긋나지 않는다(아래 CSS 가 한 곳에서 정한다).
//
// ★ 스크롤 주인은 **바깥 상자 하나뿐이다.** 처음엔 textarea 가 스크롤하고 강조본을 JS 로
//   따라가게 했는데, scroll 이벤트는 그리기가 끝난 **뒤에** 오므로 드래그로 자동 스크롤할 때
//   글자와 선택 영역이 한 프레임씩 어긋나 보였다(리포트된 버그).
//   → 강조본을 흐름에 두어 그 크기가 상자를 정하게 하고, textarea 를 그 위에 정확히 덮는다.
//     둘 다 자기 스크롤이 없으니 **같은 좌표계**에서 함께 밀린다 — 어긋날 여지가 없다.
//     거터는 sticky 로 왼쪽에 붙어 세로로만 같이 움직인다(이것도 JS 없이).
//
// 색은 **이미 앱에 있는 hljs 테마**(vendor/hljs/github*.css)의 클래스명을 그대로 쓴다 —
// 여기서 색을 새로 정하면 같은 앱 안에서 코드 색이 두 벌이 된다.
// 강조는 줄 단위로 한다: JSON 문자열 리터럴은 실제 개행을 담을 수 없으므로(개행은 \n 두 글자)
// 줄마다 따로 토큰화해도 안전하고, 입력 도중의 깨진 따옴표가 아랫줄까지 번지지 않는다.
import { ensureHljsTheme } from "../../lib/hljs.js";

const ESC = { "&": "&amp;", "<": "&lt;", ">": "&gt;" };
const esc = (s) => s.replace(/[&<>]/g, (c) => ESC[c]);

// 문자열(뒤에 ':' 가 오면 키) · 주석 · 참/거짓/널 · 숫자.
// **문자열을 먼저** 둔다 — 그래야 문자열 안의 `//` 가 주석으로 잘리지 않는다(순서가 규칙이다).
const TOK = /("(?:\\.|[^"\\])*")(\s*:)?|(\/\/[^\n]*)|\b(true|false|null)\b|(-?\d+(?:\.\d+)?(?:[eE][+-]?\d+)?)/g;

function highlightLine(src) {
  let out = "", last = 0, m;
  TOK.lastIndex = 0;
  while ((m = TOK.exec(src))) {
    out += esc(src.slice(last, m.index));
    if (m[1] !== undefined) {
      out += m[2]
        ? '<span class="hljs-attr">' + esc(m[1]) + "</span>" + esc(m[2])
        : '<span class="hljs-string">' + esc(m[1]) + "</span>";
    } else if (m[3] !== undefined) {
      out += '<span class="hljs-comment">' + esc(m[3]) + "</span>";
    } else if (m[4] !== undefined) {
      out += '<span class="hljs-literal">' + esc(m[4]) + "</span>";
    } else {
      out += '<span class="hljs-number">' + esc(m[5]) + "</span>";
    }
    last = TOK.lastIndex;
  }
  return out + esc(src.slice(last));
}

export default {
  name: "JsonEditor",
  props: {
    modelValue: { type: String, default: "" },
    // 붉게 표시할 줄번호(1-based). 검증 오류가 가리키는 자리.
    badLines: { type: Array, default: () => [] },
    placeholder: { type: String, default: "" },
  },
  emits: ["update:modelValue"],
  computed: {
    lines() { return String(this.modelValue == null ? "" : this.modelValue).split("\n"); },
    // 빈 줄도 높이를 차지해야 거터와 어긋나지 않는다 → 공백 한 칸을 넣는다.
    rows() { return this.lines.map((l) => highlightLine(l) || "&nbsp;"); },
    badSet() { return new Set(this.badLines || []); },
  },
  mounted() {
    ensureHljsTheme(document.documentElement.getAttribute("data-theme") === "dark");
    // 테마를 바꾸면 코드 색도 따라가야 한다.
    this._obs = new MutationObserver(() => {
      ensureHljsTheme(document.documentElement.getAttribute("data-theme") === "dark");
    });
    this._obs.observe(document.documentElement, { attributes: true, attributeFilter: ["data-theme"] });
  },
  unmounted() { if (this._obs) this._obs.disconnect(); },
  methods: {
    onInput(e) { this.$emit("update:modelValue", e.target.value); },
    /** Tab 으로 창을 빠져나가지 않고 두 칸 들여쓴다 — JSON 을 손보는 창이다. */
    onTab(e) {
      e.preventDefault();
      const t = e.target, s = t.selectionStart, v = t.value;
      const next = v.slice(0, s) + "  " + v.slice(t.selectionEnd);
      this.$emit("update:modelValue", next);
      this.$nextTick(() => { t.selectionStart = t.selectionEnd = s + 2; });
    },
    /** 그 줄이 보이게 스크롤한다(오류를 눌렀을 때 부모가 부른다). 스크롤 주인은 바깥 상자다. */
    revealLine(n) {
      const box = this.$refs.box, ta = this.$refs.ta;
      if (!box || !ta || !n) return;
      const lh = parseFloat(getComputedStyle(ta).lineHeight) || 19;
      box.scrollTop = Math.max(0, (n - 3) * lh);
    },
  },
  template: `
  <div class="jed" ref="box">
    <div class="jed-inner">
      <div class="jed-gut" aria-hidden="true">
        <div v-for="(l, i) in lines" :key="i" class="jed-gl" :class="{ bad: badSet.has(i + 1) }">{{ i + 1 }}</div>
      </div>
      <div class="jed-code">
        <pre class="jed-hl" aria-hidden="true"><div v-for="(r, i) in rows" :key="i"
             class="jed-row" :class="{ bad: badSet.has(i + 1) }" v-html="r"></div></pre>
        <textarea ref="ta" class="jed-ta" spellcheck="false" :placeholder="placeholder"
                  :value="modelValue" @input="onInput" @keydown.tab="onTab"></textarea>
      </div>
    </div>
  </div>`,
};
