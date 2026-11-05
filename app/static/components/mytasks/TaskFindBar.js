export default {
  name: "TaskFindBar",
  props: { query: String, index: Number, total: Number, loading: Boolean },
  emits: ["query", "next", "close"],
  mounted() { this.focus(); },
  methods: { focus() { this.$refs.input.focus(); this.$refs.input.select(); } },
  template: `
    <div class="mt-find-dock">
    <div class="mt-find" role="search" aria-label="Tasks 카드 찾기">
      <svg class="mt-find-icon" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" aria-hidden="true">
        <circle cx="10.5" cy="10.5" r="6.5"/><path d="m16 16 4.5 4.5"/>
      </svg>
      <input ref="input" :value="query" @input="$emit('query', $event.target.value)"
             @keydown.enter.prevent="$emit('next', $event.shiftKey ? -1 : 1)"
             @keydown.esc.stop.prevent="$emit('close')" aria-label="카드에서 찾기"
             placeholder="키 · 제목 · 담당자 · Epic 찾기" autocomplete="off" spellcheck="false" />
      <span class="mt-find-count" :class="{ empty: query.trim() && !total }" aria-live="polite">{{ total ? index + 1 : 0 }} / {{ total }}</span>
      <div class="mt-find-actions">
        <button type="button" :disabled="!total" @click="$emit('next', -1)" aria-label="이전 결과" title="이전 결과 (Shift+Enter)">
          <svg viewBox="0 0 24 24" aria-hidden="true"><path d="m6 14 6-6 6 6"/></svg>
        </button>
        <button type="button" :disabled="!total" @click="$emit('next', 1)" aria-label="다음 결과" title="다음 결과 (Enter)">
          <svg viewBox="0 0 24 24" aria-hidden="true"><path d="m6 10 6 6 6-6"/></svg>
        </button>
        <button type="button" @click="$emit('close')" aria-label="카드 찾기 닫기" title="찾기 닫기 (Esc)">
          <svg viewBox="0 0 24 24" aria-hidden="true"><path d="m6 6 12 12M18 6 6 18"/></svg>
        </button>
      </div>
      <span v-if="loading || (query.trim() && !total)" class="mt-find-status" role="status">
        <template v-if="query.trim() && !total">일치하는 티켓이 없습니다<span v-if="loading"> · </span></template>
        <span v-if="loading" class="mt-find-loading">불러오는 티켓이 결과에 추가됩니다</span>
      </span>
    </div>
    </div>`,
};
