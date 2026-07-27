// ToastStack.js — 우하단에 쌓였다 시간이 지나면 사라지는 알림 스택(mac 알림 느낌).
//
// 여기로 모이는 것: 인증 이벤트(auth-ok) · 다운로드 알림(lake-download) · 임의 알림(lake-toast).
// 오른쪽에서 슬라이드+페이드로 들어오고, timeout 뒤 스스로 나간다. hover 하면 잠시 멈춘다.
// 너무 많이 쌓이면 오래된 것부터 정리한다.

let _seq = 0;

export default {
  name: "ToastStack",
  data() { return { toasts: [] }; },
  mounted() {
    window.addEventListener("lake-toast", this._onToast = (e) => this.add((e && e.detail) || {}));
    // 다운로드 알림(run.py 가 앱 창에 쏜다) → 토스트로. 앱 창엔 브라우저 다운로드 표시줄이 없다.
    window.addEventListener("lake-download", this._onDl = (e) => {
      const d = (e && e.detail) || {};
      if (d.ok) this.add({ kind: "success", icon: "⬇", title: d.name || "다운로드 완료", message: d.path, timeout: 6000 });
      else this.add({ kind: "error", icon: "⚠", title: "다운로드 실패", message: d.error, timeout: 9000 });
    });
    // 인증 성공 → 토스트. key 로 중복 방지(짧은 새 반복 발생 시 하나만).
    window.addEventListener("auth-ok", this._onAuth = () => {
      this.add({ kind: "success", icon: "🔓", title: "SSO 인증 완료",
                 message: "최신 데이터를 받아옵니다.", timeout: 4000, key: "auth-ok" });
    });
  },
  unmounted() {
    window.removeEventListener("lake-toast", this._onToast);
    window.removeEventListener("lake-download", this._onDl);
    window.removeEventListener("auth-ok", this._onAuth);
    this.toasts.forEach((t) => clearTimeout(t._t));
  },
  methods: {
    add(t) {
      if (!t || (!t.title && !t.message)) return;
      // key 가 같은 알림이 이미 있으면 그걸 치우고 새로(중복 누적 방지)
      if (t.key) { const ex = this.toasts.find((x) => x.key === t.key); if (ex) this.dismiss(ex.id); }
      const id = ++_seq;
      const item = { id, kind: t.kind || "info", icon: t.icon || "", title: t.title || "",
                     message: t.message || "", key: t.key || null, _t: null };
      this.toasts.push(item);
      const ms = t.timeout == null ? 6000 : t.timeout;
      if (ms > 0) item._t = setTimeout(() => this.dismiss(id), ms);
      // 최대 5개만 — 넘치면 가장 오래된 것부터
      while (this.toasts.length > 5) this.dismiss(this.toasts[0].id);
    },
    dismiss(id) {
      const i = this.toasts.findIndex((x) => x.id === id);
      if (i < 0) return;
      clearTimeout(this.toasts[i]._t);
      this.toasts.splice(i, 1);
    },
    pause(t) { clearTimeout(t._t); },
    resume(t) { clearTimeout(t._t); t._t = setTimeout(() => this.dismiss(t.id), 2500); },
  },
  template: `
  <div class="toaststack">
    <transition-group name="toast">
      <div v-for="t in toasts" :key="t.id" class="toast" :class="'k-' + t.kind"
           @mouseenter="pause(t)" @mouseleave="resume(t)" role="status">
        <span v-if="t.icon" class="toast-ic">{{ t.icon }}</span>
        <span class="toast-body">
          <span class="toast-t">{{ t.title }}</span>
          <span v-if="t.message" class="toast-m">{{ t.message }}</span>
        </span>
        <button class="toast-x" @click="dismiss(t.id)" title="닫기" aria-label="닫기">×</button>
      </div>
    </transition-group>
  </div>`,
};
