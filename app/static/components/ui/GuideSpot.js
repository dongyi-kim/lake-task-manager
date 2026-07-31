// GuideSpot.js — 기능 안내 말풍선(코치마크) 하나를 띄운다. 목록은 lib/guides.js 가 갖는다.
//
// 설계 원칙
//  · **화면을 막지 않는다.** 가리킬 요소에 테두리만 두르고 옆에 말풍선을 놓는다. 뒤의 화면은
//    그대로 쓸 수 있다 — 안내 때문에 일을 못 하게 되는 것이 안내가 없는 것보다 나쁘다.
//  · **가리킬 요소가 없으면 안 뜬다.** 기능이 사라지면 안내도 저절로 조용해진다.
//  · 한 번에 하나. 닫으면 '봤다' 로 기록하고 다음 것을 찾는다.
//  · 요소는 화면이 데이터를 받은 뒤 늦게 나타나기도 한다 → 잠깐 기다렸다가 다시 찾는다.
import { nextGuide, markSeen, pruneSeen } from "../../lib/guides.js";

const SETTLE_MS = 900;        // 화면이 자리를 잡을 때까지(첫 렌더 직후의 흔들림을 피한다)
const RETRY_MS = 700;         // 요소가 아직 없을 때 다시 찾아보는 간격
const MAX_TRIES = 8;          // 그래도 없으면 이 화면에선 포기(다음 진입에 다시 본다)

export default {
  name: "GuideSpot",
  props: { route: { type: String, default: "" } },
  // g=지금 띄운 안내, box=가리킬 요소의 위치, pos=말풍선의 **확정** 위치(실측 후에 정해진다)
  data() { return { g: null, box: null, pos: null }; },
  mounted() {
    pruneSeen();                                  // 지워진 안내의 기록 청소
    this._reflow = () => this.measure();
    window.addEventListener("resize", this._reflow);
    window.addEventListener("scroll", this._reflow, true);
    this.schedule();
  },
  unmounted() {
    window.removeEventListener("resize", this._reflow);
    window.removeEventListener("scroll", this._reflow, true);
    this.stop();
  },
  watch: {
    // 탭을 옮기면 지금 안내는 접고(아직 '봤다' 로 치지 않는다) 그 화면의 것을 다시 찾는다.
    route() { this.g = null; this.schedule(); },
  },
  methods: {
    stop() { clearTimeout(this._t); clearInterval(this._iv); },
    schedule() {
      this.stop();
      this._tries = 0;
      this._t = setTimeout(() => {
        this._iv = setInterval(() => {
          if (this.g) return this.stop();
          if (++this._tries > MAX_TRIES) return this.stop();
          this.find();
        }, RETRY_MS);
        this.find();
      }, SETTLE_MS);
    },
    find() {
      const g = nextGuide(this.route);
      if (!g) return;
      this.g = g;
      this.$nextTick(() => this.measure());
      this.stop();
    },
    measure() {
      if (!this.g) return;
      const el = document.querySelector(this.g.anchor);
      if (!el) { this.g = null; this.box = null; this.pos = null; return; }  // 사라졌다 — 조용히 접는다
      const r = el.getBoundingClientRect();
      this.box = { top: r.top, left: r.left, width: r.width, height: r.height };
      this.$nextTick(() => this.place());
    },
    close() {
      if (this.g) markSeen(this.g.id);
      this.g = null; this.box = null; this.pos = null;
      this.schedule();                                        // 이 화면에 또 있으면 이어서
    },
    /**
     * 말풍선 자리를 정한다 — **실제로 그려진 크기를 재고 나서.**
     *
     * 처음엔 세로 중앙정렬(translateY(-50%))로 대충 놓았는데, 이 안내가 가리키는 새로고침
     * 버튼은 화면 **좌하단**에 있어 말풍선 절반이 화면 아래로 잘렸다. 높이를 모르는 채로
     * 가두면 이런 자리에서 반드시 샌다 → 그려진 뒤 rect 를 재서 네 변을 다 가둔다.
     *
     * 자리가 밀려도 꼬리는 **여전히 버튼을 가리켜야 한다** — 안내가 무엇을 말하는지는 꼬리가
     * 정한다. 그래서 꼬리 위치(--ax/--ay)를 앵커 중심에 맞춰 따로 준다.
     */
    place() {
      const b = this.box, el = this.$refs.bub;
      if (!b || !el) return;
      const M = 12, GAP = 14;                     // 화면 여백 · 앵커와의 간격
      const rect = el.getBoundingClientRect();
      const W = rect.width, H = rect.height;
      const place = (this.g && this.g.place) || "right";
      const cx = b.left + b.width / 2, cy = b.top + b.height / 2;

      let left, top;
      if (place === "right")      { left = b.left + b.width + GAP; top = cy - H / 2; }
      else if (place === "left")  { left = b.left - W - GAP;       top = cy - H / 2; }
      else if (place === "top")   { left = cx - W / 2;             top = b.top - H - GAP; }
      else                        { left = cx - W / 2;             top = b.top + b.height + GAP; }

      // 네 변 모두 화면 안으로. (여백을 뺀 자리가 음수가 되는 아주 좁은 화면에서도 위/왼쪽이 이긴다.)
      left = Math.max(M, Math.min(left, Math.max(M, window.innerWidth - W - M)));
      top = Math.max(M, Math.min(top, Math.max(M, window.innerHeight - H - M)));

      // 꼬리는 앵커 중심을 향하되, 말풍선 모서리에 붙지 않게 안쪽으로 가둔다.
      const ay = Math.max(14, Math.min(cy - top, H - 14));
      const ax = Math.max(14, Math.min(cx - left, W - 14));
      this.pos = { left: left + "px", top: top + "px", "--ay": ay + "px", "--ax": ax + "px" };
    },
    ringStyle() {
      const b = this.box; if (!b) return {};
      const pad = 6;
      return { left: (b.left - pad) + "px", top: (b.top - pad) + "px",
               width: (b.width + pad * 2) + "px", height: (b.height + pad * 2) + "px" };
    },
  },
  template: `
  <div v-if="g && box" class="guide-layer">
    <div class="guide-ring" :style="ringStyle()"></div>
    <!-- pos 가 정해지기 전(=크기를 재기 전) 한 프레임은 화면 밖에 둔다 — 잘못된 자리에서
         제자리로 튀는 것이 보이면 안내가 아니라 잡음이다. -->
    <div ref="bub" class="guide-bub" :class="'pl-' + (g.place || 'right')"
         :style="pos || { left: '-9999px', top: '0px' }" role="dialog">
      <div class="guide-t">{{ g.title }}</div>
      <div class="guide-b">{{ g.body }}</div>
      <button class="guide-x" @click="close">알겠습니다</button>
    </div>
  </div>`,
};
