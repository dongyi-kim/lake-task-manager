// All requests rejected by the same expired session join one silent renewal. A caller may
// stop waiting without cancelling renewal needed by another tab/view/request.
export function waitForAuth(promise, signal) {
  if (!signal) return promise;
  return new Promise((resolve, reject) => {
    const abort = () => reject(signal.reason || new DOMException("Aborted", "AbortError"));
    if (signal.aborted) { abort(); return; }
    signal.addEventListener("abort", abort, { once: true });
    promise.then(resolve, reject).finally(() => signal.removeEventListener("abort", abort));
  });
}

export function createAuthRecovery({ probe, loginRequired, recovered, now = Date.now }) {
  let generation = 0, inFlight = null, latest = null, finishedAt = 0, notified = false;
  return {
    get generation() { return generation; },
    authenticated() { generation++; latest = { ok: true }; notified = false; },
    requireLogin() {
      if (notified) return;
      notified = true;
      loginRequired();
    },
    recover(observedGeneration, signal) {
      if (signal?.aborted) return waitForAuth(Promise.resolve(), signal);
      if (!inFlight && latest && (observedGeneration < generation
          || (!latest.ok && now() - finishedAt < 5000))) {
        return waitForAuth(Promise.resolve(latest), signal);
      }
      if (!inFlight) {
        const started = generation;
        inFlight = Promise.resolve().then(probe).catch(error => ({ ok: false,
          needLogin: !!error?.needLogin, mode: error?.needLogin ? "authenticating" : "degraded" }))
          .then(result => {
            // A visible login may finish while an older silent probe is still returning.
            if (generation !== started && latest?.ok) result = latest;
            latest = result || { ok: false, mode: "degraded" };
            generation++; finishedAt = now();
            if (latest.ok) { notified = false; recovered(); }
            else if (latest.needLogin) this.requireLogin();
            return latest;
          }).finally(() => { inFlight = null; });
      }
      return waitForAuth(inFlight, signal);
    },
  };
}
