import { api } from '../../lib/api.js';

const FIELDS = [
  ['metadataDisplaySeconds', 'Epic 이름·프로필 사진 보존 기간', '일', 86400, 365, 30],
  ['metadataFreshnessSeconds', 'Epic 이름·프로필 사진 갱신 간격', '시간', 3600, 720, 24],
  ['confluenceBodyRetentionSeconds', 'Confluence 버전별 원본 보관 기간', '일', 86400, 365, 30],
  ['displaySeconds', '화면 표시 보존 기간', '시간', 3600, 720, 24],
  ['freshnessSeconds', '일반 데이터 갱신 간격', '분', 60, 1440, 10],
  ['resolvedFreshnessSeconds', '완료 티켓 갱신 간격', '시간', 3600, 720, 24],
  ['resolvedCommentsFreshnessSeconds', '완료 티켓 댓글 갱신 간격', '분', 60, 1440, 60],
];
export default {
  name: 'CacheSettingsDialog', emits: ['close'],
  data: () => ({ fields: FIELDS, values: {}, loading: true, busy: false, error: '', saved: '' }),
  mounted() {
    api.prefs().then(p => { for (const [key, , , unit] of FIELDS) this.values[key] = p.cachePolicy[key] / unit; })
      .catch(e => { this.error = e.message; }).finally(() => { this.loading = false; });
  },
  methods: {
    defaults() { for (const [key, , , , , value] of FIELDS) this.values[key] = value; this.saved = ''; },
    async save() {
      this.error = ''; this.saved = '';
      const policy = {};
      for (const [key, label, , unit, max] of FIELDS) {
        const n = Number(this.values[key]);
        if (!Number.isInteger(n) || n < 1 || n > max) { this.error = `${label}: 1~${max}의 정수를 입력하세요.`; return; }
        policy[key] = n * unit;
      }
      if (policy.displaySeconds < Math.max(policy.freshnessSeconds, policy.resolvedFreshnessSeconds,
          policy.resolvedCommentsFreshnessSeconds)) { this.error = '화면 표시 보존 기간은 모든 갱신 간격 이상이어야 합니다.'; return; }
      if (policy.metadataDisplaySeconds < policy.metadataFreshnessSeconds) {
        this.error = 'Epic 이름·프로필 사진 보존 기간은 갱신 간격 이상이어야 합니다.'; return;
      }
      this.busy = true;
      try { await api.setPrefs({ cachePolicy: policy }); this.saved = '저장했습니다. 현재 화면부터 적용됩니다.'; }
      catch (e) { this.error = e.message; }
      finally { this.busy = false; }
    },
  },
  template: `<teleport to="body"><div class="ag-back" @click.self="$emit('close')">
    <section class="ag-dlg" role="dialog" aria-modal="true" aria-labelledby="cache-settings-title"
             style="width: min(480px, 94vw); padding: 24px" @keydown.esc="$emit('close')">
      <h2 id="cache-settings-title">캐시 설정</h2>
      <p>저장된 내용을 먼저 표시하고, 갱신 간격이 지난 데이터는 뒤에서 확인합니다.</p>
      <p v-if="loading">설정 불러오는 중…</p>
      <form v-else @submit.prevent="save">
        <label v-for="f in fields" :key="f[0]" style="display:flex; align-items:center; gap:8px; margin:16px 0">
          <span style="flex:1">{{ f[1] }}</span>
          <input type="number" min="1" :max="f[4]" step="1" required v-model.number="values[f[0]]"
                 :aria-label="f[1]" style="width:90px" /> {{ f[2] }}
        </label>
        <p v-if="error" role="alert" style="color:var(--red,#c0392b)">{{ error }}</p>
        <p v-if="saved" role="status">{{ saved }}</p>
        <div style="display:flex;gap:8px;justify-content:flex-end">
          <button type="button" class="btn" @click="defaults" :disabled="busy">기본값 복원</button>
          <button type="button" class="btn" @click="$emit('close')">닫기</button>
          <button type="submit" class="btn primary" :disabled="busy">{{ busy ? '저장 중…' : '저장' }}</button>
        </div>
      </form>
    </section></div></teleport>`,
};
