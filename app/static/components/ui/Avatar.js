// Avatar.js — 사용자 프로필 이미지.
// /api/avatar/{id} 는 인증 세션으로 받아 same-origin 반환(브라우저 캐시 30일).
// 아바타가 없거나(404) 불러오기 실패하면 **기본 아바타**(시그니처 컬러 배경 + 이름 첫 글자)로 폴백한다.
// 시그니처 컬러는 댓글 좌측 구분 바와 같은 색이라, 프사가 없어도 사람이 색으로 구분된다.
// 아바타가 없는(404) 사용자 — 세션 동안 기억해 매 렌더마다 재요청하지 않는다.
// (탭 전환·다이얼로그 재오픈 때마다 인원수만큼 404 가 나가는 낭비 방지)
import { sigColor, initialOf } from "../../lib/colors.js";

const MISSING = new Set();

export default {
  name: "Avatar",
  props: {
    user: String,                       // Jira username (표시명 아님)
    name: String,                       // 툴팁/alt 용 본명
    size: { type: Number, default: 20 },
  },
  data() { return { failed: !this.user || MISSING.has(this.user) }; },
  watch: { user(v) { this.failed = !v || MISSING.has(v); } },
  computed: {
    src() { return this.user ? "/api/avatar/" + encodeURIComponent(this.user) : ""; },
    box() {
      return { width: this.size + "px", height: this.size + "px",
               fontSize: Math.round(this.size * 0.62) + "px" };
    },
    // 기본 아바타 — 시그니처 배경 + 이니셜. 두 글자면 원 안에 들어오게 폰트를 줄인다(크고 볼드하되 넘치지 않게).
    inibox() {
      const two = (this.initial || "").length >= 2;
      return { width: this.size + "px", height: this.size + "px",
               fontSize: Math.round(this.size * (two ? 0.42 : 0.54)) + "px",
               background: sigColor(this.user || this.name) };
    },
    initial() { return initialOf(this.name, this.user); },
    label() { return this.name || this.user || ""; },
  },
  methods: {
    onError() { if (this.user) MISSING.add(this.user); this.failed = true; },
  },
  template: `
    <img v-if="user && !failed" class="avt" :src="src" :style="box" :alt="label" :title="label"
         loading="lazy" @error="onError">
    <span v-else-if="user || name" class="avt avt-ini" :style="inibox" :title="label">{{ initial }}</span>
    <span v-else class="avt avt-df" :style="box" aria-hidden="true"></span>`,
};
