// MarkdownTableDialog.js — 마크다운 표를 붙여넣어 미리보기 후 **진짜 표**로 삽입한다.
//
// 사람들은 표를 이미 마크다운으로 들고 있는 경우가 많다(문서·GPT·깃허브). 그걸 셀 하나하나
// 다시 치게 하지 않는다. 붙여넣으면 즉시 파싱해 보여 주고, '삽입' 하면 에디터의 표 노드로 넣는다
// (그래야 이후 편집·정렬·행추가가 된다 — 텍스트로 넣으면 그냥 글자다).
//
// 정렬 마커(`:---`, `:--:`, `---:`)도 읽어 각 열의 정렬로 반영한다.
export default {
  name: "MarkdownTableDialog",
  props: { sample: { type: String, default: "" } },
  emits: ["close", "insert"],
  data() {
    return {
      text: this.sample || "| 항목 | 값 |\n| --- | --- |\n| 이름 | 홍길동 |\n| 상태 | 진행중 |",
    };
  },
  computed: {
    /** 파싱 결과 {header:[…], rows:[[…]], align:[…]} 또는 null(표가 아님). */
    parsed() { return parseMarkdownTable(this.text); },
    ok() { return !!(this.parsed && this.parsed.header.length); },
  },
  mounted() {
    this.$nextTick(() => { const el = this.$refs.ta; if (el) { el.focus(); el.select(); } });
    document.addEventListener("keydown", this._onKey = (e) => {
      if (e.key === "Escape") { e.stopPropagation(); this.$emit("close"); }
      // Ctrl/Cmd+Enter = 삽입
      else if ((e.metaKey || e.ctrlKey) && e.key === "Enter" && this.ok) { e.preventDefault(); this.doInsert(); }
    }, true);
  },
  unmounted() { document.removeEventListener("keydown", this._onKey, true); },
  methods: {
    doInsert() { if (this.ok) this.$emit("insert", this.parsed); },
  },
  template: `
  <Teleport to="body">
  <div class="mdt-ov" @click.self="$emit('close')">
  <div class="mdt" @click.stop>
    <div class="mdt-h">마크다운 표 삽입
      <span class="mdt-h-s">표를 붙여넣으면 미리보기 후 삽입됩니다</span>
      <button class="lp-x" @click="$emit('close')" aria-label="닫기">✕</button>
    </div>
    <div class="mdt-body">
      <textarea ref="ta" v-model="text" class="mdt-src" spellcheck="false"
                placeholder="| 헤더1 | 헤더2 |
| --- | --- |
| 값 | 값 |"></textarea>
      <div class="mdt-prev">
        <div class="mdt-prev-h">미리보기</div>
        <div v-if="ok" class="mdt-prev-box">
          <table class="mdt-table">
            <thead><tr><th v-for="(h, i) in parsed.header" :key="i"
              :style="{ textAlign: parsed.align[i] || 'left' }">{{ h }}</th></tr></thead>
            <tbody><tr v-for="(row, r) in parsed.rows" :key="r">
              <td v-for="(c, i) in row" :key="i"
                  :style="{ textAlign: parsed.align[i] || 'left' }">{{ c }}</td>
            </tr></tbody>
          </table>
        </div>
        <div v-else class="mdt-prev-none">마크다운 표를 붙여넣으세요. (첫 줄 헤더 · 둘째 줄 <code>| --- |</code>)</div>
      </div>
    </div>
    <div class="mdt-f">
      <span class="mdt-hint">정렬 마커도 읽습니다: <code>:--</code> 왼쪽 · <code>:-:</code> 가운데 · <code>--:</code> 오른쪽</span>
      <button class="cmt-ed-btn ghost" @click="$emit('close')">취소</button>
      <button class="cmt-ed-btn primary" :disabled="!ok" @click="doInsert">삽입</button>
    </div>
  </div>
  </div>
  </Teleport>`,
};

/** 마크다운 표 파서 — 헤더/구분선/본문. 실패하면 null. */
export function parseMarkdownTable(src) {
  const lines = String(src || "").replace(/\r/g, "").split("\n").map((l) => l.trim()).filter(Boolean);
  if (lines.length < 2) return null;
  const cells = (l) => {
    let s = l.trim();
    if (s.startsWith("|")) s = s.slice(1);
    if (s.endsWith("|")) s = s.slice(0, -1);
    // 이스케이프된 파이프(\|)는 셀 구분이 아니다
    return s.split(/(?<!\\)\|/).map((c) => c.replace(/\\\|/g, "|").trim());
  };
  const isSep = (l) => /^\s*\|?\s*:?-{1,}:?\s*(\|\s*:?-{1,}:?\s*)*\|?\s*$/.test(l);
  // 구분선 줄을 찾는다 — 보통 둘째 줄
  let sepIdx = -1;
  for (let i = 1; i < Math.min(lines.length, 3); i++) { if (isSep(lines[i])) { sepIdx = i; break; } }
  if (sepIdx < 1) return null;
  const header = cells(lines[sepIdx - 1]);
  const align = cells(lines[sepIdx]).map((s) => {
    const l = s.startsWith(":"), r = s.endsWith(":");
    return l && r ? "center" : r ? "right" : l ? "left" : "";
  });
  const rows = [];
  for (let i = sepIdx + 1; i < lines.length; i++) {
    if (isSep(lines[i])) continue;
    const c = cells(lines[i]);
    // 열 수를 헤더에 맞춘다(모자라면 채우고 넘치면 자른다)
    while (c.length < header.length) c.push("");
    rows.push(c.slice(0, header.length));
  }
  if (!header.length) return null;
  return { header, align, rows };
}
