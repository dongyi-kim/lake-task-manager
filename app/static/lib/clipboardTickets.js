// Clipboard contents stay in this page's memory. Only recognized Jira keys reach the UI.
export function clipboardTicketKeys(text, jiraBase) {
  let base;
  try { base = new URL(jiraBase); } catch (_) { return []; }
  if (!/^https?:$/.test(base.protocol)) return [];
  const root = base.pathname.replace(/\/+$/, ""), keys = new Set();
  for (const raw of String(text || "").match(/https?:\/\/[^\s<>"`]+/gi) || []) {
    try {
      const url = new URL(raw.replace(/[),.;\]}']+$/, ""));
      if (url.origin !== base.origin || url.username || url.password) continue;
      const path = decodeURIComponent(url.pathname);
      if (!path.startsWith(root + "/")) continue;
      const tail = path.slice(root.length);
      const browse = /^\/browse\/([A-Z][A-Z0-9_]*-\d+)\/?$/i.exec(tail);
      const key = browse ? browse[1] : url.searchParams.get("selectedIssue");
      if (key && /^[A-Z][A-Z0-9_]*-\d+$/i.test(key)) keys.add(key.toUpperCase());
    } catch (_) { /* Malformed or unrelated clipboard text is not an application error. */ }
  }
  return Array.from(keys);
}

/** A single focus-scoped reader. Dismissal lasts until the clipboard value changes. */
export function watchClipboardTickets({ jiraBase, onKeys, isOpen = () => false }, options = {}) {
  const win = options.window || window, doc = options.document || document;
  const nav = win.navigator, clipboard = nav.clipboard;
  const hasChangeEvent = !!clipboard && "onclipboardchange" in clipboard;
  let disposed = false, denied = false, busy = false, queued = false, epoch = 0;
  let timer = null, permission = null, lastText = null, keys = [], dismissed = false;
  const active = () => !disposed && !denied && doc.visibilityState !== "hidden" && doc.hasFocus();
  const publish = () => onKeys(active() && !dismissed ? keys.filter((key) => !isOpen(key)) : []);
  const cancelTimer = () => { if (timer !== null) win.clearTimeout(timer); timer = null; };
  const schedule = () => {
    cancelTimer();
    if (active() && !hasChangeEvent && clipboard?.readText) timer = win.setTimeout(check, 2000);
  };
  const check = async () => {
    cancelTimer();
    if (!active() || !clipboard?.readText) return;
    if (busy) { queued = true; return; }
    busy = true;
    const started = epoch;
    try {
      const text = await clipboard.readText();
      if (!active() || started !== epoch) return;
      if (text !== lastText) {
        lastText = text;
        keys = clipboardTicketKeys(text, jiraBase);
        dismissed = false;
      }
      publish();
    } catch (error) {
      // Losing focus during read is not a denial. Never repeatedly ask after a real rejection.
      if (active() && started === epoch && ["NotAllowedError", "SecurityError"].includes(error?.name)) {
        denied = true;
        publish();
      }
    } finally {
      busy = false;
      if (queued && active()) { queued = false; check(); }
      else { queued = false; schedule(); }
    }
  };
  const suspend = () => { epoch++; cancelTimer(); onKeys([]); };
  const visibility = () => { if (doc.visibilityState === "hidden") suspend(); else check(); };
  const permissionChanged = () => {
    if (disposed) return;
    if (permission.state === "denied") denied = true;
    else if (permission.state === "granted") denied = false;
    if (denied) suspend();
    else if (permission.state === "granted") check();
  };
  win.addEventListener("focus", check);
  win.addEventListener("blur", suspend);
  doc.addEventListener("visibilitychange", visibility);
  if (hasChangeEvent) clipboard.addEventListener("clipboardchange", check);
  Promise.resolve().then(() => nav.permissions?.query({ name: "clipboard-read" }))
    .catch(() => null).then((result) => {
      if (disposed) return;
      permission = result;
      if (permission) {
        if (permission.state === "denied") denied = true;
        permission.addEventListener("change", permissionChanged);
      }
      check();
    });
  return {
    refresh: publish,
    dismiss() { dismissed = true; publish(); },
    stop() {
      disposed = true;
      suspend();
      lastText = null; keys = [];
      win.removeEventListener("focus", check);
      win.removeEventListener("blur", suspend);
      doc.removeEventListener("visibilitychange", visibility);
      if (hasChangeEvent) clipboard.removeEventListener("clipboardchange", check);
      if (permission) permission.removeEventListener("change", permissionChanged);
    },
  };
}
