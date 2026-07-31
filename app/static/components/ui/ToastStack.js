// ToastStack.js — 우하단에 쌓였다 시간이 지나면 사라지는 알림 스택(mac 알림 느낌).
//
// 여기로 모이는 것: 인증 이벤트(auth-ok) · 다운로드 알림(lake-download) · 임의 알림(lake-toast).
// 오른쪽에서 슬라이드+페이드로 들어오고, timeout 뒤 스스로 나간다. hover 하면 잠시 멈춘다.
// 너무 많이 쌓이면 오래된 것부터 정리한다.

import { api } from "../../lib/api.js";

let _seq = 0;

export default {
  name: "ToastStack",
  data() { return { toasts: [] }; },
  mounted() {
    window.addEventListener("lake-toast", this._onToast = (e) => this.add((e && e.detail) || {}));
    // 다운로드 알림(run.py 가 앱 창에 쏜다) → 토스트로. 앱 창엔 브라우저 다운로드 표시줄이 없다.
    window.addEventListener("lake-download", this._onDl = (e) => {
      const d = (e && e.detail) || {};
      // reveal: 경로를 주면 '폴더 열기' 버튼이 붙는다. 경로만 적어 두면 그걸 읽고 탐색기를
      // 직접 여는 건 결국 사용자 몫이라, 받은 자리에서 바로 열게 한다.
      if (d.ok) this.add({ kind: "success", icon: "⬇", title: d.name || "다운로드 완료",
                           message: d.path, reveal: d.path, timeout: 9000 });
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
                     message: t.message || "", key: t.key || null,
                     reveal: t.reveal || "", revealErr: "", _t: null };
      this.toasts.push(item);
      // 기본 표시시간 — 6초는 읽다가 사라진다는 말이 나왔다. 10초로 늘리고, 사라질 때도
      // 천천히 페이드아웃한다(아래 CSS .toast-leave-active).
      const ms = t.timeout == null ? 10000 : t.timeout;
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
    /** 받은 파일을 탐색기에서 연다(그 파일이 선택된 채로). 실패하면 알림 안에서 사유를 말한다 —
     *  알림이 사라져 버리면 왜 안 열렸는지 물어볼 데가 없다. */
    async reveal(t) {
      clearTimeout(t._t);                       // 여는 동안 알림이 사라지면 결과를 못 본다
      t.revealErr = "";
      try {
        await api.raw("/api/app/reveal", { method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ path: t.reveal }) });
        this.dismiss(t.id);
      } catch (e) {
        t.revealErr = (e && e.message) || "폴더를 열지 못했습니다.";
      }
    },
    pause(t) { clearTimeout(t._t); },
    resume(t) { clearTimeout(t._t); t._t = setTimeout(() => this.dismiss(t.id), 4000); },
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
          <!-- 받은 파일 — 경로만 적어 두면 탐색기는 결국 사용자가 직접 열어야 한다. -->
          <button v-if="t.reveal" class="toast-act" @click="reveal(t)">📂 폴더 열기</button>
          <span v-if="t.revealErr" class="toast-m err">{{ t.revealErr }}</span>
        </span>
        <button class="toast-x" @click="dismiss(t.id)" title="닫기" aria-label="닫기">×</button>
      </div>
    </transition-group>
  </div>`,
};
