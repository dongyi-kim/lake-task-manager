import { api } from "../../lib/api.js";
import { CONFLUENCE_MACROS, macroDefinition, macroDefaults } from "../../lib/confluenceMacros.js";
export default {
  props: { initial: Object, editing: Boolean }, emits: ["apply", "close"],
  data() { return { name: this.initial.name || "expand", config: { ...JSON.parse(JSON.stringify(this.initial.config || {})), params: { ...macroDefaults(this.initial.name || "expand"), ...this.initial.config?.params } },
    query: "", results: [], searching: false, error: "", next: null, definitions: CONFLUENCE_MACROS }; },
  computed: { definition() { return macroDefinition(this.name); }, fields() { return this.definition?.fields || []; } },
  mounted() { this._previousFocus = document.activeElement; this.config.params = { ...macroDefaults(this.name), ...this.config.params }; this.$nextTick(() => this.$el.querySelector('button')?.focus()); },
  beforeUnmount() { this._seq = (this._seq || 0) + 1; clearTimeout(this._timer); this._previousFocus?.focus(); },
  methods: {
    trap(event) {
      const items = [...this.$el.querySelectorAll('button:not(:disabled),input:not(:disabled),select:not(:disabled)')];
      if (!items.length) return;
      if (event.shiftKey && document.activeElement === items[0]) { event.preventDefault(); items.at(-1).focus(); }
      else if (!event.shiftKey && document.activeElement === items.at(-1)) { event.preventDefault(); items[0].focus(); }
    },
    change() { this.config = { params: macroDefaults(this.name) }; this.results = []; this.query = ""; },
    schedule() { clearTimeout(this._timer); this._seq = (this._seq || 0) + 1; this.results = []; this.searching = !!this.query.trim(); this._timer = setTimeout(() => this.search(), 250); },
    async search(more = false) {
      const seq = this._seq = (this._seq || 0) + 1; this.error = "";
      if (!this.query.trim()) { this.searching = false; return; }
      this.searching = true;
      try { const data = await api.confluenceSearch(this.query, "", more ? this.next : 0);
        if (seq !== this._seq) return;
        const rows = (data.results || []).map(row => row.content || row);
        this.results = more ? [...this.results, ...rows] : rows;
        this.next = data._links?.next ? Number(data.start || 0) + Number(data.size ?? rows.length) : null;
      } catch (error) { if (seq === this._seq) this.error = error.message; }
      finally { if (seq === this._seq) this.searching = false; }
    },
    pick(row) { this.config.reference = { id: String(row.id), title: row.title, space: row.space?.key }; this.results = []; this.query = ""; },
    apply() {
      if (!this.definition) { this.$emit("close"); return; }
      if (Object.hasOwn(this.definition, "page") && !this.definition.optionalPage && !this.config.reference && !this.config.params[this.definition.page]?.trim()) { this.error = "포함할 문서를 검색해서 선택하세요."; return; }
      for (const field of this.fields) if (field.type === "number") {
        const value = Number(this.config.params[field.key]);
        if (!Number.isInteger(value) || value < field.min || value > field.max) { this.error = field.label + `: ${field.min}~${field.max} 정수를 입력하세요.`; return; }
      }
      if (this.config.params.minLevel && +this.config.params.minLevel > +this.config.params.maxLevel) { this.error = "시작 단계는 마지막 단계 이하여야 합니다."; return; }
      if (this.name === "anchor" && !this.config.params[""]?.trim()) { this.error = "이동 지점 이름을 입력하세요."; return; }
      this.$emit("apply", { name: this.name, config: JSON.parse(JSON.stringify(this.config)) });
    },
  },
  template: `<div class="conf-macro-overlay" @keydown.tab="trap" @keydown.esc.stop.prevent="$emit('close')"><section role="dialog" aria-modal="true" aria-label="Confluence 매크로 설정" class="conf-macro-dialog">
    <header><h3>매크로 설정</h3><button type="button" class="btn" @click="$emit('close')" aria-label="매크로 설정 닫기">✕</button></header>
    <label>종류<select ref="first" v-model="name" :disabled="editing" @change="change"><option v-for="item in definitions" :value="item.name">{{item.label}} · {{item.name}}</option><option v-if="!definition" :value="name">{{name}}</option></select></label>
    <p v-if="!definition">이 매크로는 원본 설정과 내용을 보존합니다. Confluence에서 수정하세요.</p>
    <template v-if="definition"><label v-for="field in fields" :key="field.key">{{field.label}}
      <select v-if="field.type === 'select'" v-model="config.params[field.key]"><option v-for="value in field.options" :value="value">{{value}}</option></select>
      <select v-else-if="field.type === 'boolean'" v-model="config.params[field.key]"><option value="true">켜기</option><option value="false">끄기</option></select>
      <input v-else v-model="config.params[field.key]" :type="field.type" :min="field.min" :max="field.max" maxlength="255">
    </label>
    <div v-if="Object.hasOwn(definition,'page')" class="conf-macro-page"><label>참조 문서<input v-model="query" @input="schedule" placeholder="문서 제목 검색"></label>
      <p v-if="config.reference || config.params[definition.page] && definition.page !== 'root'">{{config.reference ? (config.reference.space || '') + ' · ' + config.reference.title : config.params[definition.page]}} <button class="btn" @click="config.reference=null; delete config.params[definition.page]">선택 해제</button></p>
      <p v-else-if="definition.optionalPage" class="conf-muted">문서를 선택하지 않으면 현재 문서와 시작 위치 설정을 사용합니다.</p>
      <p v-if="searching" role="status">문서 검색 중…</p><button v-for="row in results" class="conf-macro-result" @click="pick(row)">{{row.title}} <small>{{row.space?.key}}</small></button>
      <button v-if="next !== null && results.length" class="btn" :disabled="searching" @click="search(true)">검색 결과 더 보기</button>
    </div></template><p v-if="error" role="alert" class="conf-error">{{error}}</p>
    <footer><button class="btn" @click="$emit('close')">취소</button><button v-if="definition" class="btn primary" @click="apply">{{editing ? '적용' : '삽입'}}</button></footer>
  </section></div>`,
};
