// draft.js — 작성 중이던 댓글(본문 HTML + 붙여넣은 이미지 blob)의 브라우저 임시저장.
// 제출 전에 취소하거나 페이지를 옮겨도 날아가지 않게 한다. 제출 성공 시 삭제.
//
// 왜 IndexedDB 인가: 이미지 blob 은 localStorage(문자열·약 5MB)에 담기 어렵다. IndexedDB 는
// Blob 을 그대로, 넉넉한 용량으로 저장한다. 오리진(localhost)별로 격리된다.
// 이미지 참조: 본문의 objectURL(blob:…)은 새로고침하면 무효 → 저장 시 draft:TOKEN 으로 바꾸고
// 복원 시 새 objectURL 을 만들어 되돌린다.
const DB_NAME = "ltm-drafts";
const STORE = "drafts";
const TTL_MS = 7 * 24 * 60 * 60 * 1000;         // 7일 지난 초안은 자동 폐기

function idb() {
  return new Promise((res, rej) => {
    let r;
    try { r = indexedDB.open(DB_NAME, 1); } catch (e) { rej(e); return; }
    r.onupgradeneeded = () => {
      const db = r.result;
      if (!db.objectStoreNames.contains(STORE)) db.createObjectStore(STORE);
    };
    r.onsuccess = () => res(r.result);
    r.onerror = () => rej(r.error);
  });
}

function op(mode, fn) {
  return idb().then((db) => new Promise((res, rej) => {
    const t = db.transaction(STORE, mode);
    const r = fn(t.objectStore(STORE));
    r.onsuccess = () => res(r.result);
    r.onerror = () => rej(r.error);
  }));
}

/** rec: { html, images: [{token, name, type, blob}] } */
export function saveDraft(key, rec) {
  const payload = Object.assign({}, rec, { savedAt: Date.now() });
  return op("readwrite", (s) => s.put(payload, key)).catch(() => null);
}

/** 없거나 TTL 초과면 null(초과분은 삭제). */
export function loadDraft(key) {
  return op("readonly", (s) => s.get(key)).then((rec) => {
    if (!rec) return null;
    if (Date.now() - (rec.savedAt || 0) > TTL_MS) { clearDraft(key); return null; }
    return rec;
  }).catch(() => null);
}

export function clearDraft(key) {
  return op("readwrite", (s) => s.delete(key)).catch(() => null);
}
