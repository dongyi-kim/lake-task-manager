// ProgressBar.js — 재사용 누적 막대. 세그먼트(색·값)만. 기준선은 상위(워크로드 오버레이)에서 처리.
// scale=100% 기준값. total>scale 이면 비율 유지하며 꽉 참(denom=max). updated: 2026-07-09
export default {
  name: "ProgressBar",
  props: {
    segments: { type: Array, default: () => [] },   // [{ value, color, title }]
    scale: { type: Number, default: 1 },
    height: { type: Number, default: 22 },
    darkText: Boolean,                               // 세그먼트 숫자 어둡게(부드러운 색용)
    showTotal: Boolean,                              // 막대 왼쪽에 total 숫자
  },
  computed: {
    total() { return this.segments.reduce((a, s) => a + (s.value || 0), 0); },
    denom() { return Math.max(this.total, this.scale, 1); },
  },
  template: `
    <div class="pbar-wrap">
      <div v-if="showTotal" class="pbar-total">{{ total }}</div>
      <div class="pbar" :style="{ height: height + 'px' }">
        <template v-if="total > 0">
          <div v-for="(s, i) in segments" v-show="s.value > 0" :key="i" class="pbar-seg"
               :style="{ width: (s.value / denom * 100) + '%', background: s.color, color: darkText ? '#14243a' : '#fff' }"
               :title="s.title">
            <span v-if="s.hatchFrac" class="pbar-hatch" :style="{ width: (s.hatchFrac * 100) + '%' }"></span>
            <span class="pbar-lbl">{{ s.label != null ? s.label : s.value }}</span>
          </div>
        </template>
        <div v-else class="pbar-zero">0</div>
      </div>
    </div>`,
};
