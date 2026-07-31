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
  data() { return { g: null, box: null }; },     // g=지금 띄운 안내, box=가리킬 요소의 위치
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
      if (!el) { this.g = null; this.box = null; return; }   // 도중에 사라졌다 — 조용히 접는다
      const r = el.getBoundingClientRect();
      this.box = { top: r.top, left: r.left, width: r.width, height: r.height };
    },
    close() {
      if (this.g) markSeen(this.g.id);
      this.g = null; this.box = null;
      this.schedule();                                        // 이 화면에 또 있으면 이어서
    },
    /** 말풍선 위치 — 가리킬 요소 기준. 화면 밖으로 나가지 않게 가둔다. */
    bubbleStyle() {
      const b = this.box; if (!b) return {};
      const W = 300, gap = 14;
      const place = (this.g && this.g.place) || "right";
      let left, top;
      if (place === "right") { left = b.left + b.width + gap; top = b.top + b.height / 2; }
      else if (place === "left") { left = b.left - W - gap; top = b.top + b.height / 2; }
      else if (place === "top") { left = b.left + b.width / 2 - W / 2; top = b.top - gap; }
      else { left = b.left + b.width / 2 - W / 2; top = b.top + b.height + gap; }
      left = Math.max(12, Math.min(left, window.innerWidth - W - 12));
      top = Math.max(12, Math.min(top, window.innerHeight - 40));
      const ty = (place === "right" || place === "left") ? "-50%" : (place === "top" ? "-100%" : "0");
      return { left: left + "px", top: top + "px", width: W + "px", transform: "translateY(" + ty + ")" };
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
    <div class="guide-bub" :class="'pl-' + (g.place || 'right')" :style="bubbleStyle()" role="dialog">
      <div class="guide-t">{{ g.title }}</div>
      <div class="guide-b">{{ g.body }}</div>
      <button class="guide-x" @click="close">알겠습니다</button>
    </div>
  </div>`,
};
