// FloatingRefresh.js — 좌하단 플로팅 '강제 새로고침' 버튼.
//
// 새로고침 버튼이 탭마다 제각각(어디는 ↻ 둘, 어디는 인라인 '강제 새로고침')이라 무엇이
// 캐시를 비우는 것인지 헷갈렸다. **하나로 통일**한다 — 화면 어디에 있든 좌하단 같은 자리,
// 같은 아이콘. 누르면 지금 보고 있는 화면이 캐시를 비우고 처음부터 다시 받는다.
//
// 각 뷰를 직접 알지 않는다(뷰는 keep-alive 로 갈아 끼워진다). 전역 이벤트로 부탁하고,
// 지금 화면의 뷰가 자기 hardRefresh 를 돌린 뒤 끝났다고 알린다. 아무도 못 받으면(그런 화면이면)
// 8초 뒤 스스로 멈춘다 — 영영 도는 아이콘은 '멈춘 것' 으로 읽힌다.
export default {
  name: "FloatingRefresh",
  data() { return { busy: false }; },
  mounted() {
    window.addEventListener("force-refresh-done", this._done = () => { this.busy = false; });
  },
  unmounted() {
    window.removeEventListener("force-refresh-done", this._done);
    clearTimeout(this._t);
  },
  methods: {
    go() {
      if (this.busy) return;
      this.busy = true;
      window.dispatchEvent(new CustomEvent("force-refresh"));
      clearTimeout(this._t);
      this._t = setTimeout(() => { this.busy = false; }, 8000);   // 아무도 안 받으면 스스로 멈춘다
    },
  },
  template: `
  <button class="fab-refresh" :class="{ busy }" @click="go"
          :title="busy ? '다시 받는 중…' : '강제 새로고침 (캐시 비우고 처음부터)'"
          aria-label="강제 새로고침">
    <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.2"
         stroke-linecap="round" stroke-linejoin="round">
      <path d="M21 12a9 9 0 1 1-2.64-6.36"/><path d="M21 4v5h-5"/>
    </svg>
  </button>`,
};
