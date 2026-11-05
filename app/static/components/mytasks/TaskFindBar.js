export default {
  name: "TaskFindBar",
  props: { query: String, index: Number, total: Number, loading: Boolean },
  emits: ["query", "next", "close"],
  mounted() { this.focus(); },
  methods: { focus() { this.$refs.input.focus(); this.$refs.input.select(); } },
  template: `
    <div class="mt-find" role="search" aria-label="Tasks 카드 찾기">
      <input ref="input" :value="query" @input="$emit('query', $event.target.value)"
             @keydown.enter.prevent="$emit('next', $event.shiftKey ? -1 : 1)"
             @keydown.esc.stop.prevent="$emit('close')" aria-label="카드에서 찾기"
             placeholder="카드에서 찾기" autocomplete="off" />
      <span class="mt-find-count" aria-live="polite">{{ total ? index + 1 : 0 }} / {{ total }}</span>
      <button type="button" :disabled="!total" @click="$emit('next', -1)" aria-label="이전 결과">↑</button>
      <button type="button" :disabled="!total" @click="$emit('next', 1)" aria-label="다음 결과">↓</button>
      <button type="button" @click="$emit('close')" aria-label="카드 찾기 닫기">×</button>
      <span v-if="loading" class="mt-find-loading">불러오는 티켓이 결과에 추가됩니다</span>
    </div>`,
};
