import { confluenceFailure } from '../../lib/confluenceRead.js';

export default {
  props: { error: [Object,String], loading: Boolean, slow: Boolean, retrying: Boolean, cached: Boolean, url: String },
  emits: ['retry'],
  computed: { failure() { return confluenceFailure(this.error); } },
  template: `<section v-if="loading && (slow || retrying) || error" class="conf-read-notice" :class="{'is-error': !!error && !loading}"
    :role="error && !loading ? 'alert' : 'status'" aria-live="polite">
    <span class="conf-notice-icon" aria-hidden="true">{{ loading ? '↻' : '!' }}</span>
    <div class="conf-notice-content"><strong>{{ loading ? (retrying ? '문서를 다시 불러오는 중…' : 'Confluence 응답을 기다리고 있습니다') : failure.title }}</strong>
      <p>{{ loading ? '다른 문서를 탐색하거나 다른 화면으로 이동할 수 있습니다.' : failure.detail }}</p>
      <p v-if="cached">이전에 불러온 내용을 표시하고 있습니다.</p>
      <div v-if="!loading" class="conf-notice-actions"><button v-if="failure.retry" class="btn primary" @click="$emit('retry')">문서 다시 불러오기</button>
        <a v-if="url" class="btn" :href="url" target="_blank" rel="noopener noreferrer">Confluence에서 열기</a></div>
    </div>
  </section>`,
};
