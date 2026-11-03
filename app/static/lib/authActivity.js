// 앱을 계속 사용하는 중에도 Jira 인증을 확인한다. 이벤트 핸들러는 네트워크를 기다리지 않으며,
// 실제 확인/무음 재인증은 백엔드가 single-flight로 수행한다.
export const AUTH_INTERVAL_MS = 4 * 60 * 1000;
export const AUTH_COOLDOWN_MS = 60 * 1000;

/** 앱 전역에 한 번 설치한다. 반환 함수는 모든 listener와 예약 작업을 해제한다. */
export function installAuthActivityProbe(api, options) {
  const opt = options || {};
  const win = opt.window || window;
  const doc = opt.document || document;
  const now = opt.now || (() => Date.now());
  const intervalMs = Number(opt.intervalMs) || AUTH_INTERVAL_MS;
  const cooldownMs = Number(opt.cooldownMs) || AUTH_COOLDOWN_MS;
  let lastProbeAt = -Infinity, inFlight = null, disposed = false;

  const probe = () => {
    const at = now();
    if (disposed || doc.visibilityState === "hidden" || win.navigator?.onLine === false
        || inFlight || at - lastProbeAt < cooldownMs) return inFlight;
    lastProbeAt = at;
    inFlight = Promise.resolve().then(() => api.authProbe()).then((result) => {
      if (disposed || !result) return;
      if (result.recovered) win.dispatchEvent(new CustomEvent("auth-ok"));
      else if (result.needLogin) win.dispatchEvent(new CustomEvent("need-login"));
    }).catch(() => {
      // 오프라인/서버 일시 정지는 인증 만료가 아니다. 실제 API 요청과 다음 활동이 재시도한다.
    }).finally(() => { inFlight = null; });
    return inFlight;
  };

  const activity = () => {
    if (now() - lastProbeAt >= intervalMs) probe();
  };
  const visible = () => { if (doc.visibilityState !== "hidden") probe(); };

  win.addEventListener("focus", visible, { passive: true });
  win.addEventListener("online", visible, { passive: true });
  doc.addEventListener("visibilitychange", visible, { passive: true });
  doc.addEventListener("pointerdown", activity, { passive: true, capture: true });
  doc.addEventListener("keydown", activity, { passive: true, capture: true });
  probe();

  return () => {
    if (disposed) return;
    disposed = true;
    win.removeEventListener("focus", visible);
    win.removeEventListener("online", visible);
    doc.removeEventListener("visibilitychange", visible);
    doc.removeEventListener("pointerdown", activity, true);
    doc.removeEventListener("keydown", activity, true);
  };
}
